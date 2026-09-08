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

import datetime
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
    # t-tech SDK Option has `uid` + `position_uid`, not `figi`.
    # Map to the same downstream shape so the rest of the pipeline
    # (instruments table, universe filtering) stays unchanged.
    figi = getattr(o, "figi", None) or getattr(o, "uid", None) or getattr(o, "position_uid", None)
    ticker = getattr(o, "ticker", None) or figi
    name = getattr(o, "name", None) or ticker
    return {
        "ticker": ticker,
        "figi": figi,
        "class": "option",
        "name": name,
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
    """Convert one gRPC candle (or pre-converted dict) to a parquet row.

    The new SDK's services (`MarketDataService.get_candles()` and friends)
    return already-decoded dataclass objects — but `candle.time` is no
    longer a nested google.type.Date; it's a top-level field. We accept
    either shape (raw gRPC or pre-decoded dataclass / dict) so the
    helper is robust against future SDK refactors.

    Drops candles whose date is today or later — those are the
    in-progress live bar that Tinkoff occasionally returns even with
    is_complete=True. The on-disk `last_bar_ts` should always be
    strictly < today.
    """
    # Time field: dict-style or gRPC-style.
    # Fast path: t-tech SDK hands back {"ts": "YYYY-MM-DD", ...} directly.
    if isinstance(c, dict) and c.get("ts"):
        try:
            a_date = date.fromisoformat(c["ts"][:10])
        except (TypeError, ValueError):
            a_date = None
        if a_date and a_date < date.today():
            def _maybe_num(x: Any) -> float:
                if isinstance(x, (int, float)):
                    return float(x)
                if isinstance(x, dict):
                    return float(x.get("units", 0)) + float(x.get("nano", 0)) / 1e9
                return float(x) if x is not None else 0.0

            return {
                "ts": a_date.isoformat(),
                "open": _maybe_num(c.get("open")),
                "high": _maybe_num(c.get("high")),
                "low": _maybe_num(c.get("low")),
                "close": _maybe_num(c.get("close")),
                "volume": int(c.get("volume") or 0),
            }
        # Today or future — drop.
        return None  # type: ignore[return-value]

    yr = mo = dy = None
    if isinstance(c, dict):
        t = c.get("time") or c.get("time_") or {}
        if isinstance(t, dict):
            yr, mo, dy = t.get("year", 1970), t.get("month", 1), t.get("day", 1)
        else:
            yr = getattr(t, "year", 1970)
            mo = getattr(t, "month", 1)
            dy = getattr(t, "day", 1)
        o = c.get("open", c.get("o", 0)) or 0
        h = c.get("high", c.get("h", 0)) or 0
        l = c.get("low", c.get("l", 0)) or 0
        cl = c.get("close", c.get("c", 0)) or 0
        volume = c.get("volume", c.get("v", 0)) or 0
    else:
        t = c.time
        o = c.open
        h = c.high
        l = c.low
        cl = c.close
        volume = getattr(c, "volume", 0)
        yr = getattr(t, "year", 1970)
        mo = getattr(t, "month", 1)
        dy = getattr(t, "day", 1)

    try:
        a_date = date(yr, mo, dy)
    except (TypeError, ValueError):
        return None  # type: ignore[return-value]
    if a_date >= date.today():
        return None  # type: ignore[return-value]

    def _q(quotation: Any) -> float:
        if quotation is None:
            return 0.0
        # gRPC MoneyValue/Quotation object or dict
        if isinstance(quotation, dict):
            return float(quotation.get("units", 0)) + float(quotation.get("nano", 0)) / 1e9
        units = getattr(quotation, "units", 0)
        nano = getattr(quotation, "nano", 0)
        return float(units) + float(nano) / 1e9

    return {
        "ts": a_date.isoformat(),
        "open": _q(o),
        "high": _q(h),
        "low": _q(l),
        "close": _q(cl),
        "volume": volume,
    }


def _to_iso_date(d: Any) -> str:
    """Convert google.type.Date or datetime.date to YYYY-MM-DD."""
    if hasattr(d, "year") and hasattr(d, "month") and hasattr(d, "day"):
        return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"
    return str(d)


def _to_datetime(d: datetime.date | str) -> datetime.datetime:
    """Convert a date or ISO date string to a timezone-naive datetime.

    The t-tech-investments SDK's `get_candles(from_=..., to=...)` accepts
    datetime objects (not ISO strings) for these fields; passing a string
    makes it call `.timestamp()` on the str and explode.
    """
    if isinstance(d, str):
        return datetime.datetime.fromisoformat(d)
    return datetime.datetime(d.year, d.month, d.day)
