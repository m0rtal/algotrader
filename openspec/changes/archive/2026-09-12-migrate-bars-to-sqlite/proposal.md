# migrate-bars-to-sqlite — Proposal

## Why

The Bars tab (`/api/tickets`) currently responds in ~2 seconds because
the backend scans ~7,100 parquet files via DuckDB on every request.
The drilldown chart (`/api/bars/<symbol>`) responds in ~700 ms for
the same reason.

Both endpoints have the same data already living in SQLite via
`instrument_metadata` (counts and last-write timestamps) and the raw
candles living in parquet. Neither endpoint reads from SQLite today
because there is no `bars` row table — only the parquet files hold
candles.

We want both endpoints under 100 ms latency. Per the operator
requirement set on 2026-09-12 ("задержка в любом элементе интерфейса
не должна быть больше 0,1с"), this is the next thing to fix.

## What Changes

Add a single new SQLite table `bars(figi, ts, open, high, low, close,
volume, PRIMARY KEY(figi, ts))` and write every successful backfill
candle into it alongside the existing parquet write.

* New migration `005_bars_table.sql` under
  `apps/api/src/algotrader_api/db/migrations/`.
* `BackfillRunner._backfill_one` performs a single SQLite transaction
  with `INSERT OR REPLACE INTO bars ...` for every candle, plus an
  UPDATE on `instrument_metadata` to keep `total_bars`, `first_bar_ts`,
  `last_bar_ts` in sync with the SQLite row count.
* `/api/bars/<symbol>` resolves ticker → figi via `instruments`, then
  `SELECT * FROM bars WHERE figi = ? ORDER BY ts`. Drops DuckDB from
  this path entirely.
* `/health` reads `bars_count` from `SELECT COUNT(*) FROM bars` instead
  of `DuckDB count_bars()` (which scans all parquet files).
* New operator script `scripts/migrate_parquet_to_sqlite.py`
  imports the existing 2.4 M candles from parquet into the `bars`
  table (one-shot, idempotent).
* `_flag_orphan_ok_rows` (already in `main.py` lifespan) is extended
  to also reset `total_bars = 0` and mark `last_run_status =
  'bars_missing_on_disk'` for figis whose parquet was deleted
  manually (e.g. via `cleanup_universe.py`).

Aggregations such as `bars_count`, `first_date`, `last_date`,
`file_size` are **not** stored in their own summary table. They are
computed by SQLite queries on `bars` (counts, MIN, MAX) plus an
`os.stat` call for `file_size`. This avoids a cache layer and keeps
the existing `instrument_metadata` table as the single source of
truth for last-write timestamps.

## Impact

* **Bars tab latency**: from ~2 s to <50 ms (3700 figis, all from
  SQLite, no DuckDB scan, no filesystem scan per request).
* **Drilldown chart latency**: from ~700 ms to <50 ms.
* **`/health` latency**: from ~319 ms to <10 ms.
* **Backfill write path**: ~1 extra SQLite INSERT batch per ticker
  (~600 rows × 2.4M total) — negligible vs the existing parquet write.
* **Storage**: `bars` table grows by ~50 MB at current scale (2.4 M
  rows × ~21 bytes). The same SQLite file already holds the
  `instruments`, `instrument_metadata`, `ingestion_logs`,
  `settings`, `secrets` tables; one more table does not change the
  backup story.
* **Front-end**: no changes — `/api/tickets` and `/api/bars/<symbol>`
  keep the same JSON shape.
* **MSW handlers**: no changes — the existing passthroughs already
  forward these endpoints.

## Non-Goals

* Deleting the parquet files. They stay on disk as the raw historical
  source of truth; downstream analytical queries and future
  Drilldown re-use them.
* Replacing DuckDB entirely. The backfill write path still calls
  DuckDB to read parquet and to dedupe / sort candles before writing
  them. The migration only removes DuckDB from the **read** path for
  `/api/tickets` and `/api/bars/<symbol>`.
* Pre-aggregated summary tables (`bars_summary`, `ticker_cache`).
  Aggregations are computed on read.
* Removing `instrument_metadata.total_bars`. It is kept consistent
  with the SQLite row count so other endpoints (`/api/admin/backfill/pending`)
  do not have to scan the `bars` table for counts.
* Replacing parquet with parquet-in-SQLite blobs. Parquet stays as
  raw data; SQLite stores the same data row-by-row for query speed.
