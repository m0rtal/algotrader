# Design: data-quality-guardian

## Stack

- New Python package `algotrader_api.data_quality/`:
  - `health.py` — pure-function health computation; no I/O
    outside SQLite.
  - `recovery.py` — uses the existing `BackfillRunner` (no new
    runner) plus a small priority queue.
  - `service.py` — the orchestrator; called from `worker.py
guardian` mode.
- New FastAPI route `/api/data-quality/{symbol}` in
  `routes/data_quality.py`.
- No new Python dependencies. No DB migration. No spec for
  MSW handlers (frontend reads the same env-var contract as
  every other endpoint).
- Tests: per-module unit tests + a service-level integration
  test that runs the whole guardian against a fixture DB.

## Layout

```
apps/api/src/algotrader_api/
├── data_quality/
│   ├── __init__.py           (NEW: HealthReport, compute_health,
│   │                          compute_all, run_daily_guardian,
│   │                          HealthIssue enum)
│   ├── health.py             (NEW)
│   ├── recovery.py           (NEW)
│   └── service.py            (NEW)
├── routes/
│   ├── data_quality.py       (NEW: GET /api/data-quality/{symbol})
│   ├── backfill.py           (UPDATE: pending counts use health)
│   └── data_reads.py         (UPDATE: pending ticker row includes health_score)
├── worker.py                 (UPDATE: backfill mode → guardian mode)
└── main.py                   (UPDATE: wire data_quality.router)

apps/api/tests/
├── test_data_quality_health.py       (NEW)
├── test_data_quality_recovery.py     (NEW)
├── test_data_quality_service.py      (NEW)
└── test_data_quality_route.py        (NEW)
```

## Data flow

### Daily guardian run (`run_daily_guardian`)

1. **Universe sync** — call `ingestion.universe.discover_universe`
   (already gated to `TRADEABLE_CLASSES` per the
   `enforce-tradeable-classes-at-ingest` change) and
   `upsert_instruments`. New figis land in `instruments`;
   `instrument_metadata` rows get seeded.
2. **Health pass** — `compute_all(db_path)` returns a
   `dict[figi, HealthReport]`. Each report has:
   - `health_score: int` (0-100)
   - `issues: list[HealthIssue]` (a subset of MISSING_RECENT,
     SPARSE_HISTORY, HAS_GAPS, RATE_LIMITED_FAILURES)
   - `first_bar: date | None`
   - `last_bar: date | None`
   - `expected_bars: int` (calendar days minus weekends since
     `first_bar`)
   - `actual_bars: int`
   - `recent_gaps: list[date]` (last 5 gaps, for the drill-down)
3. **Recovery** — for each report with `health_score < 100`:
   - `MISSING_RECENT` or `HAS_GAPS` or `SPARSE_HISTORY` →
     enqueue into the existing backfill queue with priority
     proportional to `100 - health_score` (lower score → higher
     priority). Reuse `BackfillRunner.run(...)`.
   - `RATE_LIMITED_FAILURES` only → log warning, do not refetch
     (the existing retry loop is already on it).
   - `stale_recovery_exhausted` (existing
     `instrument_metadata.last_run_status`) → log anomaly
     "figi has been failing 3+ cycles; operator investigation
     needed", **do not** auto-recover.
4. **Anomalies** — log structured entries the operator can grep.
   Today: `guardian.anomaly.{broker_down,schema_mismatch,
persistent_rate_limit}`. Nothing is sent over the network.
5. **Run summary** — append a row to `pipeline` table with the
   counts (figis checked, figis recovered, anomalies raised) so
   the existing `/api/pipeline` endpoint shows what happened.

### Per-ticker drill-down (`GET /api/data-quality/{symbol}`)

```
GET /api/data-quality/SBER
{
  "figi": "BBG004730N88",
  "ticker": "SBER",
  "health_score": 100,
  "issues": [],
  "first_bar": "2019-01-15",
  "last_bar": "2026-09-12",
  "actual_bars": 1872,
  "expected_bars": 1879,
  "recent_gaps": []
}
```

If the figi has `HAS_GAPS`, the response includes
`recent_gaps` with up to 5 gap dates.

If the figi has `SPARSE_HISTORY`, the response includes
`actual_bars` vs `expected_bars` so the operator can see how thin
the history is.

