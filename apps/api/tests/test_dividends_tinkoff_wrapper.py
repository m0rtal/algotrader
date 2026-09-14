"""Tests for RealTinkoffClient.get_dividends + FakeClient.get_dividends.

Validates that the wrapper:
- Calls ``services.instruments.get_dividends`` with the right kwargs
- Converts each returned Dividend via ``_dividend_to_dict``
- Records call args on the fake client
"""
from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from algotrader_api.ingestion.real_client import RealTinkoffClient
from algotrader_api.ingestion.real_client_convert import _dividend_to_dict
from algotrader_api.ingestion.fake_client import InMemoryTinkoffClient


# ─── Fixtures ────────────────────────────────────────────────────────

def _money(units: int, nano: int) -> Any:
    return SimpleNamespace(units=units, nano=nano)


def _make_dividend(
    *,
    units: int = 10,
    nano: int = 0,
    last_buy_year: int = 2024,
    last_buy_month: int = 5,
    last_buy_day: int = 15,
    dividend_type: str = "REGULAR",
    regularity: str = "ANNUAL",
) -> Any:
    return SimpleNamespace(
        dividend_net=_money(units, nano),
        payment_date=datetime.datetime(last_buy_year, last_buy_month, last_buy_day + 5),
        declared_date=datetime.datetime(last_buy_year, last_buy_month - 1, 1),
        last_buy_date=datetime.datetime(last_buy_year, last_buy_month, last_buy_day),
        dividend_type=dividend_type,
        record_date=datetime.datetime(last_buy_year, last_buy_month, last_buy_day + 1),
        regularity=regularity,
        close_price=_money(100, 0),
        yield_value=_money(2, 500_000_000),  # 2.5
        created_at=datetime.datetime(last_buy_year, 1, 1, 12, 0, 0),
    )


_FAKE_FIGI = "BBG004730N88"
_FAKE_FROM = datetime.date(2024, 1, 1)
_FAKE_TO = datetime.date(2024, 12, 31)


@pytest.fixture
def fake_dividends() -> list[dict]:
    return [
        {
            "figi": _FAKE_FIGI,
            "dividend_id": f"{_FAKE_FIGI}:2024-05-15",
            "ex_date": "2024-05-15",
            "pay_date": "2024-05-20",
            "record_date": "2024-05-16",
            "declared_at": "2024-04-01T00:00:00",
            "currency": "rub",
            "amount_per_share": 12.5,
            "close_price": 100.0,
            "yield_value": 2.5,
            "dividend_type": "REGULAR",
            "regularity": "ANNUAL",
            "created_at": "2024-01-01T12:00:00",
        },
        {
            "figi": _FAKE_FIGI,
            "dividend_id": f"{_FAKE_FIGI}:2024-11-10",
            "ex_date": "2024-11-10",
            "pay_date": "2024-11-15",
            "record_date": "2024-11-11",
            "declared_at": "2024-10-01T00:00:00",
            "currency": "rub",
            "amount_per_share": 7.25,
            "close_price": 110.0,
            "yield_value": 1.8,
            "dividend_type": "INTERIM",
            "regularity": "SEMI_ANNUAL",
            "created_at": "2024-01-01T12:00:00",
        },
    ]


# ─── Real client: SDK invocation + conversion ────────────────────────


class _FakeServices:
    def __init__(self, dividends: list[Any]) -> None:
        self._dividends = dividends
        self.instruments = SimpleNamespace(
            get_dividends=self._get_dividends,
        )

    async def _get_dividends(self, **kwargs: Any) -> Any:
        # Record the call on the instance so the test can assert on kwargs.
        self.last_call_kwargs = kwargs
        return SimpleNamespace(dividends=list(self._dividends))


class _FakeAsyncClient:
    def __init__(self, services: _FakeServices) -> None:
        self._services = services

    async def __aenter__(self) -> _FakeServices:
        return self._services

    async def __aexit__(self, *args: Any) -> None:
        return None


