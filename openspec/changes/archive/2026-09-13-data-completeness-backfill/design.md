# Design: data-completeness-backfill

## Stack

- New SQL table `moex_holidays` populated by a one-off script.
- New `algotrader_api.data_quality.completeness` module with three
  pure functions: `find_gap_intervals`, `backfill_gaps`,
  `run_completeness_pass`.
- `algotrader_api.data_quality.health` extended with the
  `INCOMPLETE_HISTORY` sub-problem and a `_weekdays_excluding_holidays`
  helper.
- `algotrader_api.data_quality.service.run_daily_guardian` extended
  to call the completeness pass after the existing recovery pass.
- No new dependencies. No new tests framework — existing pytest.
- New tests: `tests/test_data_quality_completeness.py`.

## Layout

```
apps/api/
├── src/algotrader_api/
│   ├── data_quality/
│   │   ├── __init__.py            (UPDATE: re-export completeness)
│   │   ├── completeness.py       (NEW: find_gap_intervals, backfill_gaps, run_completeness_pass)
│   │   ├── health.py             (UPDATE: INCOMPLETE_HISTORY, holiday-aware expected count)
│   │   └── service.py            (UPDATE: run_daily_guardian calls completeness after recover_stale)
│   └── db/migrations/
│       └── 006_moex_holidays.sql  (NEW)
├── scripts/
│   ├── import_moex_holidays.py    (NEW: load JSON into moex_holidays)
│   └── data/
│       └── moex_holidays.json     (NEW: static calendar 2020..2027)
└── tests/
    └── test_data_quality_completeness.py   (NEW)
```

## Data flow

### Daily guardian extended run

1. `discover_universe(client)` → upsert — **unchanged**.
2. `compute_all(db_path)` — **updated**:
   - `_weekdays_between(first_bar, today)` now uses
     `moex_holidays` to subtract non-trading days.
   - New `INCOMPLETE_HISTORY` issue when
     `actual < expected * 0.95`.
3. `recover_stale(db_path, runner, reports)` — **unchanged**.
4. **NEW**: `run_completeness_pass(db_path, client, runner, reports)`:
   - Filter reports to those with `INCOMPLETE_HISTORY`.
   - Skip those with `last_run_status == 'completeness_exhausted'`.
   - For each figi:
     - `gaps = find_gap_intervals(db, figi, min_gap_days=5)`.
     - `bars_added = backfill_gaps(client, figi, gaps, db)` (one
       broker call per gap, sequential).
     - If `bars_added == 0` and `len(gaps) > 0`, mark
       `completeness_exhausted`.
   - Return `CompletenessSummary(figis_examined, gaps_found,
bars_added, exhausted)`.
5. **Pipeline row** with the combined summary.

### Gap detection algorithm

For each figi:

```
SELECT ts FROM bars WHERE figi = ? AND ts < today ORDER BY ts
```

Walk rows; for each consecutive pair `(prev, cur)`:

- Compute `(cur - prev).days`.
- Subtract holidays in range `[prev, cur]` from that gap.
- If `gap_days > min_gap_days` (default 5): emit `(prev, cur)` as
  a gap.

Returns `list[tuple[date, date]]`. Each tuple is the half-open
interval `(start_exclusive, end_inclusive)` — but for the
backfill call we send `[start, end]` inclusive. The caller
adds one day to `end` before sending (broker fetches
`[from, to]` inclusive; we already have `start` so we want
`[start + 1, end]`).

### Backfill call

```python
def backfill_gaps(client, figi, gaps, db):
    total_added = 0
    for start, end in gaps:
        # Skip the start day — we already have a bar for it.
        from_date = (start + timedelta(days=1)).isoformat()
        to_date = end.isoformat()
        candles = await client.get_candles(
            figi=figi, date_from=from_date, date_to=to_date,
            interval="CANDLE_INTERVAL_DAY",
        )
        # Filter to closed bars (today may be in the range).
        today = date.today()
        closed = [c for c in candles if _candle_date(c) < today]
        added = replace_bars_for_figi(
            db, figi, [_to_dict(c) for c in closed],
            replace=True,  # overwrite any partial bars in this range
        )
        total_added += added
    return total_added
```

### Exhaustion logic

```python
def run_completeness_pass(db, client, runner, reports):
    exhausted_count = 0
    bars_added = 0
    gaps_found = 0
    for figi, report in reports.items():
        if HealthIssue.INCOMPLETE_HISTORY not in report.issues:
            continue
        if _is_completeness_exhausted(db, figi):
            continue
        gaps = find_gap_intervals(db, figi, min_gap_days=5)
        gaps_found += len(gaps)
        added = backfill_gaps(client, figi, gaps, db)
        bars_added += added
        if gaps and added == 0:
            _mark_completeness_exhausted(db, figi)
            exhausted_count += 1
    return CompletenessSummary(
        figis_examined=..., gaps_found=gaps_found,
        bars_added=bars_added, exhausted=exhausted_count,
    )
```

### `INCOMPLETE_HISTORY` health check

```python
expected_bars = _weekdays_between(first_bar, today, holiday_dates)
if expected_bars > 0 and actual_bars < expected_bars * 0.95:
    issues.append(HealthIssue.INCOMPLETE_HISTORY)
    penalty += 25
```

`_weekdays_between(start, end, holidays)` returns
`weekdays_in_range - holidays_in_range[start, end]`. Holidays
come from `SELECT date FROM moex_holidays` in one query.

### `moex_holidays` table

```sql
CREATE TABLE moex_holidays (
    date TEXT PRIMARY KEY,  -- ISO 'YYYY-MM-DD'
    name TEXT NOT NULL      -- 'New Year', 'Victory Day', etc.
);
CREATE INDEX idx_moex_holidays_date ON moex_holidays(date);
```

## Testing

- `test_data_quality_completeness.py`:
  - `test_find_gap_intervals_returns_empty_for_complete_history`
  - `test_find_gap_intervals_returns_gap_for_5_day_hole`
  - `test_find_gap_intervals_ignores_weekends`
  - `test_find_gap_intervals_handles_dst_and_long_holiday`
  - `test_backfill_gaps_writes_only_in_gap_range`
  - `test_backfill_gaps_does_not_overwrite_existing_bars_outside_range`
  - `test_completeness_pass_marks_exhausted_when_no_bars_added`
  - `test_completeness_pass_skips_exhausted_figis`
  - `test_health_report_marks_incomplete_history_with_holidays`
- Updates to `test_data_quality_health.py`:
  - `test_health_report_incomplete_history_with_holidays`

## Rollback

- Stop the cron. Guardian falls back to its pre-change behavior
  (no completeness pass). `INCOMPLETE_HISTORY` stops appearing in
  health reports (the enum value stays; the issue is just never
  appended).
- Remove migration `006_moex_holidays.sql` if the operator wants
  to clean up; the table is unused outside completeness.

## Operator notes

- After merging, run `python -m scripts.import_moex_holidays` once
  to populate the table. Holidays cover 2020-2027 in the bundled
  JSON.
- To refresh the calendar (next year): edit
  `scripts/data/moex_holidays.json`, re-run the import script.
- Watch the `guardian.log` for `guardian.completeness.gap_filled`
  log lines. Each one means the broker returned N bars for a
  missing interval.
