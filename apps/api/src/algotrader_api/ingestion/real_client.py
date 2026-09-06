"""Real Tinkoff client wrapping tinkoff-investments AsyncClient.

The SDK is imported lazily so unit tests don't need the package installed.
Production deployment installs it via the GitLab mirror index URL — see
add-data-fetch/proposal.md for the exact pin.
"""
from __future__ import annotations

import importlib
import logging
from typing import Any

from ..observability.logging import get_logger

logger = get_logger("algotrader_api.ingestion.real_client")


def _to_iso(d: date | str) -> str:
    """Convert date or ISO string to YYYY-MM-DD."""
    if isinstance(d, str):
        return d
    return d.isoformat()


class RealTinkoffClient:
    """Wraps tinkoff.invest.AsyncClient with our Protocol interface."""

    def __init__(self, *, token: str, target: str = "sandbox") -> None:
        try:
            self._sdk = importlib.import_module("tinkoff.invest")
        except ImportError as e:
            raise RuntimeError(
                "tinkoff-investments SDK not installed. "
                "Install from GitLab mirror or set ALGOTRADER_INGEST_FAKE=1 for dev."
            ) from e

        target_module = importlib.import_module("tinkoff.invest.constants")
        target_constant = getattr(target_module, f"INVEST_GRPC_API_{target.upper()}", None)
        if target_constant is None:
            raise ValueError(f"unknown target: {target} (use 'sandbox' or 'production')")
        self._target = target_constant
        self._token = token
        self._client: Any = None  # AsyncClient, opened lazily on first async call
        # Read INSTRUMENT_STATUS_BASE constant at init time
        self._status_base = getattr(
            importlib.import_module("tinkoff.invest"),
            "InstrumentStatus",
        ).INSTRUMENT_STATUS_BASE

    async def _ensure(self) -> Any:
        if self._client is None:
            AsyncClient = getattr(self._sdk, "AsyncClient")
            self._client = AsyncClient(self._token, target=self._target)
        return self._client

    async def get_accounts(self) -> list[dict]:
        client = await self._ensure()
        response = await client.users.get_accounts()
        return [_acct_to_dict(a) for a in response.accounts]

    async def get_shares(self) -> list[dict]:
        client = await self._ensure()
        response = await client.instruments.shares(
            instrument_status=self._status_base
        )
        return [_share_to_dict(s) for s in response.instruments]

    async def get_bonds(self) -> list[dict]:
        client = await self._ensure()
        response = await client.instruments.bonds(
            instrument_status=self._status_base
        )
        return [_bond_to_dict(b) for b in response.instruments]

    async def get_etfs(self) -> list[dict]:
        client = await self._ensure()
        response = await client.instruments.etfs(
            instrument_status=self._status_base
        )
        return [_etf_to_dict(e) for e in response.instruments]

    async def get_futures(self) -> list[dict]:
        client = await self._ensure()
        response = await client.instruments.futures(
            instrument_status=self._status_base
        )
        return [_future_to_dict(f) for f in response.instruments]

    async def get_options(self) -> list[dict]:
        client = await self._ensure()
        response = await client.instruments.options(
            instrument_status=self._status_base
        )
        return [_option_to_dict(o) for o in response.instruments]

    async def get_candles(
        self,
        *,
        figi: str,
        date_from: str | date,
        date_to: str | date,
        interval: str = "CANDLE_INTERVAL_DAY",
    ) -> list[dict]:
        client = await self._ensure()
        sdk = self._sdk
        CandleInterval = sdk.CandleInterval
        interval_enum = getattr(CandleInterval, interval, CandleInterval.CANDLE_INTERVAL_DAY)

        response = await client.market_data.get_candles(
            figi=figi,
            from_=_iso(date_from),
            to=_iso(date_to),
            interval=interval_enum,
        )
        return [_candle_to_dict(c) for c in response.candles]

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


# ─── gRPC → dict converters ─────────────────────────────────────────────
# The SDK uses google.type.MoneyValue for price and google.type.Date for dates.
# We coerce to plain Python types so downstream code can serialize to JSON/parquet.


def _money(units: int, nano: int) -> float:
    """Convert MoneyValue.units + MoneyValue.nano to a float."""
    return units + nano / 1_000_000_000


def _quotation(units: int, nano: int) -> float:
    return _money(units, nano)


def _share_to_dict(s: Any) -> dict:
    return {
        "ticker": s.ticker,
        "figi": s.figi,
        "class": "share",
        "name": s.name,
        "currency": s.currency,
        "lot_size": s.lot,
        "isin": getattr(s, "isin", None),
        "sector": getattr(s, "sector", None),
    }


def _bond_to_dict(b: Any) -> dict:
    return {
        "ticker": b.ticker,
        "figi": b.figi,
        "class": "bond",
        "name": b.name,
        "currency": b.currency,
        "lot_size": b.lot,
        "isin": getattr(b, "isin", None),
        "sector": getattr(b, "sector", None),
    }


def _etf_to_dict(e: Any) -> dict:
    return {
        "ticker": e.ticker,
        "figi": e.figi,
        "class": "etf",
        "name": e.name,
        "currency": e.currency,
        "lot_size": e.lot,
        "isin": getattr(e, "isin", None),
        "sector": getattr(e, "sector", None),
    }


def _future_to_dict(f: Any) -> dict:
    return {
        "ticker": f.ticker,
        "figi": f.figi,
        "class": "future",
        "name": f.name,
        "currency": "RUB",
        "lot_size": f.lot,
        "isin": None,
        "sector": getattr(f, "sector", None),
    }


def _option_to_dict(o: Any) -> dict:
    return {
        "ticker": o.ticker,
        "figi": o.figi,
        "class": "option",
        "name": o.name,
        "currency": "RUB",
        "lot_size": 1,
        "isin": None,
        "sector": None,
    }


def _acct_to_dict(a: Any) -> dict:
    return {
        "id": a.id,
        "name": getattr(a, "name", ""),
        "type": str(getattr(a, "type", "")),
        "status": str(getattr(a, "status", "")),
    }


def _candle_to_dict(c: Any) -> dict:
    o = c.open
    h = c.high
    l = c.low
    cl = c.close
    return {
        "ts": _to_iso_date(c.time),
        "open": _quotation(o.units, o.nano) if o else 0.0,
        "high": _quotation(h.units, h.nano) if h else 0.0,
        "low": _quotation(l.units, l.nano) if l else 0.0,
        "close": _quotation(cl.units, cl.nano) if cl else 0.0,
        "volume": getattr(c, "volume", 0),
    }


def _to_iso_date(d: Any) -> str:
    """Convert google.type.Date or datetime.date to YYYY-MM-DD."""
    if hasattr(d, "year") and hasattr(d, "month") and hasattr(d, "day"):
        return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"
    return str(d)
