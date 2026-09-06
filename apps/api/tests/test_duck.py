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
