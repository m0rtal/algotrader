"""Deterministic bar-level integrity rules.

Each rule produces a structured `IntegrityViolation` with a stable code.
The validator runs on every fetched candle before write — invalid bars are
logged and skipped, not written to the `bars` table.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class Rule(str, Enum):
    HIGH_BELOW_O_H_L_C = "high-below-o-h-l-c"
    LOW_ABOVE_O_H_L_C = "low-above-o-h-l-c"
    VOLUME_NEGATIVE = "volume-negative"
    ALL_ZERO = "all-zero"
    MISSING_FIELD = "missing-field"


REQUIRED_FIELDS = ("ts", "open", "high", "low", "close", "volume")


@dataclass
class IntegrityViolation:
    rule: Rule
    message: str


def _coerce_num(v: Any) -> float | None:
    """Coerce a bar field to a float, supporting Tinkoff Quotation shape.

    Tinkoff's raw gRPC candles carry `{units: int, nano: int}` for every
    price field — both as nested dicts and as dataclass-like attrs. The
    validator must accept the same shapes `bars_sqlite._row` does,
    otherwise it cannot run on production candle output.
    """
    if v is None:
        return None
    if isinstance(v, Mapping):
        if "units" in v or "nano" in v:
            return float(v.get("units", 0)) + float(v.get("nano", 0) or 0) / 1e9
        # Plain numeric mapping without units/nano — treat the mapping
        # itself as the numeric value (used by tests that build flat
        # dict rows like {"open": 100, ...}).
        try:
            return float(v.get("value", v))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
    units = getattr(v, "units", None)
    if units is not None or getattr(v, "nano", None) is not None:
        return float(units or 0) + float(getattr(v, "nano", 0) or 0) / 1e9
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _get(c: Any, key: str) -> Any:
    if isinstance(c, Mapping):
        return c.get(key)
    return getattr(c, key, None)


def _get_ts(bar: Any) -> str | None:
    """Extract the bar's date as a string, mirroring `bars_sqlite._row`."""
    raw = _get(bar, "ts")
    if raw is not None:
        if hasattr(raw, "isoformat"):
            return raw.isoformat()
        return str(raw)[:10]
    t = _get(bar, "time") or _get(bar, "time_") or {}
    if isinstance(t, Mapping):
        y, m, d = t.get("year"), t.get("month"), t.get("day")
    else:
        y, m, d = getattr(t, "year", None), getattr(t, "month", None), getattr(t, "day", None)
    if y and m and d:
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    return None


def validate_bar(bar: Any) -> list[IntegrityViolation]:
    out: list[IntegrityViolation] = []
    # `ts` may come in as a flat field (SDK MarketDataService output) or
    # as nested time.year/month/day attrs (raw gRPC output). Either is
    # sufficient — mirrors `bars_sqlite._row`'s tolerance so production
    # Tinkoff responses don't trip a spurious MISSING_FIELD.
    if _get_ts(bar) is None:
        out.append(IntegrityViolation(Rule.MISSING_FIELD, "missing field: ts"))
    for field in REQUIRED_FIELDS:
        if field == "ts":
            continue
        if _get(bar, field) is None:
            out.append(IntegrityViolation(Rule.MISSING_FIELD, f"missing field: {field}"))
    if out:
        return out
    o = _coerce_num(_get(bar, "open"))
    h = _coerce_num(_get(bar, "high"))
    l = _coerce_num(_get(bar, "low"))
    c = _coerce_num(_get(bar, "close"))
    if None in (o, h, l, c):
        out.append(IntegrityViolation(Rule.MISSING_FIELD, "missing numeric field"))
        return out
    v_raw = _get(bar, "volume")
    try:
        v = int(v_raw)
    except (TypeError, ValueError):
        out.append(IntegrityViolation(Rule.MISSING_FIELD, "missing field: volume"))
        return out
    if o == 0 and h == 0 and l == 0 and c == 0 and v == 0:
        out.append(IntegrityViolation(Rule.ALL_ZERO, "bar is all zero"))
    if h < max(o, l, c):
        out.append(IntegrityViolation(
            Rule.HIGH_BELOW_O_H_L_C,
            f"high={h} < max(o={o},l={l},c={c})",
        ))
    if l > min(o, h, c):
        out.append(IntegrityViolation(
            Rule.LOW_ABOVE_O_H_L_C,
            f"low={l} > min(o={o},h={h},c={c})",
        ))
    if v < 0:
        out.append(IntegrityViolation(
            Rule.VOLUME_NEGATIVE, f"volume={v} < 0",
        ))
    return out


def bar_ts(bar: Any) -> str | None:
    """Public helper: ISO-date of a bar (or None) — for log messages."""
    return _get_ts(bar)
