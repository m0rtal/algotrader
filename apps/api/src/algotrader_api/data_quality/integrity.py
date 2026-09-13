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


def _get(c: Any, key: str) -> Any:
    if isinstance(c, Mapping):
        return c.get(key)
    return getattr(c, key, None)


def validate_bar(bar: Any) -> list[IntegrityViolation]:
    out: list[IntegrityViolation] = []
    for field in REQUIRED_FIELDS:
        if _get(bar, field) is None:
            out.append(IntegrityViolation(Rule.MISSING_FIELD, f"missing field: {field}"))
    if out:
        return out
    o, h, l, c, v = (
        float(_get(bar, "open")), float(_get(bar, "high")),
        float(_get(bar, "low")),  float(_get(bar, "close")),
        int(_get(bar, "volume")),
    )
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