"""Tests for universe discovery and SQLite persistence."""
from __future__ import annotations

import pytest

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db import sqlite as sqlitedb
from algotrader_api.ingestion.fake_client import InMemoryTinkoffClient
from algotrader_api.ingestion import universe


@pytest.mark.asyncio
async def test_discover_universe_returns_tradeable_classes_only():
    """`discover_universe` filters at the source — future/option
    rows set on the InMemoryTinkoffClient never reach the output,
    even though the SDK method is wired. This is the runtime
    contract that keeps the universe from drifting to 10k+ figs.
    """
    client = InMemoryTinkoffClient()
    client.set_shares([{"ticker": "SBER", "figi": "s1", "class": "share", "name": "n", "currency": "RUB", "lot_size": 10}])
    client.set_bonds([{"ticker": "RU000A0", "figi": "b1", "class": "bond", "name": "n", "currency": "RUB", "lot_size": 1}])
    client.set_etfs([{"ticker": "FXRU", "figi": "e1", "class": "etf", "name": "n", "currency": "RUB", "lot_size": 1}])
    # Non-tradeable rows are set on the client but must not appear
    # in the output (we never call get_futures / get_options).
    client.set_futures([{"ticker": "RIU5", "figi": "f1", "class": "future", "name": "n", "currency": "RUB", "lot_size": 1}])
    client.set_options([{"ticker": "SIU5C", "figi": "o1", "class": "option", "name": "n", "currency": "RUB", "lot_size": 1}])

    rows = await universe.discover_universe(client)
    assert len(rows) == 3
    classes = {r["class"] for r in rows}
    assert classes == {"share", "bond", "etf"}


@pytest.mark.asyncio
async def test_discover_universe_partial_failure_continues():
    """If one class fails, others should still be returned.

    Tradeable classes are shares/bonds/etfs; we leave bonds empty
    here so the count check stays accurate after the tradeable
    filter was added.
    """
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


def test_upsert_instruments_refreshes_metadata_for_existing_figi(tmp_path):
    """When the broker returns the same figi again with updated metadata
    (different name, lot_size, etc.), upsert_instruments must refresh the
    existing row. This covers cases like sector reclassification or
    lot_size changes after corporate actions.
    """
    db_path = str(tmp_path / "test.db")
    sqlitedb.run_migrations(db_path, MIGRATIONS_DIR)
    universe.upsert_instruments(
        db_path,
        [{"ticker": "SBER", "figi": "F1", "class": "share",
         "name": "old", "currency": "RUB", "lot_size": 10}],
    )
    universe.upsert_instruments(
        db_path,
        [{"ticker": "SBER", "figi": "F1", "class": "share",
         "name": "new", "currency": "RUB", "lot_size": 1}],
    )
    result = sqlitedb.execute(db_path, "SELECT figi, name, lot_size FROM instruments WHERE ticker = 'SBER'")
    assert len(result) == 1
    assert result[0]["figi"] == "F1"
    assert result[0]["name"] == "new"
    assert result[0]["lot_size"] == 1


def test_upsert_instruments_preserves_both_figis_for_duplicate_ticker(tmp_path):
    """When the broker returns TWO figis for the same ticker (relisted
    instruments), BOTH figis must coexist — neither replaces the other.

    PR #50's "older figi wins" rule was wrong because relisted figis
    carry the bars while the older figi has 0 bars (relisted = replaced).
    Dropping the bar-carrying figi silently destroyed data.

    The fix: drop PRIMARY KEY (ticker) so duplicate tickers can coexist.
    figi remains UNIQUE so each figi is a distinct row.
    """
    db_path = str(tmp_path / "test.db")
    sqlitedb.run_migrations(db_path, MIGRATIONS_DIR)
    universe.upsert_instruments(
        db_path,
        [{"ticker": "RU000A1057D4", "figi": "TCS00A1057D4", "class": "bond",
         "name": "Original", "currency": "rub", "lot_size": 1}],
    )
    universe.upsert_instruments(
        db_path,
        [{"ticker": "RU000A1057D4", "figi": "TCS90A1057D4", "class": "bond",
         "name": "Relisted", "currency": "rub", "lot_size": 1}],
    )
    result = sqlitedb.execute(
        db_path,
        "SELECT figi, ticker FROM instruments ORDER BY figi",
    )
    figis = [(r["figi"], r["ticker"]) for r in result]
    assert figis == [
        ("TCS00A1057D4", "RU000A1057D4"),
        ("TCS90A1057D4", "RU000A1057D4"),
    ], f"Duplicate-ticker figis were collapsed: {figis}"


def test_upsert_instruments_empty_returns_zero(tmp_path):
    db_path = str(tmp_path / "test.db")
    sqlitedb.run_migrations(db_path, MIGRATIONS_DIR)
    assert universe.upsert_instruments(db_path, []) == 0
