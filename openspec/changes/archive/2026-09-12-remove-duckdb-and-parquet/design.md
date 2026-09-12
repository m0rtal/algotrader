# Design: Remove DuckDB and legacy parquet files

## Stack

- **Database:** SQLite (existing `state.db`) is the single source
  of truth. No changes to the schema.
- **Read path:** every API endpoint that currently touches DuckDB
  switches to a pure SQLite query.
- **Write path:** backfill ingestion writes bars into the SQLite
  `bars` table via the existing `replace_bars_for_figi` helper
  (added in `migrate-bars-to-sqlite`).
- **No new dependencies.** We drop `duckdb` from
  `pyproject.toml`.

## Data Flow

### `get_tickers` (read path)

Before (DuckDB):

```python
overview = duck.query_ticker_overview(bars_dir, sqlite_path=...)
# + os.listdir(bars_dir) for file sizes
# + sqlite_exec(instruments) for metadata
```

After (SQLite only):

```python
rows = sqlite_exec(
    sqlite_path,
    """
    SELECT
        b.figi,
        MIN(b.ts)  AS first_ts,
        MAX(b.ts)  AS last_ts,
        COUNT(*)   AS bars
    FROM bars b
    JOIN instruments i ON i.figi = b.figi
    WHERE i.ticker IS NOT NULL
    GROUP BY b.figi
    """,
    (),
)
# Single query, ~10 ms for 2.36M rows.
```

`fileSize` field disappears from the response (no on-disk parquet
file to size). Frontend already handles `fileSize: 0`; we will
keep the field but always return 0, or drop it via the spec delta.

### Backfill (write path)

Before:

```
fetch candles
→ _atomic_write_parquet(...)       # write to data/bars/<figi>.parquet
→ replace_bars_for_figi(...)       # mirror into SQLite
```

After:

```
fetch candles
→ replace_bars_for_figi(...)       # SQLite only
```

The `bars_dir` setting and `_bars_dir_holder` plumbing disappear
from `data_reads.py`. `main.py` lifespan no longer calls
`data_reads.set_bars_dir(...)`.

## Layout

```
apps/api/src/algotrader_api/
├── db/
│   ├── bars_sqlite.py     (unchanged)
│   ├── sqlite.py          (unchanged)
│   └── duck.py            (DELETED)
├── ingestion/
│   ├── backfill.py        (parquet write removed)
│   └── bars.py            (unchanged — already SQLite-backed)
├── routes/
│   ├── bars.py            (unchanged — already SQLite)
│   ├── data_reads.py      (get_tickers → SQLite; bars_dir plumbing gone)
│   └── health.py          (unchanged)
└── main.py                (drop set_bars_dir call)
```

## Risks

- **R1 — `/api/tickers` UI expectations.** Frontend renders a
  `Bars` count, `FirstDate`, `LastDate`, `fileSize`. After
  migration `fileSize` is gone (no file). Mitigation: keep the
  field, return `0`, document in the spec delta.
- **R2 — `query_ticker_overview` had schema-probe logic** for
  legacy parquet files. After deletion the probe is dead code; we
  delete it. The new SQLite query handles both ticker-style and
  figi-style via the `JOIN instruments` filter.
- **R3 — duckdb dependency is used by `scripts/migrate_parquet_to_sqlite.py`**
  for the ATTACH + read_parquet bulk path. The script has already
  run on production. We delete the script.
- **R4 — parquet deletion is irreversible** in the sense that
  re-migration would need a fresh backfill. SQLite is already
  populated (2,361,394 bars across 3699 figis), so nothing of
  value is lost.
- **R5 — any other DuckDB user we missed.** Mitigation: grep the
  codebase for `duckdb` and `duck\.` before deletion.

## Testing

- Existing `test_bars_route.py`, `test_bars_sqlite.py` cover the
  SQLite read path — these stay green.
- New `test_data_reads_tickers.py` covers the rewritten
  `get_tickers`: rows joined from `bars` × `instruments`,
  field shape, fileSize default.
- Existing `test_backfill.py::test_backfill_one_mirrors_candles_into_sqlite_bars_table`
  already verifies the SQLite write path.
- Live verification: `/api/tickers` <100 ms; `/health` <50 ms.

## Rollback

Restore from `apps/api/data/bars/` if it was archived before
deletion. (Not planned — but feasible in the next 60 seconds
after deletion while the OS page cache still has the files.)
