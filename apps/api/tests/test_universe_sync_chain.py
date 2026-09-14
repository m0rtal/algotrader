"""Tests for algotrader_api.ingestion.universe_sync chain wrapper."""
import sqlite3
import pathlib
from unittest.mock import AsyncMock

import pytest

from algotrader_api.ingestion.universe_sync import run_universe_sync


class FakeClient:
    def __init__(self, shares, etfs, bonds):
        self._shares = shares
        self._etfs = etfs
        self._bonds = bonds

    async def get_shares(self):
        return [
            {"figi": "F1", "ticker": "GAZP", "class": "share",
             "name": "Gazprom", "currency": "RUB", "lot_size": 10},
        ]

    async def get_etfs(self):
        return [
            {"figi": "F2", "ticker": "FXRL", "class": "etf",
             "name": "FinEx ETF", "currency": "RUB", "lot_size": 1},
        ]

    async def get_bonds(self):
        return []

    async def get_futures(self):
        raise AssertionError("futures must not be called")

    async def get_options(self):
        return []


@pytest.mark.asyncio
async def test_run_universe_sync_inserts_tradeable_instruments(tmp_path: pathlib.Path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE instruments ("
        "  ticker TEXT PRIMARY KEY, figi TEXT NOT NULL UNIQUE,"
        "  class TEXT NOT NULL, name TEXT NOT NULL,"
        "  currency TEXT NOT NULL, lot_size INTEGER NOT NULL,"
        "  isin TEXT, sector TEXT);"
    )
    conn.commit()
    conn.close()

    client = FakeClient(shares=[], etfs=[], bonds=[])
    count = await run_universe_sync(str(db), client)
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT figi, class FROM instruments ORDER BY figi").fetchall()
    conn.close()
    assert rows == [("F1", "share"), ("F2", "etf")]
    assert count == 2