If the figi has `RATE_LIMITED_FAILURES`, the response includes
`recent_failures: list[{ts, level, message}]` capped at 5.

### Backfill queue ordering

`/api/admin/backfill/pending` already returns
`{new, stale, up_to_date, error, total}`. After this change it
adds `by_health: {100: int, 99-90: int, 89-50: int, <50: int}` and
`worst: [{ticker, health_score}, ...]` (top 5 worst figis).

The `backfill_pending` route uses `compute_all` (or a cached
version refreshed on each `/api/admin/backfill/start` call) so
the operator can see whether the daily guardian has done its
work before kicking off a manual run.

## Health-score formula

```
penalty = 0
if MISSING_RECENT:                 penalty += 30
if SPARSE_HISTORY:                  penalty += 20  # capped
if HAS_GAPS:                        penalty += 20
if RATE_LIMITED_FAILURES:           penalty += 30  # any in last 7d

health_score = max(0, 100 - penalty)
```

### Sub-problem definitions

- **MISSING_RECENT** — `last_bar < today - 3 days` (allows for one
  missed day + broker weekends). Always checked first; it's the
  most common reason a figi needs refetching.
- **SPARSE_HISTORY** — `actual_bars < expected_bars × 0.9` where
  `expected_bars = weekdays_since(first_bar)`. Caps penalty at
  20 so a 6-month delisted figi doesn't drop the score by 80.
- **HAS_GAPS** — detected by a SQL gap query:
  `SELECT ts FROM bars WHERE figi = ? AND ts < today ORDER BY ts`
  and a Python pass that finds any gap > 5 days between
  consecutive rows. If at least one such gap exists in the
  history, raise `HAS_GAPS`. The drill-down returns the actual
  gap dates.
- **RATE_LIMITED_FAILURES** —
  `SELECT COUNT(*) FROM ingestion_logs WHERE figi = ? AND
 ts > datetime('now', '-7 days') AND level IN ('warn',
 'error') AND message LIKE '%rate%' OR message LIKE
 '%RESOURCE_EXHAUSTED%'`. If count > 0, raise the flag.

## Recovery loop

```python
def recover_stale(db_path, runner, reports: dict[str, HealthReport]) -> RecoverySummary:
    queued = []
    for figi, report in reports.items():
        if report.health_score == 100:
            continue
        # Don't re-queue exhausted figis.
        meta = _get_metadata(db_path, figi)
        if meta and meta["last_run_status"] == "stale_recovery_exhausted":
            continue
        # Don't re-queue RATE_LIMITED_FAILURES-only — the live retry loop handles that.
        if report.issues == [HealthIssue.RATE_LIMITED_FAILURES]:
            continue
        queued.append(figi)
    # Sort by ascending health_score so the worst get attention first.
    queued.sort(key=lambda f: reports[f].health_score)
    # Run the backfill in chunks so the rate limiter doesn't blow up.
    return runner.run_with_queue(queued, ...)
```

`BackfillRunner.run_with_queue` is a small extension to the
existing runner. It runs the existing `_backfill_one` over a
priority-ordered list with the same retry policy. We do NOT add
new runner logic; we just feed it a different `instruments`
sequence.

## Testing

- `test_data_quality_health.py` — unit tests for each
  sub-problem detector. Synthetic DB rows.
- `test_data_quality_recovery.py` — recovery loop skips
  exhausted figis, sorts by score, doesn't re-queue RATE_LIMITED-only.
- `test_data_quality_service.py` — integration: full
  `run_daily_guardian` against a fixture DB with a mix of
  healthy / stale / sparse / gappy / exhausted figis. Asserts
  the right ones get requeued and the right ones don't.
- `test_data_quality_route.py` — endpoint shape, drill-down
  payload, missing-symbol behaviour.

## Rollback

- Stop the systemd timer at 23:00 MSK. The operator can manually
  run `python -m algotrader_api.worker backfill` (the old mode)
  until the change is reverted.
- The `/api/data-quality/{symbol}` endpoint is read-only; if it
  misbehaves, deleting `routes/data_quality.py` and removing the
  router from `main.py` is enough to disable it without affecting
  the rest of the system.
