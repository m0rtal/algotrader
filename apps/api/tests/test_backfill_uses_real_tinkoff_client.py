"""RED test: backfill_bonds_to_depth must call get_candles on the production
client (RealTinkoffClient), NOT the non-existent get_historical_bonds.

This is the integration test for Requirement: Auto-recovery fetches bonds
via real Tinkoff API (Task B.2 in autonomous-data-pipeline/tasks.md).

The existing test_backfill_bonds_to_depth.py mocks get_historical_bonds,
which is the very bug we are fixing: that method does not exist on
RealTinkoffClient, so the mocked test passes while the production code
fails silently at runtime (RealTinkoffClient.get_historical_bonds → AttributeError).

This test instead stubs the client with a *real-shaped* object whose only
Tinkoff-side method is get_candles (matching RealTinkoffClient's API
surface). If the production code calls anything else (including the
non-existent get_historical_bonds), the test fails — proving that the
production path matches the real client.
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def db_with_bonds(tmp_path: Path):
    """In-memory SQLite with one bond that needs depth backfill.

    The ``instrument_metadata`` table is included so the
    coordinated bar writer (Task 2) can run its aggregate UPDATE
    without an ``OperationalError``. Production DBs run all
    migrations including 004 + 005b; this fixture mirrors that
    surface for the test.
    """
    con = sqlite3.connect(str(tmp_path / "test_red.db"))
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
        CREATE TABLE instrument_metadata (
            figi              TEXT PRIMARY KEY,
            last_bar_ts       TEXT,
            first_bar_ts      TEXT,
            last_backfilled_at TEXT,
            total_bars        INTEGER NOT NULL DEFAULT 0,
            last_run_status   TEXT,
            last_run_at       TEXT,
            last_error        TEXT
        );
        INSERT INTO instruments VALUES ('BBG000BONDX1', 'BONDX1', 'bond');
        INSERT INTO bars VALUES ('BBG000BONDX1', '2024-12-01', 100, 101, 99, 100, 1000, 'moex');
    """)
    yield con
    con.close()


class RealShapedTinkoffClient:
    """Stub that mirrors RealTinkoffClient's actual API surface.

    Critically: this class has NO `get_historical_bonds` attribute. If
    the production code tries to call it, Python raises AttributeError,
    and the test fails — proving the production path cannot rely on
    a method that doesn't exist on the real client.
    """

    def __init__(self):
        self.get_candles = self._async_stub
        self.get_calls: list[dict] = []

    async def _async_stub(self, *, figi: str, date_from, date_to, interval: str = "CANDLE_INTERVAL_DAY"):
        # Return shape matches RealTinkoffClient.get_candles (list of dicts)
        # with enough rows to push the bond past target_days.
        self.get_calls.append({"figi": figi, "date_from": str(date_from), "date_to": str(date_to)})
        return [
            {
                "figi": figi,
                "ts": (date.today() - timedelta(days=i + 1)).isoformat(),
                "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000,
            }
            for i in range(30)
        ]


def test_backfill_calls_get_candles_not_get_historical_bonds(db_with_bonds):
    """The production backfill_bonds_to_depth MUST call client.get_candles.

    Calling get_historical_bonds on the real client raises AttributeError
    (it does not exist on RealTinkoffClient) — see the existing
    bond_depth_backfill_error in the worker log.
    """
    from algotrader_api.ingestion.backfill import backfill_bonds_to_depth

    real_client = RealShapedTinkoffClient()

    # Patch make_client to return the real-shaped stub, AND patch
    # the async rate-limiter to be a no-op (so we don't need a live
    # event loop for acquire).
    async def no_acquire(*_args, **_kwargs):
        return None

    with patch('algotrader_api.ingestion.client.make_client') as mock_factory, \
         patch('algotrader_api.ingestion.rate_limit.get_global') as mock_rl:
        mock_factory.return_value = real_client
        mock_rl.return_value.acquire = no_acquire

        backfill_bonds_to_depth(target_days=30, conn=db_with_bonds)

    # Assert: get_candles was called for our bond.
    assert len(real_client.get_calls) == 1, (
        f"Expected 1 get_candles call, got {len(real_client.get_calls)}: "
        f"{real_client.get_calls}"
    )
    assert real_client.get_calls[0]["figi"] == "BBG000BONDX1"

    # Assert: 30 new bars were inserted via the dict-shaped response.
    new_count = db_with_bonds.execute(
        "SELECT COUNT(*) FROM bars WHERE figi='BBG000BONDX1'"
    ).fetchone()[0]
    assert new_count >= 30, f"Expected >= 30 bars, got {new_count}"


def test_backfill_handles_dict_shaped_response(db_with_bonds):
    """RealTinkoffClient.get_candles returns list[dict], not list[object].

    Existing tests use MagicMock candles with attribute access (.figi,
    .ts, .open). RealTinkoffClient returns dicts (via _candle_to_dict).
    Production code must work with dicts.
    """
    from algotrader_api.ingestion.backfill import backfill_bonds_to_depth

    real_client = RealShapedTinkoffClient()

    async def no_acquire(*_args, **_kwargs):
        return None

    with patch('algotrader_api.ingestion.client.make_client') as mock_factory, \
         patch('algotrader_api.ingestion.rate_limit.get_global') as mock_rl:
        mock_factory.return_value = real_client
        mock_rl.return_value.acquire = no_acquire

        # Should not raise AttributeError on .figi / .ts access on dicts
        result = backfill_bonds_to_depth(target_days=30, conn=db_with_bonds)

    # If the production code did .figi on a dict, this would have raised.
    # Verify bars were inserted and the count is right.
    assert result["bars_added"] >= 30
