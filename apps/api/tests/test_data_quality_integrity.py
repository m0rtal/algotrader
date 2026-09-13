"""Tests for bar-level integrity validation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

from algotrader_api.data_quality.integrity import (
    IntegrityViolation,
    Rule,
    bar_ts,
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


def test_validate_bar_accepts_quotation_dict_shape():
    """Raw gRPC serialised as dicts carries `{units, nano}` per price."""
    bar = {
        "ts": "2025-09-12",
        "open":  {"units": 100, "nano": 0},
        "high":  {"units": 110, "nano": 500_000_000},
        "low":   {"units": 95,  "nano": 0},
        "close": {"units": 105, "nano": 0},
        "volume": 1000,
    }
    assert validate_bar(bar) == []


def test_validate_bar_accepts_quotation_dataclass_shape():
    """Raw gRPC response objects carry `.units`/`.nano` attrs."""
    bar = SimpleNamespace(
        time=SimpleNamespace(year=2025, month=9, day=12),
        open=SimpleNamespace(units=100, nano=0),
        high=SimpleNamespace(units=110, nano=500_000_000),
        low=SimpleNamespace(units=95, nano=0),
        close=SimpleNamespace(units=105, nano=0),
        volume=1000,
    )
    assert validate_bar(bar) == []


def test_validate_bar_accepts_ts_via_time_attrs():
    """`ts` may come in as nested `time.year/month/day` instead of a flat field."""
    bar = SimpleNamespace(
        time=SimpleNamespace(year=2025, month=9, day=12),
        open=SimpleNamespace(units=100, nano=0),
        high=SimpleNamespace(units=110, nano=0),
        low=SimpleNamespace(units=95, nano=0),
        close=SimpleNamespace(units=105, nano=0),
        volume=1000,
    )
    violations = validate_bar(bar)
    assert violations == []


def test_validate_bar_flags_missing_numeric_field():
    """A field that isn't a number and isn't a Quotation shape trips MISSING_FIELD."""
    bar = {
        "ts": "2025-09-12",
        "open": "not-a-number",
        "high": 110,
        "low": 95,
        "close": 105,
        "volume": 1000,
    }
    violations = validate_bar(bar)
    assert any(v.rule == Rule.MISSING_FIELD for v in violations)


def test_validate_bar_flags_missing_volume():
    """volume=None (or unparseable) trips MISSING_FIELD rather than crashing."""
    bar = {
        "ts": "2025-09-12",
        "open": 100, "high": 110, "low": 95, "close": 105,
        "volume": None,
    }
    violations = validate_bar(bar)
    assert any(v.rule == Rule.MISSING_FIELD for v in violations)


def test_bar_ts_returns_iso_string_for_flat_ts():
    assert bar_ts({"ts": "2025-09-12"}) == "2025-09-12"


def test_bar_ts_returns_iso_string_for_date_object():
    assert bar_ts({"ts": date(2025, 9, 12)}) == "2025-09-12"


def test_bar_ts_returns_iso_string_for_time_attrs():
    bar = SimpleNamespace(time=SimpleNamespace(year=2025, month=9, day=12))
    assert bar_ts(bar) == "2025-09-12"


def test_bar_ts_returns_none_when_no_date():
    assert bar_ts({}) is None
    assert bar_ts(SimpleNamespace()) is None