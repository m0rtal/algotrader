# migrate-bars-to-sqlite — Tasks

## Migration & schema

- [ ] Write `apps/api/src/algotrader_api/db/migrations/005_bars_table.sql`
      with `CREATE TABLE IF NOT EXISTS bars (figi, ts, open, high, low,
      close, volume, PRIMARY KEY(figi, ts))` and
      `CREATE INDEX IF NOT EXISTS idx_bars_figi ON bars(figi)`.
- [ ] Run `uv run pytest` after migration to confirm `pytest's autouse
      _run_migrations` fixture picks up the new file and applies it.

## Write path: BackfillRunner

- [ ] In `apps/api/src/algotrader_api/ingestion/backfill.py`, extend
      `_backfill_one` to perform a SQLite transaction:
      - `DELETE FROM bars WHERE figi = :figi` (replace strategy)
      - Bulk `INSERT OR REPLACE INTO bars` for every candle
      - `UPDATE instrument_metadata` with `total_bars`, `first_bar_ts`,
        `last_bar_ts` recomputed via subqueries
- [ ] Add `bars_db.py` helper module under
      `apps/api/src/algotrader_api/db/` with
      `replace_bars_for_figi(sqlite_path, figi, candles)` and
      `resolve_figi_for_ticker(sqlite_path, symbol)`.
- [ ] Add tests under `apps/api/tests/ingestion/test_backfill.py`:
      - `test_backfill_one_writes_to_bars_table`
      - `test_backfill_one_updates_metadata_after_bars_write`
      - `test_backfill_replace_strategy_dedupes_same_figi_ts`

## Read path: `/api/bars/<symbol>`

- [ ] Rewrite `apps/api/src/algotrader_api/routes/bars.py` to read
      from `bars` table via `resolve_figi_for_ticker` + `SELECT * FROM
      bars`. No DuckDB, no parquet.
- [ ] Add tests under `apps/api/tests/test_bars.py`:
      - `test_bars_endpoint_returns_rows_from_sqlite`
      - `test_bars_endpoint_404_for_unknown_symbol`
      - `test_bars_endpoint_resolves_figi_from_ticker_or_figi`

## Read path: `/health`

- [ ] Rewrite `apps/api/src/algotrader_api/routes/health.py` to read
      `bars_count` from `SELECT COUNT(*) FROM bars`. Drop
      `duck.count_bars`.
- [ ] Update `apps/api/tests/test_health.py` to expect SQLite count.

## Operator script

- [ ] Write `apps/api/scripts/migrate_parquet_to_sqlite.py` with
      DuckDB→SQLite bulk insert (see design.md).
- [ ] Make idempotent: re-running produces no duplicate `(figi, ts)`
      rows. `INSERT OR IGNORE` handles this.
- [ ] Print summary at the end: rows inserted, time taken, figis
      touched, orphans count.
- [ ] Add `--dry-run` flag (compute + report, no writes).

## Orphan reconciliation

- [ ] In `apps/api/src/algotrader_api/main.py`, extend
      `_flag_orphan_ok_rows` to mark rows for figis whose parquet
      file no longer exists with `total_bars = 0` and
      `last_run_status = 'bars_missing_on_disk'`.
- [ ] Add tests under `apps/api/tests/test_main_lifespan.py`.

## Coverage + latency verification

- [ ] Run `uv run pytest --cov=algotrader_api -q` — must reach
      ≥95% coverage. New tests cover the bars helpers and the
      rewrite path. The migration script is excluded from coverage
      gate (operator script).
- [ ] Manual latency check: hit
      `curl http://127.0.0.1:8000/api/tickets` from the Hermes host
      and confirm p50 latency < 100 ms. Same for `/api/bars/<symbol>`.

## OpenSpec workflow

- [ ] After implementation passes tests and lint:
      `openspec archive migrate-bars-to-sqlite --yes --skip-specs`.
- [ ] Verify with `git log --oneline -5` and `git status` that the
      change is on `origin/main`.
