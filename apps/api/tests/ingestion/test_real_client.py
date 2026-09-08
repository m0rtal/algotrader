"""Tests for the gRPC → dict converters used by RealTinkoffClient.

Converters live in `real_client_convert.py` now (separated from the wrapper
to keep the wrapper file focused on target dispatch). The wrapper itself
is covered by the mocked SDK tests below — see test_real_client.py for the
SDK-shape tests and test_real_sandbox.py for the gated sandbox integration.
"""
from __future__ import annotations

from dataclasses import dataclass

from algotrader_api.ingestion.real_client_convert import (
    _acct_to_dict,
    _bond_to_dict,
    _candle_to_dict,
    _etf_to_dict,
    _future_to_dict,
    _money,
    _option_to_dict,
    _quotation,
    _share_to_dict,
    _to_iso_date,
)


@dataclass
class FakeQuotation:
    units: int
    nano: int


@dataclass
class FakeDate:
    year: int
    month: int
    day: int


def test_money_combines_units_and_nano():
    assert _money(100, 0) == 100.0
    assert _money(0, 500_000_000) == 0.5
    assert _money(123, 456_789_012) == 123.456789012


def test_quotation_returns_float():
    assert _quotation(10, 250_000_000) == 10.25


def test_to_iso_date_formats_components():
    assert _to_iso_date(FakeDate(2024, 6, 15)) == "2024-06-15"
    # Should also work with plain strings / other objects
    assert _to_iso_date("2024-01-02") == "2024-01-02"


def test_share_to_dict_basic():
    fake = type(
        "FakeShare",
        (),
        {
            "ticker": "SBER",
            "figi": "BBG004730N88",
            "name": "Сбер Банк",
            "currency": "RUB",
            "lot": 10,
            "isin": "RU0009029540",
            "sector": "Financials",
        },
    )()
    result = _share_to_dict(fake)
    assert result == {
        "ticker": "SBER",
        "figi": "BBG004730N88",
        "class": "share",
        "name": "Сбер Банк",
        "currency": "RUB",
        "lot_size": 10,
        "isin": "RU0009029540",
        "sector": "Financials",
    }


def test_share_to_dict_handles_missing_optional_attrs():
    """If isin/sector are missing (SDK may not always populate them), defaults to None."""

    class MinimalShare:
        ticker = "X"
        figi = "F"
        name = "n"
        currency = "RUB"
        lot = 1

    result = _share_to_dict(MinimalShare())
    assert result["isin"] is None
    assert result["sector"] is None


def test_bond_etf_share_same_shape():
    inst = type("Inst", (), {"ticker": "B", "figi": "F", "name": "n", "currency": "USD", "lot": 1, "isin": None, "sector": None})()
    assert _bond_to_dict(inst)["class"] == "bond"
    assert _etf_to_dict(inst)["class"] == "etf"
    assert _share_to_dict(inst)["class"] == "share"


def test_future_sets_currency_to_rub():
    inst = type("F", (), {"ticker": "RI", "figi": "F_F", "name": "n", "lot": 1, "sector": None})()
    result = _future_to_dict(inst)
    assert result["class"] == "future"
    assert result["currency"] == "RUB"
    assert result["lot_size"] == 1
    assert result["isin"] is None


def test_option_has_lot_size_one():
    inst = type("O", (), {"ticker": "SI", "figi": "F_O", "name": "n", "sector": None})()
    result = _option_to_dict(inst)
    assert result["class"] == "option"
    assert result["lot_size"] == 1


def test_acct_to_dict_basic():
    acct = type(
        "A",
        (),
        {"id": "ACC-123", "name": "Main", "type": "ACCOUNT_TYPE_TINKOFF", "status": "ACCOUNT_STATUS_OPEN"},
    )()
    result = _acct_to_dict(acct)
    assert result["id"] == "ACC-123"
    assert result["name"] == "Main"
    assert result["type"] == "ACCOUNT_TYPE_TINKOFF"
    assert result["status"] == "ACCOUNT_STATUS_OPEN"


def test_candle_to_dict_with_quotations():
    candle = type(
        "C",
        (),
        {
            "time": FakeDate(2024, 3, 1),
            "open": FakeQuotation(280, 0),
            "high": FakeQuotation(285, 500_000_000),
            "low": FakeQuotation(278, 0),
            "close": FakeQuotation(282, 750_000_000),
            "volume": 1_500_000,
        },
    )()
    result = _candle_to_dict(candle)
    assert result["ts"] == "2024-03-01"
    assert result["open"] == 280.0
    assert result["high"] == 285.5
    assert result["low"] == 278.0
    assert result["close"] == 282.75
    assert result["volume"] == 1_500_000


def test_candle_to_dict_handles_none_quotations():
    candle = type(
        "C",
        (),
        {
            "time": FakeDate(2024, 3, 2),
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "volume": 0,
        },
    )()
    result = _candle_to_dict(candle)
    assert result["ts"] == "2024-03-02"
    # When quotation is None, we use 0.0 default
    assert result["open"] == 0.0
    assert result["high"] == 0.0
    assert result["low"] == 0.0
    assert result["close"] == 0.0
    assert result["volume"] == 0


def test_real_client_init_raises_on_missing_sdk(monkeypatch):
    """If t_tech.invest cannot be imported, RealTinkoffClient raises RuntimeError."""
    import builtins
    from algotrader_api.ingestion import real_client

    # Save and break the cached import
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "t_tech.invest" or name.startswith("t_tech.invest"):
            raise ImportError("simulated missing SDK")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with __import__("pytest").raises(RuntimeError, match="t-tech-investments SDK not installed"):
        real_client.RealTinkoffClient(token="t.fake")


def test_real_client_aclose_is_safe_without_open():
    """aclose() should be safe to call even if the client was never opened."""
    from algotrader_api.ingestion import real_client

    # RealTinkoffClient.__init__ may fail if SDK isn't installed; if so skip
    try:
        client = real_client.RealTinkoffClient(token="t.fake")
    except RuntimeError:
        __import__("pytest").skip("t-tech-investments SDK not installed")
    # Should not raise even if _client is None
    import asyncio

    asyncio.run(client.aclose())
