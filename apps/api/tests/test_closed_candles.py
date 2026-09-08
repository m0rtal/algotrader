"""Tests for the is_closed_candle filter.

Closed bars are the only kind the runner writes to parquet. An open bar
(the in-progress current-day candle in Tinkoff's stream) would corrupt
historical analytics because it's a partial price snapshot.

The SDK exposes `candle.is_complete` (a protobuf bool field). The wrapper
centralises the rule here so a future rename touches one file, not
every writer.
"""
from __future__ import annotations

from types import SimpleNamespace

from algotrader_api.ingestion.closed_candles import is_closed_candle


def test_closed_candle_with_is_complete_true_returns_true():
    assert is_closed_candle(SimpleNamespace(is_complete=True)) is True


def test_open_candle_with_is_complete_false_returns_false():
    assert is_closed_candle(SimpleNamespace(is_complete=False)) is False


def test_candle_missing_is_complete_attribute_returns_true():
    """Defensive: if the SDK field is absent (older version, mock), assume
    the candle is closed and write it. The runner filters downstream by
    `candle.time < today_utc` as a safety net."""
    assert is_closed_candle(SimpleNamespace()) is True


def test_candle_with_is_complete_none_returns_false():
    """Explicit None (proto3 unset boolean) means the bar was emitted but
    not finalised by the server — drop it."""
    assert is_closed_candle(SimpleNamespace(is_complete=None)) is False
