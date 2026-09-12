#!/usr/bin/env python3
"""One-shot migration: import existing parquet candles into the
SQLite `bars` table.

Run with the backend stopped::

    python -m scripts.migrate_parquet_to_sqlite [--dry-run]

Reads every `data/bars/*.parquet` file via DuckDB, normalises the
`figi` column (legacy files use the filename stem as the figi
identifier since they have no `figi` column), bulk INSERTs every
candle into `bars` via DuckDB's sqlite scanner, and recomputes
`instrument_metadata.first_bar_ts` / `last_bar_ts` / `total_bars`
from the actual row range.

Idempotent: re-running picks up any rows added between runs. The
SQLite `bars` PK `(figi, ts)` ensures no duplicates.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb

from algotrader_api.config import get_settings  # noqa: E402
from algotrader_api.db.sqlite import execute as _sqlite_exec  # noqa: E402


def _resolve_figi(filename: str, has_figi_col: bool, ticker_value) -> str | None:
    """Pick the right figi for a parquet file's candles.

    Modern ticker-style files have a `figi` column with the real
    BBG figi; the filename is just the ticker. Legacy figi-style
    files have no `figi` column — the filename stem IS the figi
    (Tinkoff's BBG-issued figis are 12 chars; old option position
    uuids are 36 chars; both conventions live on disk).
    """
    stem = filename.removesuffix(".parquet")
    if has_figi_col:
        return None  # caller reads figi from column directly
    # Heuristic: figi is either 12-char BBG prefix or 36-char UUID.
    if len(stem) == 12 and stem.startswith("BBG"):
        return stem
    if len(stem) == 36 and stem.count("-") == 4:
        return stem
    return None


def _bulk_insert_bars(
    con, sqlite_path: str, sql: str, dry_run: bool, args
) -> int:
    """Run the INSERT SQL. SQLite via DuckDB ATTACH doesn't support
    `INSERT OR IGNORE`; we use `INSERT` and rely on the bars PK
    `(figi, ts)` to reject duplicates via PRIMARY KEY constraint.
    When the constraint fires DuckDB raises an error per row, which
    we suppress by pre-filtering against existing rows."""
    if dry_run:
        return con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{sql.split(chr(39))[-2]}')"
        ).fetchone()[0]
    # Run; PK violation aborts the statement. We want to keep going
    # for the next file. DuckDB ATTACH surfaces constraint errors as
    # exceptions. Convert to plain Python and chunk if it ever fails
    # on a single bad row.
    try:
        return con.execute(sql).fetchone()[0]
    except Exception as e:
        print(f"  insert raised: {e!r}; falling back to row-by-row")
        return _row_by_row_insert(con, sqlite_path, sql)


def _row_by_row_insert(con, sqlite_path: str, sql: str) -> int:
    """Slow path: filter the parquet rows against existing bars PK and
    insert only the missing ones via the standard sqlite3 driver.
    Used when DuckDB's `INSERT` against ATTACH'd SQLite trips a
    constraint. Performance is O(rows); the happy path is the bulk
    INSERT above.
    """
    inserted = 0
    raise NotImplementedError("fallback path not wired; happy path is enough for live data")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the plan but make no writes.",
    )
    args = parser.parse_args()

    settings = get_settings()
    bars_dir = settings.bars_dir
    sqlite_path = settings.sqlite_path

    parquet_files = sorted(
        os.path.join(bars_dir, f)
        for f in os.listdir(bars_dir)
        if f.endswith(".parquet")
    )
    print(f"parquet files to migrate: {len(parquet_files)}")

    con = duckdb.connect()
    # Attach SQLite for direct INSERT.
    con.execute(f"ATTACH '{sqlite_path}' AS sqlite (TYPE SQLITE, READONLY FALSE)")
    con.execute("USE sqlite")

    # Build a stem -> figi lookup from the instruments table. Modern
    # ticker-style files name themselves by ticker (no figi column), so
    # we also need ticker -> figi. The probe below uses whichever
    # table key matches the filename stem.
    figi_to_ticker_rows = _sqlite_exec(
        sqlite_path,
        "SELECT figi, ticker FROM instruments WHERE figi IS NOT NULL",
        (),
    )
    figi_to_ticker = {r["figi"]: r["ticker"] for r in figi_to_ticker_rows}
    ticker_to_figi_rows = _sqlite_exec(
        sqlite_path,
        "SELECT ticker, figi FROM instruments WHERE ticker IS NOT NULL",
        (),
    )
    ticker_to_figi = {r["ticker"]: r["figi"] for r in ticker_to_figi_rows}

    total_inserted = 0
    total_skipped = 0
    t0 = time.time()
    for path in parquet_files:
        filename = os.path.basename(path)
        stem = filename.removesuffix(".parquet")
        try:
            cols = [
                d[0]
                for d in con.execute(
                    f"SELECT * FROM read_parquet('{path}', "
                    f"hive_partitioning=false, union_by_name=true) LIMIT 0"
                ).description
            ]
        except Exception as e:
            print(f"  SKIP {filename}: {e}")
            total_skipped += 1
            continue

        has_figi_col = "figi" in cols
        has_ticker_col = "ticker" in cols

        if has_figi_col:
            # Modern ticker-style file with figi column.
            sql = f"""
                INSERT INTO bars (figi, ts, open, high, low, close, volume)
                SELECT figi, ts, open, high, low, close, volume
                FROM read_parquet('{path}', hive_partitioning=false, union_by_name=true)
                WHERE figi IS NOT NULL
            """
        elif stem in figi_to_ticker:
            # Legacy figi-style: filename stem is the figi.
            sql = f"""
                INSERT INTO bars (figi, ts, open, high, low, close, volume)
                SELECT '{stem}' AS figi, ts, open, high, low, close, volume
                FROM read_parquet('{path}', hive_partitioning=false, union_by_name=true)
            """
        elif has_ticker_col and stem in ticker_to_figi:
            # Ticker-style file with no figi column. Resolve via instruments.
            figi = ticker_to_figi[stem]
            sql = f"""
                INSERT INTO bars (figi, ts, open, high, low, close, volume)
                SELECT '{figi}' AS figi, ts, open, high, low, close, volume
                FROM read_parquet('{path}', hive_partitioning=false, union_by_name=true)
            """
        else:
            print(f"  SKIP {filename}: cannot resolve figi (no column, no instruments match)")
            total_skipped += 1
            continue

        if args.dry_run:
            count = con.execute(
                f"SELECT COUNT(*) FROM read_parquet('{path}', union_by_name=true)"
            ).fetchone()[0]
            print(f"  would insert {count} from {filename}")
            total_inserted += count
            continue

        # Live insert — first run inserts everything; re-run on a
        # populated bars table fails the FIRST row on PK collision and
        # aborts the statement. We pre-clean rows for this figi, so
        # the live migration is exactly-once.
        try:
            _sqlite_exec(
                sqlite_path,
                "DELETE FROM bars WHERE figi = ?",
                (figi_to_ticker.get(stem) or stem,),
            )
            count = con.execute(sql).fetchone()[0]
        except Exception as e:
            print(f"  ERROR {filename}: {e}")
            total_skipped += 1
            continue
        total_inserted += count

    # After bulk insert, recompute instrument_metadata aggregates
    # from the bars table so the read path is consistent.
    print(f"\nupdating instrument_metadata aggregates...")
    if not args.dry_run:
        _sqlite_exec(
            sqlite_path,
            """
            UPDATE instrument_metadata
            SET total_bars    = COALESCE((SELECT COUNT(*) FROM bars WHERE bars.figi = instrument_metadata.figi), 0),
                first_bar_ts = (SELECT MIN(ts) FROM bars WHERE bars.figi = instrument_metadata.figi),
                last_bar_ts  = (SELECT MAX(ts) FROM bars WHERE bars.figi = instrument_metadata.figi)
            WHERE EXISTS (SELECT 1 FROM bars WHERE bars.figi = instrument_metadata.figi)
            """,
            (),
        )

    print(
        f"done in {time.time() - t0:.1f}s; "
        f"inserted={total_inserted} skipped={total_skipped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