async def test_real_client_get_dividends_calls_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wrapper invokes instruments.get_dividends with the expected kwargs
    and converts each Dividend via _dividend_to_dict.
    """
    sdk_dividend_1 = _make_dividend(units=12, nano=500_000_000, last_buy_day=15)
    sdk_dividend_2 = _make_dividend(
        units=7, nano=250_000_000, last_buy_month=11, last_buy_day=10,
        dividend_type="INTERIM", regularity="SEMI_ANNUAL",
    )
    services = _FakeServices([sdk_dividend_1, sdk_dividend_2])

    # Stub the SDK so we never hit the network.
    fake_sdk = SimpleNamespace(
        AsyncClient=lambda token, target: _FakeAsyncClient(services),
    )
    monkeypatch.setattr(
        "algotrader_api.ingestion.real_client.importlib.import_module",
        lambda name: fake_sdk if name == "t_tech.invest" else SimpleNamespace(
            INVEST_GRPC_API_SANDBOX="sandbox-test",
        ),
    )

    client = RealTinkoffClient(token="dummy-token", target="sandbox")
    rows = await client.get_dividends(_FAKE_FIGI, _FAKE_FROM, _FAKE_TO)

    # The SDK was called with the right kwargs.
    kwargs = services.last_call_kwargs
    assert kwargs["figi"] == _FAKE_FIGI
    assert kwargs["instrument_id"] == _FAKE_FIGI
    assert kwargs["from_"] == datetime.datetime(2024, 1, 1)
    assert kwargs["to"] == datetime.datetime(2024, 12, 31)

    # Each dividend was converted into a flat dict with our schema.
    assert len(rows) == 2
    first = rows[0]
    assert first["figi"] == _FAKE_FIGI
    assert first["dividend_id"] == f"{_FAKE_FIGI}:2024-05-15"
    assert first["ex_date"] == "2024-05-15"
    assert first["pay_date"] == "2024-05-20"
    assert first["record_date"] == "2024-05-16"
    assert first["currency"] == "rub"
    assert first["amount_per_share"] == pytest.approx(12.5)
    assert first["close_price"] == pytest.approx(100.0)
    assert first["yield_value"] == pytest.approx(2.5)
    assert first["dividend_type"] == "REGULAR"
    assert first["regularity"] == "ANNUAL"

    second = rows[1]
    assert second["dividend_id"] == f"{_FAKE_FIGI}:2024-11-10"
    assert second["amount_per_share"] == pytest.approx(7.25)
    assert second["dividend_type"] == "INTERIM"
    assert second["regularity"] == "SEMI_ANNUAL"


async def test_real_client_get_dividends_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wrapper returns [] when the SDK yields no dividends."""
    services = _FakeServices([])

    fake_sdk = SimpleNamespace(
        AsyncClient=lambda token, target: _FakeAsyncClient(services),
    )
    monkeypatch.setattr(
        "algotrader_api.ingestion.real_client.importlib.import_module",
        lambda name: fake_sdk if name == "t_tech.invest" else SimpleNamespace(
            INVEST_GRPC_API_SANDBOX="sandbox-test",
        ),
    )

    client = RealTinkoffClient(token="dummy-token", target="sandbox")
    rows = await client.get_dividends(_FAKE_FIGI, _FAKE_FROM, _FAKE_TO)
    assert rows == []


# ─── Fake client: storage + retrieval ────────────────────────────────


async def test_fake_client_get_dividends_returns_its_list(fake_dividends: list[dict]) -> None:
    """FakeClient.get_dividends returns its pre-loaded list verbatim
    and records the call args.
    """
    client = InMemoryTinkoffClient()
    client.set_dividends(_FAKE_FIGI, fake_dividends)

    rows = await client.get_dividends(_FAKE_FIGI, _FAKE_FROM, _FAKE_TO)

    assert rows == fake_dividends
    assert client.call_count("get_dividends") == 1
    assert client.calls_for("get_dividends") == [(_FAKE_FIGI, _FAKE_FROM, _FAKE_TO)]


async def test_fake_client_get_dividends_unknown_figi() -> None:
    """Unknown figi returns [] rather than raising."""
    client = InMemoryTinkoffClient()
    rows = await client.get_dividends("UNKNOWN", _FAKE_FROM, _FAKE_TO)
    assert rows == []
    assert client.call_count("get_dividends") == 1


# ─── _dividend_to_dict helper (direct, no SDK) ───────────────────────


def test_dividend_to_dict_direct() -> None:
    """_dividend_to_dict works on a raw Dividend-like object."""
    div = _make_dividend(units=12, nano=500_000_000)
    out = _dividend_to_dict(div, figi=_FAKE_FIGI)

    assert out["figi"] == _FAKE_FIGI
    assert out["dividend_id"] == f"{_FAKE_FIGI}:2024-05-15"
    assert out["amount_per_share"] == pytest.approx(12.5)
    assert out["yield_value"] == pytest.approx(2.5)
    assert out["ex_date"] == "2024-05-15"


def test_dividend_to_dict_handles_none_fields() -> None:
    """Defensive: None money/date fields must not crash."""
    div = SimpleNamespace(
        dividend_net=None,
        payment_date=None,
        declared_date=None,
        last_buy_date=None,
        record_date=None,
        created_at=None,
        dividend_type="",
        regularity="",
        close_price=None,
        yield_value=None,
    )
    out = _dividend_to_dict(div, figi=_FAKE_FIGI)
    assert out["amount_per_share"] == 0.0
    assert out["close_price"] == 0.0
    assert out["yield_value"] == 0.0
    assert out["ex_date"] == ""
    assert out["dividend_id"] == f"{_FAKE_FIGI}:"