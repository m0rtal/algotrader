"""Tests for backfill_bonds_to_depth in
apps/api/src/algotrader_api/ingestion/backfill.py."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def db_with_bonds(tmp_path: Path):
    """In-memory SQLite with instruments + bars tables seeded."""
    con = sqlite3.connect(str(tmp_path / "test.db"))
    con.executescript("""
        CREATE TABLE instruments (
            figi TEXT PRIMARY KEY,
            ticker TEXT,
            class TEXT
        );
        CREATE TABLE bars (
            figi TEXT,
            ts TEXT,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            source TEXT DEFAULT 'moex',
            PRIMARY KEY (figi, ts)
        );
        INSERT INTO instruments VALUES ('BBG000BOND15', 'BOND15', 'bond');
        INSERT INTO instruments VALUES ('BBG000BOND30', 'BOND30', 'bond');
        INSERT INTO instruments VALUES ('BBG000BOND00', 'BOND00', 'bond');
        -- BOND15: 15 bars (sparse)
        INSERT INTO bars VALUES
            ('BBG000BOND15', '2024-12-01', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-02', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-03', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-04', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-05', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-06', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-09', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-10', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-11', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-12', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-13', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-16', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-17', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-18', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-19', 100, 101, 99, 100, 1000, 'moex');
        -- BOND30: 250 bars (full, should be skipped).
        -- Generate 250 unique dates by spreading rows across years
        -- (month + day alone collide after 336; year prefix avoids it).
        INSERT INTO bars
            SELECT 'BBG000BOND30',
                   (2010 + ((n - 1) / 28)) || '-' ||
                   printf('%02d', ((n - 1) / 28 % 12) + 1) || '-' ||
                   printf('%02d', ((n - 1) % 28) + 1),
                   100, 101, 99, 100, 1000, 'moex'
            FROM (
                WITH RECURSIVE seq(n) AS (
                    SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < 250
                )
                SELECT n FROM seq
            );
        -- BOND00: 0 bars
    """)
    yield con
    con.close()


def _make_candle(figi: str, ts: str):
    c = MagicMock()
    c.figi = figi
    c.ts = ts
    c.open = 100
    c.high = 101
    c.low = 99
    c.close = 100
    c.volume = 1000
    return c


def test_sparse_bond_is_brought_to_target_depth(db_with_bonds):
    """Bond with 15 bars should be brought to >=30 after backfill."""
    from algotrader_api.ingestion.backfill import backfill_bonds_to_depth

    # Mock Tinkoff to return 20 new bars for BOND15
    new_candles = [_make_candle('BBG000BOND15', f'2025-01-{i+1:02d}') for i in range(20)]
    with patch('algotrader_api.ingestion.client.make_client') as mock_client_factory, \
         patch('algotrader_api.ingestion.rate_limit.get_global') as mock_rl:
        mock_client = MagicMock()
        mock_client.get_historical_bonds = MagicMock(return_value=new_candles)
        mock_client_factory.return_value = mock_client
        mock_rl.return_value.acquire = MagicMock()

        result = backfill_bonds_to_depth(target_days=30, conn=db_with_bonds)

    # Verify BOND15 now has 35 bars (15 original + 20 new)
    new_count = db_with_bonds.execute(
        "SELECT COUNT(*) FROM bars WHERE figi='BBG000BOND15'"
    ).fetchone()[0]
    assert new_count >= 30
    assert result["bars_added"] >= 15


def test_full_bond_is_skipped(db_with_bonds):
    """Bond with 250 bars should NOT trigger a Tinkoff fetch."""
    from algotrader_api.ingestion.backfill import backfill_bonds_to_depth

    with patch('algotrader_api.ingestion.client.make_client') as mock_client_factory, \
         patch('algotrader_api.ingestion.rate_limit.get_global') as mock_rl:
        mock_client = MagicMock()
        mock_client.get_historical_bonds = MagicMock(return_value=[])
        mock_client_factory.return_value = mock_client
        mock_rl.return_value.acquire = MagicMock()

        result = backfill_bonds_to_depth(target_days=30, conn=db_with_bonds)

    # ADAPT-2: The brief's literal assertion (`assert_not_called()`) is
    # incompatible with the shared fixture — BOND15 (15 bars) and BOND00
    # (0 bars) MUST trigger a fetch in the same test run. We assert the
    # documented intent instead: BOND30 (the full bond) was never passed
    # to get_historical_bonds. Calls for BOND15/BOND00 are expected.
    called_figis = {
        call.kwargs.get("figi") for call in mock_client.get_historical_bonds.call_args_list
    }
    assert "BBG000BOND30" not in called_figis
    assert result["skipped"] >= 1


def test_zero_bar_bond_is_fully_backfilled(db_with_bonds):
    """Bond with 0 bars should get >=30 bars after backfill."""
    from algotrader_api.ingestion.backfill import backfill_bonds_to_depth

    new_candles = [_make_candle('BBG000BOND00', f'2025-02-{i+1:02d}') for i in range(30)]
    with patch('algotrader_api.ingestion.client.make_client') as mock_client_factory, \
         patch('algotrader_api.ingestion.rate_limit.get_global') as mock_rl:
        mock_client = MagicMock()
        mock_client.get_historical_bonds = MagicMock(return_value=new_candles)
        mock_client_factory.return_value = mock_client
        mock_rl.return_value.acquire = MagicMock()

        result = backfill_bonds_to_depth(target_days=30, conn=db_with_bonds)

    new_count = db_with_bonds.execute(
        "SELECT COUNT(*) FROM bars WHERE figi='BBG000BOND00'"
    ).fetchone()[0]
    assert new_count >= 30


def test_duplicate_bars_are_skipped(db_with_bonds):
    """Re-running backfill with the same candles should not produce duplicates."""
    from algotrader_api.ingestion.backfill import backfill_bonds_to_depth

    # First call: insert 5 new bars for BOND15 (which has 15)
    new_candles = [_make_candle('BBG000BOND15', f'2025-03-{i+1:02d}') for i in range(5)]
    with patch('algotrader_api.ingestion.client.make_client') as mock_client_factory, \
         patch('algotrader_api.ingestion.rate_limit.get_global') as mock_rl:
        mock_client = MagicMock()
        mock_client.get_historical_bonds = MagicMock(return_value=new_candles)
        mock_client_factory.return_value = mock_client
        mock_rl.return_value.acquire = MagicMock()

        backfill_bonds_to_depth(target_days=30, conn=db_with_bonds)
        count_after_first = db_with_bonds.execute(
            "SELECT COUNT(*) FROM bars WHERE figi='BBG000BOND15'"
        ).fetchone()[0]

        # Second call with the SAME candles — must not duplicate
        backfill_bonds_to_depth(target_days=30, conn=db_with_bonds)
        count_after_second = db_with_bonds.execute(
            "SELECT COUNT(*) FROM bars WHERE figi='BBG000BOND15'"
        ).fetchone()[0]

    assert count_after_first == count_after_second
