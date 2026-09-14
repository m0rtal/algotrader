# Tasks: data-quality-integrity

## 1. Corporate actions table (Migration 007)

- [ ] 1.1 Add failing tests in
      `apps/api/tests/test_corporate_actions.py` for table presence,
      column types, primary key
- [ ] 1.2 Create
      `apps/api/src/algotrader_api/db/migrations/007_corporate_actions.sql`
      with the table + index
- [ ] 1.3 Confirm `pytest tests/test_corporate_actions.py` passes
- [ ] 1.4 Confirm full suite still passes (`pytest -q --tb=no`)

## 2. Corporate actions import script

- [ ] 2.1 Add failing tests in
      `apps/api/tests/test_corporate_actions.py` for idempotency,
      known-row write, operator-wrapper delegation
- [ ] 2.2 Create
      `apps/api/scripts/data/corporate_actions.json` (7 demo events:
      SBER, GAZP, LKOH, YNDX, MSFT, AAPL split; AAPL dividend)
- [ ] 2.3 Create
      `apps/api/src/algotrader_api/scripts_import/import_corporate_actions.py`
      (in-package implementation, idempotent)
- [ ] 2.4 Create `apps/api/scripts/import_corporate_actions.py`
      (operator-facing thin wrapper)
- [ ] 2.5 Confirm `tests/test_corporate_actions.py` passes

## 3. Adjusted-close view (Migration 008)

- [ ] 3.1 Add failing tests in `apps/api/tests/test_bars_adjusted.py`
      (view exists + scaled-down adj_close for pre-split bar;
      adj_close == close when no events)
- [ ] 3.2 Create
      `apps/api/src/algotrader_api/db/migrations/008_bars_adjusted.sql`
      using `EXP(SUM(LN(factor)))` for portability
- [ ] 3.3 Confirm `tests/test_bars_adjusted.py` passes

## 4. Bar integrity validator

- [ ] 4.1 Add failing tests in
      `apps/api/tests/test_data_quality_integrity.py` (7 cases:
      happy path, each rule failure, dict-or-dataclass input)
- [ ] 4.2 Create
      `apps/api/src/algotrader_api/data_quality/integrity.py`
      with `Rule`, `IntegrityViolation`, `validate_bar`
- [ ] 4.3 Update
      `apps/api/src/algotrader_api/data_quality/__init__.py`
      to re-export the new symbols
- [ ] 4.4 Confirm `tests/test_data_quality_integrity.py` passes

## 5. `BAR_CORRUPTION` health issue

- [ ] 5.1 Add `BAR_CORRUPTION` to the `HealthIssue` enum in
      `apps/api/src/algotrader_api/data_quality/health.py`; add
      `_PENALTY[BAR_CORRUPTION] = 40`
- [ ] 5.2 In `_compute_issues_and_penalty`, add a SQL count for
      figi-bars violating integrity rules; append `BAR_CORRUPTION`
      + apply `-40` when count > 0
- [ ] 5.3 Add failing test to
      `apps/api/tests/test_data_quality_health.py`:
      `test_health_report_marks_bar_corruption`
- [ ] 5.4 Confirm test passes; full health-suite passes

## 6. Ingest path: validate before write

- [ ] 6.1 In
      `apps/api/src/algotrader_api/ingestion/backfill.py`:
      call `validate_bar` on each fetched candle before
      `replace_bars_for_figi`; log skipped candles to
      `ingestion_logs` (level='warn') and only write the survivors
- [ ] 6.2 Sanitise any fixture bars in `tests/test_backfill.py` and
      `tests/test_bars_sqlite.py` that violate the new rules
- [ ] 6.3 Add focused test
      `test_backfill_skips_invalid_bars_but_writes_valid`
      (mix of valid + invalid candles; only valid land in `bars`;
      `ingestion_logs` records skipped count)
- [ ] 6.4 Confirm full suite ≥95% coverage

## 7. OpenSpec apply

- [ ] 7.1 Spec delta at `openspec/changes/data-quality-integrity/specs/data-quality/spec.md`
      (this file's contents; 2 ADDED Requirements with GIVEN/WHEN/THEN
      scenarios)
- [ ] 7.2 Run `openspec validate data-quality-integrity --strict`
      and confirm clean
- [ ] 7.3 Apply delta to `openspec/specs/data-quality/spec.md`
      (canonical copy + drop `(delta)` title suffix + keep
      `## Requirements` section structure)
- [ ] 7.4 Run `openspec validate data-quality --strict` and confirm
      clean
- [ ] 7.5 Run `openspec archive data-quality-integrity --yes --skip-specs`
- [ ] 7.6 Commit `openspec/` on `feature/data-quality-integrity`
      as `docs(spec): data-quality-integrity change`

## 8. Operator runbook

- [ ] 8.1 Add a short paragraph under an "Operator runbook" header in
      `CONTRIBUTING.md` (or `README.md`) describing
      `python -m scripts.import_corporate_actions`, when to add an
      entry to `corporate_actions.json`, and what to do when
      `BAR_CORRUPTION` shows up in the daily guardian

---

## Final validation

- [ ] `uv run pytest --cov=algotrader_api -q --tb=no` reports ≥95%
- [ ] `openspec validate data-quality --strict` passes
- [ ] No `bars` row with `volume < 0` survives in any test that runs
      `BackfillRunner._backfill_one`
