"""Test DuckDB parquet layer + seed."""
from __future__ import annotations

import duckdb

from algotrader_api.db import duck
from algotrader_api.seed import seed_bars


def test_seed_bars_writes_parquet(tmp_path):
    bars_dir = str(tmp_path / "bars")
    n = seed_bars(bars_dir)
    assert n > 0
    conn = duckdb.connect(":memory:")
    rows = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{bars_dir}/*.parquet', union_by_name=true)").fetchone()
    assert rows[0] == n


def test_seed_bars_idempotent_when_called_twice(tmp_path):
    bars_dir = str(tmp_path / "bars")
    seed_bars(bars_dir)
    seed_bars(bars_dir)  # overwrites — should not fail
    conn = duckdb.connect(":memory:")
    rows = conn.execute(f"SELECT COUNT(DISTINCT ticker) FROM read_parquet('{bars_dir}/*.parquet', union_by_name=true)").fetchone()
    assert rows[0] >= 16  # 16 tickers seeded


def test_duck_list_tickers(tmp_path):
    bars_dir = str(tmp_path / "bars")
    seed_bars(bars_dir)
    tickers = duck.list_tickers(bars_dir)
    assert "SBER" in tickers
    assert "GAZP" in tickers


def test_duck_count_bars(tmp_path):
    bars_dir = str(tmp_path / "bars")
    seed_bars(bars_dir)
    n = duck.count_bars(bars_dir)
    assert n > 200  # 16 tickers × ~180 trading days


def test_duck_query_bars_returns_dicts(tmp_path):
    bars_dir = str(tmp_path / "bars")
    seed_bars(bars_dir)
    rows = duck.query_bars(bars_dir, "SBER")
    assert len(rows) > 0
    assert "open" in rows[0]
    assert "high" in rows[0]
    assert "low" in rows[0]
    assert "close" in rows[0]
    assert "volume" in rows[0]


def test_duck_query_bars_with_date_filter(tmp_path):
    bars_dir = str(tmp_path / "bars")
    seed_bars(bars_dir)
    rows = duck.query_bars(bars_dir, "SBER", date_from="2025-12-01", date_till="2025-12-31")
    assert all("2025-12-01" <= str(r["ts"]) <= "2025-12-31" for r in rows)


def test_query_ticker_overview_resolves_legacy_figi_files(tmp_path):
    """Legacy figi-style parquet files ( = figi UUID, no
    `ticker` column inside) used to be invisible to
    `query_ticker_overview` because DuckDB only groups by the
    non-null ticker column. The fix: when a file has no ticker
    column, use the filename (stem) as the figi, then JOIN against
    the SQLite `instruments` table to resolve it to a ticker."""
    from algotrader_api.db import duck as duck_mod
    import sqlite3

    bars_dir = tmp_path / "bars"
    bars_dir.mkdir()

    # Seed the SQLite instruments table with a figi we will create
    # a legacy parquet file for. Tests share the canonical state.db
    # under tmp_path.
    con = sqlite3.connect(str(tmp_path / "state.db"))
    con.execute(
        "CREATE TABLE instruments "
        "(ticker TEXT PRIMARY KEY, figi TEXT UNIQUE, class TEXT, "
        "name TEXT, currency TEXT, lot_size INTEGER)"
    )
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("OLDLEGACY", "aabbccdd-1111-2222-3333-444455556666", "share", "Old Legacy", "rub", 1),
    )
    con.commit()
    con.close()

    # Write a figi-style parquet file: filename = figi UUID, no ticker column
    import duckdb as _duck

    conn = _duck.connect(":memory:")
    conn.execute(
        f"COPY (SELECT DATE '2025-01-01' AS ts, 100.0 AS open, 110.0 AS high, "
        f"95.0 AS low, 105.0 AS close, 1000 AS volume UNION ALL "
        f"SELECT DATE '2025-01-02', 101.0, 111.0, 96.0, 106.0, 1100) "
        f"TO '{bars_dir}/aabbccdd-1111-2222-3333-444455556666.parquet' "
        f"(FORMAT PARQUET)"
    )
    conn.close()

    # Call overview with sqlite_path pointing at our test DB
    rows = duck_mod.query_ticker_overview(
        str(bars_dir), sqlite_path=str(tmp_path / "state.db")
    )
    tickers = {r["ticker"] for r in rows}
    assert "OLDLEGACY" in tickers, f"legacy figi not resolved; got {tickers}"
    legacy = next(r for r in rows if r["ticker"] == "OLDLEGACY")
    assert legacy["bars"] == 2
    assert legacy["first_ts"] is not None
    assert legacy["last_ts"] is not None


def test_query_ticker_overview_merges_legacy_with_modern(tmp_path):
    """When a modern ticker-style parquet and a legacy figi-style
    parquet both resolve to the same ticker, the rows are merged —
    bar counts add up and min/max timestamps widen."""
    from algotrader_api.db import duck as duck_mod
    import sqlite3

    bars_dir = tmp_path / "bars"
    bars_dir.mkdir()

    con = sqlite3.connect(str(tmp_path / "state.db"))
    con.execute(
        "CREATE TABLE instruments "
        "(ticker TEXT PRIMARY KEY, figi TEXT UNIQUE, class TEXT, "
        "name TEXT, currency TEXT, lot_size INTEGER)"
    )
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("MERGED", "MERGED-FIGI", "share", "Merged Co", "rub", 1),
    )
    con.commit()
    con.close()

    import duckdb as _duck
    conn = _duck.connect(":memory:")
    # Modern file: ticker column, 3 bars
    conn.execute(
        f"COPY (SELECT 'MERGED' AS ticker, DATE '2025-06-01' AS ts, "
        f"100.0 AS open, 110.0 AS high, 95.0 AS low, 105.0 AS close, 1000 AS volume "
        f"UNION ALL SELECT 'MERGED', DATE '2025-06-02', 101, 111, 96, 106, 1100 "
        f"UNION ALL SELECT 'MERGED', DATE '2025-06-03', 102, 112, 97, 107, 1200) "
        f"TO '{bars_dir}/MERGED.parquet' (FORMAT PARQUET)"
    )
    # Legacy file: filename = figi, no ticker column, 2 bars
    conn.execute(
        f"COPY (SELECT DATE '2024-12-01' AS ts, 50.0 AS open, 55.0 AS high, "
        f"49.0 AS low, 53.0 AS close, 500 AS volume UNION ALL "
        f"SELECT DATE '2024-12-02', 51, 56, 50, 54, 600) "
        f"TO '{bars_dir}/MERGED-FIGI.parquet' (FORMAT PARQUET)"
    )
    conn.close()

    rows = duck_mod.query_ticker_overview(
        str(bars_dir), sqlite_path=str(tmp_path / "state.db")
    )
    merged = next((r for r in rows if r["ticker"] == "MERGED"), None)
    assert merged is not None
    # 3 modern + 2 legacy = 5 bars
    assert merged["bars"] == 5
    # Min/max should span both ranges
    assert str(merged["first_ts"]) == "2024-12-01"
    assert str(merged["last_ts"]) == "2025-06-03"
