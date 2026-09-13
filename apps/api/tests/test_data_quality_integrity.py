"""Tests for bar-level integrity validation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from algotrader_api.data_quality.integrity import (
    IntegrityViolation,
    Rule,
    validate_bar,
)


def test_validate_bar_happy_path():
    bar = {
        "ts": "2025-09-12", "open": 100, "high": 110,
        "low": 95, "close": 105, "volume": 1000,
    }
    assert validate_bar(bar) == []


def test_high_must_be_max_of_o_h_l_c():
    bar = {"ts": "2025-09-12", "open": 100, "high": 90,
           "low": 95, "close": 105, "volume": 1000}
    violations = validate_bar(bar)
    assert any(v.rule == Rule.HIGH_BELOW_O_H_L_C for v in violations)


def test_low_must_be_min_of_o_h_l_c():
    bar = {"ts": "2025-09-12", "open": 100, "high": 110,
           "low": 102, "close": 105, "volume": 1000}
    violations = validate_bar(bar)
    assert any(v.rule == Rule.LOW_ABOVE_O_H_L_C for v in violations)


def test_volume_non_negative():
    bar = {"ts": "2025-09-12", "open": 100, "high": 110,
           "low": 95, "close": 105, "volume": -1}
    violations = validate_bar(bar)
    assert any(v.rule == Rule.VOLUME_NEGATIVE for v in violations)


def test_close_nonzero():
    bar = {"ts": "2025-09-12", "open": 0, "high": 0,
           "low": 0, "close": 0, "volume": 0}
    violations = validate_bar(bar)
    assert any(v.rule == Rule.ALL_ZERO for v in violations)


def test_required_fields_present():
    bar = {"ts": "2025-09-12", "open": 100}  # missing fields
    violations = validate_bar(bar)
    assert any(v.rule == Rule.MISSING_FIELD for v in violations)


def test_validate_bar_accepts_dict_or_dataclass():
    """The validator must accept both SDK dicts and dataclass-like objects."""
    @dataclass
    class Candle:
        ts: date = date(2025, 9, 12)
        open: float = 100.0
        high: float = 110.0
        low: float = 95.0
        close: float = 105.0
        volume: int = 1000

    assert validate_bar(Candle()) == []


def test_rule_is_str_enum():
    """Rule values must be string-comparable (for log output) but a distinct type."""
    assert Rule.HIGH_BELOW_O_H_L_C == "high-below-o-h-l-c"
    assert Rule.HIGH_BELOW_O_H_L_C != Rule.LOW_ABOVE_O_H_L_C
    assert isinstance(Rule.HIGH_BELOW_O_H_L_C, str)


def test_integrity_violation_has_rule_and_message():
    bar = {"ts": "2025-09-12", "open": 100, "high": 110,
           "low": 95, "close": 105, "volume": -5}
    [violation] = validate_bar(bar)
    assert isinstance(violation, IntegrityViolation)
    assert violation.rule is Rule.VOLUME_NEGATIVE
    assert "volume" in violation.message.lower()