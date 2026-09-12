"""Tests for the tradeable-classes contract.

This is the central policy primitive for which asset classes the
ingest pipeline and the operator's dashboards care about. Every
layer that touches `instruments.class` must read from here.

A change to TRADEABLE_CLASSES is a spec change and a tests change.
"""
from __future__ import annotations


from algotrader_api.domain.tradeable import (
    TRADEABLE_CLASSES,
    filter_tradeable,
    is_tradeable,
)


def test_tradeable_classes_exact_set():
    """The set is exactly {share, etf, bond} and nothing else."""
    assert TRADEABLE_CLASSES == frozenset({"share", "etf", "bond"})


def test_is_tradeable_true_for_each_class():
    for c in ("share", "etf", "bond"):
        assert is_tradeable(c) is True


def test_is_tradeable_false_for_non_tradeable():
    for c in ("future", "option", "currency", "commodity", "index"):
        assert is_tradeable(c) is False


def test_is_tradeable_false_for_nullish():
    assert is_tradeable(None) is False
    assert is_tradeable("") is False


def test_filter_tradeable_drops_non_tradeable_keeps_order():
    rows = [
        {"ticker": "SBER", "class": "share"},
        {"ticker": "FUT1", "class": "future"},
        {"ticker": "OFZ", "class": "bond"},
        {"ticker": "OPT1", "class": "option"},
        {"ticker": "FXUS", "class": "etf"},
    ]
    assert filter_tradeable(rows) == [
        {"ticker": "SBER", "class": "share"},
        {"ticker": "OFZ", "class": "bond"},
        {"ticker": "FXUS", "class": "etf"},
    ]


def test_filter_tradeable_empty():
    assert filter_tradeable([]) == []


def test_filter_tradeable_handles_missing_class():
    """A row without a `class` field is treated as non-tradeable."""
    rows = [{"ticker": "X"}, {"ticker": "Y", "class": None}]
    assert filter_tradeable(rows) == []
