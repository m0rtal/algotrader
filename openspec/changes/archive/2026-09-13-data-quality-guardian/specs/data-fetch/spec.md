# data-fetch Specification (delta)

## ADDED Requirements

### Requirement: Per-ticker data quality is computed with a drill-down payload

The system SHALL compute, for every figi in `instruments` whose
class is in `TRADEABLE_CLASSES`, a `HealthReport` containing:

- `health_score: int` in `[0, 100]`
- `issues: list[HealthIssue]` (subset of `MISSING_RECENT`,
  `SPARSE_HISTORY`, `HAS_GAPS`, `RATE_LIMITED_FAILURES`)
- `first_bar: date | None`
- `last_bar: date | None`
- `actual_bars: int`
- `expected_bars: int` (weekdays since `first_bar` to `today`)
- `recent_gaps: list[date]` (up to 5 gap dates when `HAS_GAPS` is set)
- `recent_failures: list[{ts, level, message}]` (up to 5 entries
  when `RATE_LIMITED_FAILURES` is set)

The health-score formula is:

```
penalty  = (30 if MISSING_RECENT else 0)
        + (20 if SPARSE_HISTORY else 0)
        + (20 if HAS_GAPS else 0)
        + (30 if RATE_LIMITED_FAILURES else 0)
health_score = max(0, 100 - penalty)
```

#### Scenario: missing-recent-days sub-problem

- GIVEN a figi whose `MAX(bars.ts) = today - 5 days`
- WHEN `compute_health` runs
- THEN the report has `health_score <= 70`
- AND `MISSING_RECENT` is in `issues`
- AND `last_bar` is the stale date

#### Scenario: sparse-history sub-problem

- GIVEN a figi with `actual_bars / expected_bars = 0.5`
  (e.g. a delisted ticker that traded 6 months in 2019)
- WHEN `compute_health` runs
- THEN the report has `SPARSE_HISTORY` in `issues`
- AND the penalty contribution is capped at 20 (so the score
  does not drop below 80 from this issue alone)

#### Scenario: gaps sub-problem

- GIVEN a figi whose `bars` table has a gap of 7 consecutive days
  in 2024-03 (broker outage)
- WHEN `compute_health` runs
- THEN the report has `HAS_GAPS` in `issues`
- AND `recent_gaps` includes the gap dates

#### Scenario: rate-limited-failures sub-problem

- GIVEN a figi whose `ingestion_logs` has 5 `warn` rows in the
  last 7 days matching `rate%` or `RESOURCE_EXHAUSTED%`
- WHEN `compute_health` runs
- THEN the report has `RATE_LIMITED_FAILURES` in `issues`
- AND `recent_failures` includes up to 5 of those rows

#### Scenario: healthy figi

- GIVEN a figi with full day-level history, last bar = today,
  no gaps, no recent failures
- WHEN `compute_health` runs
- THEN the report has `health_score = 100` and `issues == []`

### Requirement: Per-ticker drill-down is exposed via a read endpoint

The system SHALL expose `GET /api/data-quality/{symbol}` that
returns the full `HealthReport` for the requested symbol
(ticker or figi). Returns 404 if the symbol is not in
`instruments`.

#### Scenario: drill-down for a healthy ticker

- GIVEN `SBER` is in `instruments` with `health_score = 100`
- WHEN `GET /api/data-quality/SBER`
- THEN the response is 200 with `health_score: 100` and
  `issues: []`

#### Scenario: drill-down for an unhealthy ticker

- GIVEN `OFZ` is in `instruments` with two `warn` rows in the
  last week and last bar = today - 7 days
- WHEN `GET /api/data-quality/OFZ`
- THEN the response is 200 with `health_score: 40` and
  `issues: ["MISSING_RECENT", "RATE_LIMITED_FAILURES"]`

#### Scenario: drill-down for unknown symbol

- GIVEN `XYZ` is not in `instruments`
- WHEN `GET /api/data-quality/XYZ`
- THEN the response is 404

### Requirement: A daily guardian runs universe sync + health + recovery

The system SHALL provide a cron entry point that, once per day:

1. Calls `discover_universe` (already gated to
   `TRADEABLE_CLASSES`) and `upsert_instruments` to sync the
   universe.
2. Calls `compute_all` to get one `HealthReport` per tradeable
   figi.
3. For each figi with `health_score < 100` and `last_run_status
!= 'stale_recovery_exhausted'`, enqueues the figi into the
   backfill queue. RATE_LIMITED_FAILURES-only figis are NOT
   re-queued (the live retry loop handles those).
4. Logs a structured anomaly entry for every broker-down or
   schema-mismatch condition detected during the run.
5. Appends a row to the `pipeline` table summarising the run
   (`{figis_checked, figis_recovered, anomalies_raised}`).

#### Scenario: daily guardian recovers stale figis

- GIVEN the `instrument_metadata` table has 5 figis with
  `last_bar_ts = today - 7 days`
- WHEN `run_daily_guardian` runs
- THEN those 5 figis are enqueued for backfill in the same run
- AND after the run, `instrument_metadata.last_bar_ts` is at
  most `today - 1 day` for at least 3 of them

#### Scenario: daily guardian does not re-queue exhausted figis

- GIVEN a figi has `last_run_status = 'stale_recovery_exhausted'`
  and `health_score = 50`
- WHEN `run_daily_guardian` runs
- THEN that figi is NOT enqueued for backfill
- AND a `guardian.anomaly.stale_recovery_exhausted` log line is
  emitted

#### Scenario: daily guardian does not re-queue RATE_LIMITED-only figis

- GIVEN a figi has `health_score = 70` with `RATE_LIMITED_FAILURES`
  as the only issue
- WHEN `run_daily_guardian` runs
- THEN that figi is NOT enqueued (the live retry loop handles
  rate-limit errors)
