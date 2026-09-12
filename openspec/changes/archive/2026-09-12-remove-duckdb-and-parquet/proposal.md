# Remove DuckDB and legacy parquet files

## Why

After `migrate-bars-to-sqlite` (archived 2026-09-12), bars live in
SQLite. `db/duck.py` is now only used by:

- `routes/data_reads.py` — `get_tickers` via `duck.query_ticker_overview`
- `routes/data_reads.py` — file size lookup via `os.path.getsize` on
  the bars dir (this is filesystem, not DuckDB, but the bars dir
  is the legacy parquet dir)
- `scripts/migrate_parquet_to_sqlite.py` — the one-shot migration
  tool, which has already run

The DuckDB connection still scans 3717 parquet files on every
`/api/tickers` request (1.77 s queued + 3.4 s download = 5 s
UI delay, violating the 100 ms SLO). Parquet files take
42.98 MB on disk and are now redundant with the SQLite `bars`
table.

## What Changes

1. **Delete `apps/api/src/algotrader_api/db/duck.py`** entirely.
   Remove `duckdb` from `apps/api/pyproject.toml`.
2. **Rewrite `routes/data_reads.py:get_tickers`** to read aggregates
   from the `bars` SQLite table via a single `SELECT figi, MIN(ts),
MAX(ts), COUNT(*) GROUP BY figi` joined to `instruments` for
   metadata. No DuckDB connection, no parquet glob.
3. **Drop `apps/api/data/bars/`** directory and its 3717 parquet
   files. The bars SQLite table is the source of truth.
4. **Drop `apps/api/scripts/migrate_parquet_to_sqlite.py`** —
   one-shot tool, migration complete.
5. **Update `ingestion/backfill.py:_atomic_write_parquet`** to write
   only to SQLite. Remove the `bars_dir` and `_bars_dir_holder` plumbing.
6. **Drop the `bars_dir` config setting** and the `_bars_dir_holder`
   in `routes/data_reads.py`.
7. **Drop duckdb ATTACH path** in any remaining one-off scripts.

## Impact

- `/api/tickers` latency: 1.7 s → expected <50 ms
- Disk usage: -42.98 MB (parquet dir deleted)
- Code surface: -1 file (`db/duck.py`), -1 script
- Dependency: -`duckdb` package from `pyproject.toml`
- Backfill ingestion: bars go to SQLite only; the existing
  `_atomic_write_parquet` calls in `_backfill_one` become a
  single SQLite write (`replace_bars_for_figi`).
- Schema: no migration needed — `bars` table from
  `005_bars_table.sql` already holds everything.

## Non-Goals

- We are **not** moving bars to Postgres / MySQL. SQLite is fast
  enough and keeps a single binary file.
- We are **not** changing the broker SDK or ingestion rate limits.
- We are **not** adding caching layers. SQLite IS the cache now.
- We are **not** rewriting the UI. `Bars` and `Tickers` tabs
  receive the same JSON shapes; no MSW handler changes needed
  (handlers already mock these shapes).
