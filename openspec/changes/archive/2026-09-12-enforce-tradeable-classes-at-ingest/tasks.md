# Tasks: enforce-tradeable-classes-at-ingest

## 1. Domain layer

- [ ] 1.1 Create `apps/api/src/algotrader_api/domain/__init__.py` and `tradeable.py`
- [ ] 1.2 `TRADEABLE_CLASSES = frozenset({"share", "etf", "bond"})`
- [ ] 1.3 `is_tradeable(class_name)` and `filter_tradeable(rows)` helpers
- [ ] 1.4 Tests: `tests/test_tradeable.py` covers the contract (7 cases)

## 2. Runtime gates (three layers)

- [ ] 2.1 `ingestion/universe.py::discover_universe` skips non-tradeable `get_<class>()` calls
- [ ] 2.2 `ingestion/universe.py::upsert_instruments` filters rows before INSERT
- [ ] 2.3 `ingestion/backfill.py::_discover_universe` skips non-tradeable classes
- [ ] 2.4 `ingestion/backfill.py::_list_instruments` filters via `WHERE class IN (...)`

## 3. Re-export for backwards compat

- [ ] 3.1 `maintenance/cleanup.py` imports `TRADEABLE_CLASSES` from the new home and re-exports
- [ ] 3.2 `scripts/cleanup_universe.py` keeps working (no change beyond the
      already-applied `_format_summary` fix)

## 4. Tests

- [ ] 4.1 `tests/test_universe_filter.py`: 2 unit tests for `upsert_instruments` filter
- [ ] 4.2 Rewrite `tests/ingestion/test_universe.py::test_discover_universe_returns_all_classes`
      → `..._returns_tradeable_classes_only`
- [ ] 4.3 Update `tests/ingestion/test_universe_coverage.py` to expect 2 (not 3) classes
- [ ] 4.4 Update `tests/test_backfill.py::test_discover_universe_returns_total_instrument_count`
      to assert `get_futures` / `get_options` are `assert_not_called`
- [ ] 4.5 Rename `tests/test_backfill.py::test_list_instruments_returns_all_classes`
      → `..._returns_tradeable_only`

## 5. Spec apply + archive

- [ ] 5.1 `openspec validate enforce-tradeable-classes-at-ingest --strict`
- [ ] 5.2 Apply delta to canonical `openspec/specs/data-fetch/spec.md`
- [ ] 5.3 `openspec validate data-fetch --strict`
- [ ] 5.4 `openspec archive enforce-tradeable-classes-at-ingest --yes --skip-specs`

## 6. Live verification

- [ ] 6.1 `scripts/cleanup_universe.py` (without `--dry-run`) on the prod DB
- [ ] 6.2 Restart backend; `/api/admin/backfill/pending` shows only share/etf/bond rows
- [ ] 6.3 Re-trigger a backfill; it does NOT call `get_futures` / `get_options` (check logs)
- [ ] 6.4 `bars_count` does not decrease by more than the cleanup delta
