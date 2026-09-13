# data-quality-guardian

## Why

Pre-change, the operator had no way to answer the simple question
"is SBER complete from 2019 to today?". The Backfill tab showed
aggregate counters (new / stale / up_to_date / error) but those
buckets are coarse: a figi with `last_bar_ts = today` looks
"up_to_date" even when its history is half-empty.

The deeper problem is operational:

- A figi whose history has gaps in the middle (broker outage in
  March, partial response in July) shows up as `up_to_date` because
  the LAST bar is fresh. The gaps are invisible.
- A figi whose history is too short (delisted ticker with 6 months
  of data) shows up as `up_to_date` for the same reason.
- A figi that has been failing on every rate-limit retry for a week
  shows up as `error`, but only if the operator remembers to look.

The operator wants a per-ticker drill-down ("why is SBER at 70?")
and a self-healing service that keeps the database in a healthy
state without manual intervention.

## What Changes

1. **New `data_quality` package** with three modules:
   - `health.py` — `compute_health(db_path, figi) -> HealthReport`
     and `compute_all(db_path) -> dict[figi, HealthReport]`.
     Computes four sub-problems per figi plus a 0-100 health-score.
   - `recovery.py` — `recover_stale(db_path, runner, reports)`,
     queues a backfill for figis whose health-score is below 100.
   - `service.py` — `run_daily_guardian()`, the daily cron entry
     point: universe sync → health → recovery → anomalies.

2. **New `GET /api/data-quality/{symbol}` endpoint** that returns
   the drill-down payload for one ticker.

3. **`/api/admin/backfill/pending`** now uses
   `compute_health` to surface sub-problem counts alongside the
   existing new/stale/up_to_date/error buckets. The Backfill tab
   gets a "Health" column.

4. **`apps/api/worker.py`** `guardian` mode replaces the existing
   `backfill` mode. The systemd timer at 23:00 MSK runs the guardian
   instead.

5. **Canonical spec delta** in `data-fetch` adds a requirement
   that per-ticker health is computed, exposed via drill-down, and
   acted on by the daily guardian.

## Impact

- **Operator UI**: `/api/data-quality/SBER` returns
  `{ figi, health_score, issues: [...], sample_gaps: [...] }`.
  The Backfill tab gains a Health column.
- **Operational**: the daily guardian replaces the manual backfill
  loop the operator used to run. After the first run, the operator
  no longer touches `/api/admin/backfill/start`.
- **Data**: stale / sparse / gappy figis get auto-refetched on the
  next guardian run. The first run after this change ships will
  spend most of its budget healing the existing 967 error figis;
  subsequent runs stabilise.
- **No DB migration**: the health module reads from existing
  `bars`, `instruments`, `instrument_metadata`, `ingestion_logs`.
  No new tables, no new indexes (yet — the `bars(figi, ts)` PK and
  `instrument_metadata(figi)` PK are already sufficient).

## Non-Goals

- **Real-time monitoring**. The guardian runs daily. We do not add
  a 5-minute poll loop or a streaming health endpoint in this
  change. The cron rhythm is what the operator asked for.
- **Notifications / alerts**. Anomalies land in structured logs
  the operator can grep; we do not wire a Telegram / Discord /
  PagerDuty path. The operator asked for "UI / log only".
- **Intraday bars**. The health module works on day-level
  granularity. Hour / minute gaps are not in scope.
- **Broker holiday calendar**. `sparse-history` and `has-gaps`
  compare actual bar count against `today - first_bar_ts` minus
  weekends; we do not subtract MOEX holidays. The score is a
  proxy, not an exact count.
- **Self-service new ticker ingestion** (e.g. ticker appears in
  the broker for the first time, gets picked up the same day).
  The guardian adds it via `discover_universe`, but only on the
  daily cadence, not in real-time.

## Risks

- **First run is heavy**. The current 967 error figis will all be
  requeued. The guardian is designed to chunk them through the
  rate limiter so this is bounded; we run the chunk sizes past the
  existing `BackfillRunner._backfill_one` retry policy.
- **Score drift across broker holidays**. Without a holiday
  calendar, `sparse-history` will flag figis around New Year and
  Victory Day. The operator can ignore the warning; we mark the
  sub-problem with a "broker-holiday-likely" hint when
  `COUNT(bars) / expected_bars > 0.85`.
- **Genuine delisted tickers**. A figi that traded for 1 month in
  2019 will never have full history. The `sparse-history` flag is
  informational; the recovery loop will not refetch it. After
  three failed attempts the recovery loop stops re-queueing the
  figi (the `last_run_status='stale_recovery_exhausted'` state).
