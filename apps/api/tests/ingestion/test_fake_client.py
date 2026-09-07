"""Tests for InMemoryTinkoffClient — records calls, returns canned responses."""
from __future__ import annotations

import threading

import pytest

from algotrader_api.ingestion.client import TinkoffClient
from algotrader_api.ingestion.fake_client import InMemoryTinkoffClient


@pytest.mark.asyncio
async def test_fake_client_is_protocol_compatible():
    client = InMemoryTinkoffClient()
    # Protocol check at runtime
    assert isinstance(client, TinkoffClient)


@pytest.mark.asyncio
async def test_set_and_get_shares():
    client = InMemoryTinkoffClient()
    shares = [
        {"ticker": "SBER", "figi": "BBG004730N88", "class": "share", "name": "Sber", "currency": "RUB", "lot_size": 10},
        {"ticker": "GAZP", "figi": "BBG004730RP0", "class": "share", "name": "Gazprom", "currency": "RUB", "lot_size": 10},
    ]
    client.set_shares(shares)
    result = await client.get_shares()
    assert result == shares
    assert client.call_count("get_shares") == 1
    assert client.calls_for("get_shares") == [()]


@pytest.mark.asyncio
async def test_set_all_instrument_classes():
    client = InMemoryTinkoffClient()
    client.set_bonds([{"ticker": "RU000A0", "figi": "F1", "class": "bond", "name": "x", "currency": "RUB", "lot_size": 1}])
    client.set_etfs([{"ticker": "FXRU", "figi": "F2", "class": "etf", "name": "y", "currency": "RUB", "lot_size": 1}])
    client.set_futures([{"ticker": "RIU5", "figi": "F3", "class": "future", "name": "z", "currency": "RUB", "lot_size": 1}])
    client.set_options([{"ticker": "SIU5C", "figi": "F4", "class": "option", "name": "w", "currency": "RUB", "lot_size": 1}])
    assert len(await client.get_bonds()) == 1
    assert len(await client.get_etfs()) == 1
    assert len(await client.get_futures()) == 1
    assert len(await client.get_options()) == 1


@pytest.mark.asyncio
async def test_get_candles_records_args():
    client = InMemoryTinkoffClient()
    client.set_candles(
        "BBG004730N88",
        [
            {"ts": "2024-01-02", "open": 280.0, "high": 285.0, "low": 278.0, "close": 282.0, "volume": 1_000_000},
        ],
    )
    result = await client.get_candles(
        figi="BBG004730N88", date_from="2024-01-01", date_to="2024-01-05"
    )
    assert len(result) == 1
    assert client.calls_for("get_candles") == [
        ("BBG004730N88", "2024-01-01", "2024-01-05", "CANDLE_INTERVAL_DAY"),
    ]


@pytest.mark.asyncio
async def test_get_candles_for_unknown_figi_returns_empty():
    client = InMemoryTinkoffClient()
    result = await client.get_candles(
        figi="UNKNOWN", date_from="2024-01-01", date_to="2024-01-05"
    )
    assert result == []


@pytest.mark.asyncio
async def test_aclose_sets_closed_flag():
    client = InMemoryTinkoffClient()
    assert not client.is_closed()
    await client.aclose()
    assert client.is_closed()


@pytest.mark.asyncio
async def test_thread_safe_preload():
    """Test that preloading data from one thread is visible from another."""
    import threading

    client = InMemoryTinkoffClient()
    loaded = []
    event = threading.Event()

    def loader():
        client.set_shares([{"ticker": "X", "figi": "F", "class": "share", "name": "n", "currency": "RUB", "lot_size": 1}])
        event.set()

    t = threading.Thread(target=loader)
    t.start()
    event.wait(timeout=2.0)
    t.join(timeout=2.0)

    result = await client.get_shares()
    assert len(result) == 1
    assert result[0]["ticker"] == "X"
    loaded.append(result)
    assert len(loaded) == 1


@pytest.mark.asyncio
async def test_get_accounts_returns_storage_snapshot():
    """get_accounts returns the configured account list and records the call."""
    client = InMemoryTinkoffClient()
    result = await client.get_accounts()
    # Storage has no set_accounts API, so list is always empty on default client.
    assert result == []
    # But the call is recorded.
    assert client.call_count("get_accounts") == 1


@pytest.mark.asyncio
async def test_get_accounts_empty_returns_empty_list():
    """get_accounts on empty client returns []."""
    client = InMemoryTinkoffClient()
    result = await client.get_accounts()
    assert result == []
