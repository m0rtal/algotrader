"""Tests for universe discovery and SQLite persistence."""
from __future__ import annotations

import pytest

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db import sqlite as sqlitedb
from algotrader_api.ingestion.fake_client import InMemoryTinkoffClient
from algotrader_api.ingestion import universe


@pytest.mark.asyncio
async def test_discover_universe_returns_all_classes():
    client = InMemoryTinkoffClient()
    client.set_shares([{"ticker": "SBER", "figi": "s1", "class": "share", "name": "n", "currency": "RUB", "lot_size": 10}])
    client.set_bonds([{"ticker": "RU000A0", "figi": "b1", "class": "bond", "name": "n", "currency": "RUB", "lot_size": 1}])
    client.set_etfs([{"ticker": "FXRU", "figi": "e1", "class": "etf", "name": "n", "currency": "RUB", "lot_size": 1}])
    client.set_futures([{"ticker": "RIU5", "figi": "f1", "class": "future", "name": "n", "currency": "RUB", "lot_size": 1}])
    client.set_options([{"ticker": "SIU5C", "figi": "o1", "class": "option", "name": "n", "currency": "RUB", "lot_size": 1}])

    rows = await universe.discover_universe(client)
    assert len(rows) == 5
    classes = {r["class"] for r in rows}
    assert classes == {"share", "bond", "etf", "future", "option"}


@pytest.mark.asyncio
async def test_discover_universe_partial_failure_continues():
    """If one class fails, others should still be returned."""
    client = InMemoryTinkoffClient()
    client.set_shares([{"ticker": "SBER", "figi": "s1", "class": "share", "name": "n", "currency": "RUB", "lot_size": 10}])
    # Don't set bonds — will return empty (not raise)
    client.set_etfs([{"ticker": "FXRU", "figi": "e1", "class": "etf", "name": "n", "currency": "RUB", "lot_size": 1}])

    rows = await universe.discover_universe(client)
    assert len(rows) == 2  # shares + etfs (bonds empty)


def test_upsert_instruments_inserts_rows(tmp_path):
    db_path = str(tmp_path / "test.db")
    sqlitedb.run_migrations(db_path, MIGRATIONS_DIR)
    rows = [
        {"ticker": "SBER", "figi": "s1", "class": "share", "name": "n", "currency": "RUB", "lot_size": 10},
        {"ticker": "GAZP", "figi": "g1", "class": "share", "name": "n", "currency": "RUB", "lot_size": 10},
    ]
    n = universe.upsert_instruments(db_path, rows)
    assert n == 2
    result = sqlitedb.execute(db_path, "SELECT ticker FROM instruments ORDER BY ticker")
    assert [r["ticker"] for r in result] == ["GAZP", "SBER"]


def test_upsert_instruments_replaces_existing(tmp_path):
    db_path = str(tmp_path / "test.db")
    sqlitedb.run_migrations(db_path, MIGRATIONS_DIR)
    universe.upsert_instruments(
        db_path,
        [{"ticker": "SBER", "figi": "old", "class": "share", "name": "old", "currency": "RUB", "lot_size": 10}],
    )
    universe.upsert_instruments(
        db_path,
        [{"ticker": "SBER", "figi": "new", "class": "share", "name": "new", "currency": "RUB", "lot_size": 1}],
    )
    result = sqlitedb.execute(db_path, "SELECT figi, name, lot_size FROM instruments WHERE ticker = 'SBER'")
    assert len(result) == 1
    assert result[0]["figi"] == "new"
    assert result[0]["name"] == "new"
    assert result[0]["lot_size"] == 1


def test_upsert_instruments_empty_returns_zero(tmp_path):
    db_path = str(tmp_path / "test.db")
    sqlitedb.run_migrations(db_path, MIGRATIONS_DIR)
    assert universe.upsert_instruments(db_path, []) == 0
