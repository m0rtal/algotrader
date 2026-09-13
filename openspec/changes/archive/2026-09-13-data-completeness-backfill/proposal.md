# data-completeness-backfill

## Why

Today's guardian detects stale data (last bar older than 3 days)
and rate-limit failures, but it does not detect **historical
gaps** — a figi whose data looks healthy on the surface (recent
bars, no rate-limits) but is missing days in the middle of its
history because of broker outages, schema changes, or an early
backfill that finished before its time.

The operator wants maximum historical completeness: every tradeable
figi should have a contiguous daily series from its first available
bar to today (or to delisting). Anything less is a data quality
issue that should be detected and fixed automatically.

The pre-change state is that sparse / gappy histories are silently
acceptable. The post-change state is that the guardian runs a
second pass after the daily recovery: for every figi whose history
has long gaps, it fetches the specific missing intervals from the
broker and writes them to the SQLite `bars` table.

## What Changes

1. **New `moex_holidays` table** with the official MOEX trading
   calendar. Loaded from a one-off script
   (`scripts/import_moex_holidays.py`) that reads a static JSON
   bundled with the repo and inserts rows. The calendar is the
   source of truth for "this date is a trading day" — used to
   compute the exact number of expected bars per figi.

2. **New `HealthIssue.INCOMPLETE_HISTORY`** sub-problem in the
   health module. Penalty -25 when
   `actual_bars < (weekdays_since_first - holidays_in_range) * 0.95`.

3. **New `completeness` module** in `data_quality/`:
   - `find_gap_intervals(db, figi, min_gap_days=5) -> list[(start, end)]`
     walks `bars` in order and returns each pair of consecutive
     bars separated by more than `min_gap_days` (with a holiday
     adjustment: a 7-day gap is fine if both endpoints are around
     a long weekend).
   - `backfill_gaps(client, figi, gaps, db) -> int` calls the
     broker's `get_candles` once per gap, then writes the new
     bars via `replace_bars_for_figi` (already supports the
     `replace=True` mode).
   - `run_completeness_pass(db, client, figis) -> CompletenessSummary`
     iterates figis with `INCOMPLETE_HISTORY`, finds their gaps,
     backfills each gap, and returns counts.

4. **`run_daily_guardian` extended**: after the existing
   `recover_stale(...)` call, a new `completeness_backfill(...)`
   call runs. The two phases are independent — the second can fail
   without blocking the first.

5. **Exhaustion handling**: a figi whose completeness pass
   fetched 0 bars for every gap (broker doesn't have data for that
   range) gets `last_run_status='completeness_exhausted'`. The
   `find_gap_intervals` call skips it on subsequent passes.

6. **New sub-problem UI**: the `data_quality` report now carries
   `incomplete_history` alongside the existing four sub-problems.
   The drill-down endpoint and the Backfill tab bucket count both
   show it.

## Impact

- **Operator UI**: `/api/data-quality/SBER` may show
  `incomplete-history` with a list of the actual missing intervals
  in `recent_gaps`. The Backfill tab `by_health` buckets gain
  a new sub-bucket.
- **Daily runtime**: the second pass fetches only what is missing.
  A figi with 3 gaps (say 5 trading days each) makes 3 broker
  calls, not 1500.
- **Rate-limit budget**: 1 fetch per gap, all sequential, no
  fan-out. Worst case: a figi with 20 gaps = 20 calls. Well within
  Tinkoff sandbox limits.
- **Data integrity**: idempotent. The gap-detection walks
  `bars`, finds intervals where `next.ts - prev.ts > min_gap_days`,
  and backfills exactly those intervals. We don't touch bars
  outside the gap.

## Non-Goals

- **No IPO detection**. The broker's `share`/`bond`/`etf`
  payloads do not include a "first listing date" field. We
  backfill from the existing first bar forward, not from some
  mythical listing date.
- **No minute / hour bars**. Day-level only.
- **No delisting-date awareness**. We backfill up to `today`,
  not up to `delisted_at`. The operator can extend this later
  by adding `delisted_at` to the instruments schema.
- **No broker holiday detection from the broker itself**. We
  use a static MOEX calendar. If MOEX adds a one-off holiday
  we don't know about, the sparse check will slightly over-flag
  for that day. Acceptable for an MVP.
- **No backfill of tickers the broker no longer lists**. A
  figi that was delisted 2 years ago will have only the data the
  broker returned during the initial backfill. The exhaustion
  guard marks it after one failed pass.

## Risks

- **First run is slow**. The current 11 "error" figis plus any
  figis with historical gaps (potentially hundreds — the
  `recovery_unfilled_gap_count` from the initial backfill was
  non-zero) will all be re-fetched. The runner's existing retry
  policy (`AdaptiveRetry`) protects against transient failures.
- **Stale holiday table**. MOEX holidays change yearly. The
  operator needs to refresh `moex_holidays` once a year via
  the import script. We document this in the script header.
- **False gaps around holidays**. A long weekend (Friday +
  Saturday + Sunday + Monday = 4 calendar days) is fine. A
  4-day MOEX holiday (e.g. New Year) is 4 calendar days too,
  but no bars. If the calendar misses an entry, we'd flag it
  as INCOMPLETE_HISTORY when it's actually fine. Mitigation:
  the `min_gap_days=5` default tolerates 4-day gaps.
- **Holiday calendar file format**. We use a JSON file bundled
  in the repo. The operator can edit it without code changes.
