# migrate-bars-to-sqlite — Design

## Stack

* Python 3.11 / FastAPI / SQLite 3 (built-in `sqlite3`).
* Existing `apps/api/src/algotrader_api/db/migrations/` migration
  framework (forward-only `*.sql` files, idempotent via `CREATE
  TABLE IF NOT EXISTS`).
* No new dependency, no new service.

## Layout

```
apps/api/src/algotrader_api/db/
├── migrations/
│   └── 005_bars_table.sql           (NEW)
└── sqlite.py                          (extend with bars helpers)

apps/api/src/algotrader_api/ingestion/
└── backfill.py                        (extend _backfill_one)

apps/api/src/algotrader_api/routes/
└── bars.py                            (rewrite /api/bars/<symbol> to read bars table)
└── health.py                          (replace DuckDB count with SQLite SELECT)

apps/api/src/algotrader_api/main.py    (extend _flag_orphan_ok_rows)
apps/api/scripts/
└── migrate_parquet_to_sqlite.py      (NEW — operator script)
apps/api/tests/
└── test_bars_sqlite.py               (NEW — coverage for migration + read)
```

## Data Flow

### Write path: Backfill

```
BackfillRunner._backfill_one(figi, candles)
├── _atomic_write_parquet(figi, candles)               # unchanged
├── BEGIN TRANSACTION
│   ├── DELETE FROM bars WHERE figi = :figi             (replace strategy)
│   ├── INSERT OR REPLACE INTO bars (figi, ts, o, h, l, c, v) VALUES (...)  (× N candles)
│   └── UPDATE instrument_metadata
│         SET total_bars    = (SELECT COUNT(*) FROM bars WHERE figi = :figi),
│             first_bar_ts  = (SELECT MIN(ts)     FROM bars WHERE figi = :figi),
│             last_bar_ts   = (SELECT MAX(ts)     FROM bars WHERE figi = :figi),
│             last_run_status = 'ok',
│             last_run_at   = datetime('now')
│         WHERE figi = :figi
└── COMMIT
```

The transaction wraps all three writes so a concurrent drilldown
reader either sees the pre-run snapshot or the post-run snapshot.

### Read path: `/api/tickets` (Bars tab)

```
SELECT m.figi AS source_figi, m.total_bars AS bars, m.first_bar_ts, m.last_bar_ts,
       s.bytes AS file_size, i.ticker, i.name, i.sector, i.currency, i.lot_size
FROM instrument_metadata m
JOIN instruments i ON i.figi = m.figi
JOIN (SELECT stem, bytes FROM files_sizes) s ON s.stem = m.figi
WHERE m.total_bars > 0
ORDER BY i.ticker
```

`files_sizes` is a one-shot CTE computed at startup from `os.listdir`
+ `os.stat`, cached in module-level memory and invalidated by
`os.path.getmtime(bars_dir)` mtime check (≤50 ms even for 3700 files).

### Read path: `/api/bars/<symbol>`

```
GET /api/bars/{symbol}
├── resolve ticker → figi via:
│   SELECT figi FROM instruments WHERE ticker = :symbol OR figi = :symbol LIMIT 1
├── SELECT ts, open, high, low, close, volume
│   FROM bars WHERE figi = :figi ORDER BY ts
└── return JSON list
```

No DuckDB, no parquet scan.

### Read path: `/health`

```
SELECT (SELECT COUNT(*) FROM bars) AS bars_count
```

Replaces `duck.count_bars(bars_dir)` which currently scans 7137
parquet files.

### Migration script: `migrate_parquet_to_sqlite.py`

```
1. Build DuckDB connection over data/bars/*.parquet
2. INSERT OR IGNORE INTO bars (figi, ts, o, h, l, c, v)
   SELECT figi_resolved, ts, open, high, low, close, volume
   FROM read_parquet('data/bars/*.parquet', union_by_name=true)
   -- figi_resolved: filename stem for legacy files, figi column for modern files
3. UPDATE instrument_metadata SET total_bars / first_bar_ts / last_bar_ts
   from aggregated SELECT per figi
4. VACUUM (defragment SQLite after bulk insert)
5. print summary: rows inserted, time taken
```

Idempotent: re-running picks up new candles added between runs.

## Risks

* **Write amplification**: every backfill run writes twice (parquet +
  SQLite). Acceptable — 2.4 M rows total, ~600 rows per figi. INSERT
  OR REPLACE on the same `(figi, ts)` is O(1).
* **WAL pressure**: with the `bars` table growing, WAL files may
  accumulate. `PRAGMA journal_mode=WAL` is on by default; the
  periodic `VACUUM` in the migration script reclaims space.
* **Concurrent reader during transaction**: SQLite WAL gives
  readers a snapshot of the committed state at statement start, so
  drilldown during a backfill sees the pre-backfill snapshot. The
  runner does not block readers.
* **Migration run time**: ~30–60 seconds for 2.4 M rows on a
  commodity box. Run once on operator request, not automatically.
* **Backwards compatibility**: old clients reading `/api/tickets`
  see the same JSON shape; old clients reading `/api/bars/<symbol>`
  see the same JSON shape. No client code changes.
