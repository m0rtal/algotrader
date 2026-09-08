"""gRPC → plain-dict converters for the t-tech-investments SDK.

Lives in its own module (separate from `real_client.py`) so the wrapper
file stays focused on target dispatch and lazy import. Functions are
referenced by `real_client.py` via:

    from .real_client_convert import _share_to_dict, ...

The shape returned here is the contract consumed by
`ingestion.universe.upsert_instruments()` and `ingestion.bars.run_bars_phase()`.
Breaking these dict shapes breaks downstream code, so changes here should
be paired with a test that exercises a real faked instrument/candle.
"""
from __future__ import annotations

from datetime import date
from typing import Any


def _money(units: int, nano: int) -> float:
    """Convert MoneyValue.units + MoneyValue.nano to a float."""
    return units + nano / 1_000_000_000


def _quotation(units: int, nano: int) -> float:
    """Alias for `_money`; Tinkoff uses both names in different schemas."""
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


def _to_iso(d: date | str) -> str:
    """Convert date or ISO string to YYYY-MM-DD.

    Public for `real_client.py`: it calls this when building
    `GetCandlesRequest(from_=..., to=...)` so the wrapper can accept either
    a `datetime.date` or an already-formatted ISO string from upstream code.
    """
    if isinstance(d, str):
        return d
    return d.isoformat()
