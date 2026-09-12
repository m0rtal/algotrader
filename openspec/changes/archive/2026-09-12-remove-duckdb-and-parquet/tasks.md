# Tasks: Remove DuckDB and legacy parquet files

## 1. Search & confirm surface area

- [ ] 1.1 grep `duckdb` and `duck\.` across `apps/api/` — list
      every consumer. Confirm the list matches the proposal's
      Impact section.
- [ ] 1.2 grep `_atomic_write_parquet` and `_bars_dir_holder` —
      confirm all references are accounted for.

## 2. Rewrite `get_tickers`

- [ ] 2.1 Add failing test `test_get_tickers_returns_sqlite_aggregates`
      to `tests/test_data_reads_routes.py` covering the new shape.
- [ ] 2.2 Rewrite `routes/data_reads.py:get_tickers` to run the
      SQLite `GROUP BY figi` join. Drop the `os.listdir(bars_dir)`
      file-size loop.
- [ ] 2.3 Add `fileSize: 0` (or omit field) and verify the test.
- [ ] 2.4 Verify `get_tickers` returns <100 ms locally.

## 3. Drop `bars_dir` plumbing

- [ ] 3.1 Remove `_bars_dir_holder` and `set_bars_dir` from
      `routes/data_reads.py`.
- [ ] 3.2 Remove `bars_dir` setting from `apps/api/src/algotrader_api/config.py`
      (or keep it for one release with a deprecation comment — choose).
- [ ] 3.3 Remove `data_reads.set_bars_dir(...)` call from
      `main.py` lifespan.
- [ ] 3.4 Remove `_bars_dir_holder` defensive `RuntimeError` from
      `routes/data_reads.py`.

## 4. Update backfill write path

- [ ] 4.1 Update `_atomic_write_parquet` in
      `ingestion/backfill.py` to write SQLite-only (or remove the
      helper entirely and inline `replace_bars_for_figi`).
- [ ] 4.2 Update existing tests in `test_backfill.py` so parquet
      side effects are no longer asserted (e.g.
      `test_backfill_one_writes_parquet` becomes
      `test_backfill_one_writes_sqlite_bars`).

## 5. Delete DuckDB module + dependency

- [ ] 5.1 Delete `apps/api/src/algotrader_api/db/duck.py`.
- [ ] 5.2 Remove `duckdb` from `apps/api/pyproject.toml`.
- [ ] 5.3 Run `uv lock` to refresh the lockfile.
- [ ] 5.4 Run full pytest — confirm nothing else imports
      `algotrader_api.db.duck`.

## 6. Delete the one-shot migration script

- [ ] 6.1 Delete `apps/api/scripts/migrate_parquet_to_sqlite.py`.

## 7. Delete legacy parquet files

- [ ] 7.1 Verify `bars_count` from `/health` matches the parquet
      row count before deletion (currently 2,361,394 SQLite vs 2,427,471
      parquet — the delta is the 15 files that hit UNIQUE constraint
      errors during migration and the 3 skipped debug files).
- [ ] 7.2 Stop the backend (`sudo systemctl stop algotrader-api`)
      so the parquet directory is not held open.
- [ ] 7.3 `rm -rf apps/api/data/bars/`.
- [ ] 7.4 Start the backend; confirm `/api/tickers` and `/health`
      return 200.
- [ ] 7.5 Confirm `bars_count` from `/health` is unchanged.

## 8. Spec apply + archive

- [ ] 8.1 `openspec validate remove-duckdb-and-parquet --strict`
      passes.
- [ ] 8.2 Apply delta to canonical `openspec/specs/data-fetch/spec.md`.
- [ ] 8.3 `openspec validate data-fetch --strict` passes.
- [ ] 8.4 `openspec archive remove-duckdb-and-parquet --yes --skip-specs`.
