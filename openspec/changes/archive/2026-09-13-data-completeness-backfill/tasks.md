# Tasks: data-completeness-backfill

## 1. Migration + holiday calendar

- [ ] 1.1 Create `apps/api/src/algotrader_api/db/migrations/006_moex_holidays.sql`
      with `CREATE TABLE moex_holidays (date TEXT PRIMARY KEY, name TEXT)`
- [ ] 1.2 Create `apps/api/scripts/data/moex_holidays.json` with
      2020-2027 holidays (New Year block, Defender Day, International
      Women's Day, Spring/Labour Day, Victory Day, Russia Day, Unity Day).
      Roughly 60-80 entries.
- [ ] 1.3 Create `apps/api/scripts/import_moex_holidays.py` reading the
      JSON and inserting via `INSERT OR REPLACE INTO moex_holidays`.
- [ ] 1.4 Tests: `tests/test_moex_holidays.py` — file exists, import is
      idempotent, date format is ISO.

## 2. Health module: INCOMPLETE_HISTORY + holiday-aware count

- [ ] 2.1 Add `HealthIssue.INCOMPLETE_HISTORY` to `data_quality/health.py`.
- [ ] 2.2 Add `_weekdays_excluding_holidays(start, end, con) -> int`
      helper that subtracts `moex_holidays` rows.
- [ ] 2.3 Wire `expected_bars` in `compute_all` to use the new helper.
- [ ] 2.4 Wire `compute_issues_and_penalty` to append
      `INCOMPLETE_HISTORY` when `actual < expected * 0.95` (penalty -25).
- [ ] 2.5 Tests: extend `tests/test_data_quality_health.py` with
      `test_health_report_incomplete_history_with_holidays`.

## 3. Completeness module

- [ ] 3.1 Create `apps/api/src/algotrader_api/data_quality/completeness.py`
      with `find_gap_intervals(db, figi, min_gap_days=5) -> list[(date, date)]`.
      Algorithm: walk bars in order, find consecutive pairs with
      gap > min_gap_days (subtract holidays in range).
- [ ] 3.2 With `backfill_gaps(client, figi, gaps, db) -> int` that
      calls `get_candles` once per gap and writes via
      `replace_bars_for_figi(replace=True)`.
- [ ] 3.3 With `run_completeness_pass(db, client, runner, reports)
    -> CompletenessSummary`. Filter INCOMPLETE_HISTORY, skip
      exhausted, backfill each gap, mark exhausted if no bars
      returned for any non-empty gap.
- [ ] 3.4 Re-export `completeness` symbols from
      `data_quality/__init__.py`.
- [ ] 3.5 Tests: `tests/test_data_quality_completeness.py` with
      8 cases (see design).

## 4. Service integration

- [ ] 4.1 In `data_quality/service.run_daily_guardian`, after
      `recover_stale(...)`, call
      `run_completeness_pass(db_path, client, runner, reports)`.
- [ ] 4.2 Add `exhausted` and `bars_added` counts to the
      pipeline row's detail string.
- [ ] 4.3 Tests: extend `tests/test_data_quality_service.py`
      to verify completeness pass runs after recovery.

## 5. UI sub-problem surface

- [ ] 5.1 `_report_to_dict` in `routes/data_quality.py` includes
      `incomplete_history` in the issues array (uses `i.value`
      like the others).
- [ ] 5.2 `/api/admin/backfill/pending` `by_health` bucket keeps
      working — `INCOMPLETE_HISTORY` rolls into the `<50` and
      `89-50` buckets via the existing penalty logic.
- [ ] 5.3 Tests: smoke-check that the drill-down endpoint
      serializes `INCOMPLETE_HISTORY` correctly.

## 6. Spec apply + archive

- [ ] 6.1 `openspec validate data-completeness-backfill --strict`
- [ ] 6.2 Apply delta to canonical `openspec/specs/data-fetch/spec.md`
- [ ] 6.3 `openspec validate data-fetch --strict`
- [ ] 6.4 `openspec archive data-completeness-backfill --yes --skip-specs`

## 7. Live verification

- [ ] 7.1 Run `python -m scripts.import_moex_holidays` on prod DB.
      Verify `SELECT COUNT(*) FROM moex_holidays` returns ~70-80.
- [ ] 7.2 Pick one figi known to have a historical gap
      (e.g. one that was 'error' for weeks). Trigger a manual
      backfill; observe a `guardian.completeness.gap_filled` log line.
- [ ] 7.3 After one full daily run, `bars_count` should be
      slightly higher than before (a few hundred bars added).
- [ ] 7.4 `/api/data-quality/SBER` shows the issues array
      without `INCOMPLETE_HISTORY` if the history is now complete.
