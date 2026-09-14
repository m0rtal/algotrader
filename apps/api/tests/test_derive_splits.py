"""Tests for derive_splits: detect historical splits from local bars."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from algotrader_api.scripts_import import derive_splits


class _Bar:
    """Minimal bar shape for tests."""
    __slots__ = ("ts", "close", "volume")

    def __init__(self, ts: date, close: float, volume: int):
        self.ts = ts
        self.close = close
        self.volume = volume


# --------------------------------------------------------------------------- #
# Threshold rule
# --------------------------------------------------------------------------- #


def test_derive_ignores_below_threshold_change():
    """A 1.4× ratio is ordinary price movement — not a split."""
    base = date(2024, 1, 1)
    bars = [
        _Bar(base, 100, 1_000),
        _Bar(base + timedelta(days=1), 140, 1_000),  # ratio 1.4 — below 2.0
    ]
    rows = derive_splits.derive_splits_for_figi(
        bars=bars, face_value=None, figi="X"
    )
    assert rows == []


def test_derive_ignores_sub_threshold_reverse_change():
    """A 1.5× reverse-style jump (1.0 → 1.5) is below threshold."""
    base = date(2024, 1, 1)
    bars = [
        _Bar(base, 100, 1_000),
        _Bar(base + timedelta(days=1), 150, 1_000),  # ratio 1.5 — below 2.0
    ]
    rows = derive_splits.derive_splits_for_figi(
        bars=bars, face_value=None, figi="X"
    )
    assert rows == []


# --------------------------------------------------------------------------- #
# Single split
# --------------------------------------------------------------------------- #


def test_derive_detects_single_2_for_1_split():
    """Price divides by 2 → ratio 0.5 → factor 2.0."""
    base = date(2024, 1, 1)
    bars = [
        _Bar(base, 100, 1_000),
        _Bar(base + timedelta(days=1), 50, 1_000),
        _Bar(base + timedelta(days=2), 51, 1_000),
    ]
    rows = derive_splits.derive_splits_for_figi(
        bars=bars, face_value=None, figi="X"
    )
    assert len(rows) == 1
    assert rows[0].figi == "X"
    assert rows[0].action_type == "split"
    assert rows[0].ex_date == base + timedelta(days=1)
    assert rows[0].factor == 2.0
    assert rows[0].source.startswith("derived:bars+facevalue:")


def test_derive_detects_4_for_1_split():
    """YNDX-style 4:1 split."""
    base = date(2014, 6, 17)
    bars = [
        _Bar(base, 100, 1_000),
        _Bar(base + timedelta(days=1), 25, 1_000),
    ]
    rows = derive_splits.derive_splits_for_figi(
        bars=bars, face_value=None, figi="YNDX"
    )
    assert len(rows) == 1
    assert rows[0].factor == 4.0


def test_derive_detects_5000_for_1_reverse_split():
    """VTB 2024-07-11 consolidation: price ×5000, volume unchanged."""
    base = date(2024, 7, 10)
    bars = [
        _Bar(base, 0.00003, 100_000_000),  # pre-consolidation: tiny price
        _Bar(base + timedelta(days=1), 0.15, 100_000_000),  # post: 5000×
    ]
    rows = derive_splits.derive_splits_for_figi(
        bars=bars, face_value=None, figi="VTBR"
    )
    assert len(rows) == 1
    assert rows[0].factor == pytest.approx(5000.0, rel=0.01)


# --------------------------------------------------------------------------- #
# Reverse split vs bonus issue
# --------------------------------------------------------------------------- #


def test_derive_detects_reverse_split_when_volume_unchanged():
    """Reverse split leaves volume unchanged; BONU scales volume."""
    base = date(2024, 1, 1)
    bars = [
        _Bar(base, 5, 1_000),                  # pre
        _Bar(base + timedelta(days=1), 50, 1_000),  # ×10 price, vol unchanged
    ]
    rows = derive_splits.derive_splits_for_figi(
        bars=bars, face_value=None, figi="Y"
    )
    assert len(rows) == 1
    assert rows[0].factor == 10.0


def test_derive_discriminates_bonus_issue_from_reverse_split():
    """BONU: volume scales proportionally → NOT a split."""
    base = date(2024, 1, 1)
    bars = [
        _Bar(base, 100, 1_000),
        _Bar(base + timedelta(days=1), 10, 10_000),  # ×10 price, ×10 volume
    ]
    rows = derive_splits.derive_splits_for_figi(
        bars=bars, face_value=None, figi="V"
    )
    assert rows == []


# --------------------------------------------------------------------------- #
# Multiple splits
# --------------------------------------------------------------------------- #


def test_derive_detects_multiple_splits_in_history():
    """Two splits, same ticker — both detected."""
    base = date(2020, 1, 1)
    bars = [
        _Bar(base, 100, 1_000),
        _Bar(base + timedelta(days=1), 25, 1_000),    # 4:1 split
        _Bar(base + timedelta(days=2), 25, 1_000),    # unchanged
        _Bar(base + timedelta(days=3), 12.5, 1_000),  # 2:1 split
    ]
    rows = derive_splits.derive_splits_for_figi(
        bars=bars, face_value=None, figi="W"
    )
    assert len(rows) == 2
    factors = sorted(r.factor for r in rows)
    assert factors == [2.0, 4.0]


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #


def test_derive_handles_empty_bars():
    rows = derive_splits.derive_splits_for_figi(
        bars=[], face_value=None, figi="EMPTY"
    )
    assert rows == []


def test_derive_handles_single_bar():
    base = date(2024, 1, 1)
    rows = derive_splits.derive_splits_for_figi(
        bars=[_Bar(base, 100, 1_000)], face_value=None, figi="SINGLE"
    )
    assert rows == []


def test_derive_handles_zero_pre_split_volume():
    """If pre-split volume is 0, BONU discrimination is undefined — fall back
    to assuming the change is a real reverse split, since BONU at zero volume
    is nonsensical."""
    base = date(2024, 1, 1)
    bars = [
        _Bar(base, 5, 0),                            # zero volume (no trades)
        _Bar(base + timedelta(days=1), 50, 1_000),   # ×10 price
    ]
    rows = derive_splits.derive_splits_for_figi(
        bars=bars, face_value=None, figi="Z"
    )
    assert len(rows) == 1
    assert rows[0].factor == 10.0


def test_derive_threshold_is_hardcoded_at_2x():
    """The 2× threshold is a constant — operator decision 2026-09-13."""
    assert derive_splits.SPLIT_RATIO_THRESHOLD == 2.0


# --------------------------------------------------------------------------- #
# End-to-end: run_derivation on a temp DB
# --------------------------------------------------------------------------- #


def test_run_derivation_writes_split_rows(tmp_path):
    """End-to-end: fixture DB with a 2:1 split in bars + run_derivation
    → corporate_actions has one row with factor=2.0 and a derived source."""
    import sqlite3

    db_path = tmp_path / "test.db"
    # Set up schema (mirrors migration 007).
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE bars (
            figi TEXT, ts TEXT, open REAL, high REAL,
            low REAL, close REAL, volume INTEGER,
            UNIQUE (figi, ts)
        );
        CREATE TABLE corporate_actions (
            figi TEXT, action_type TEXT, ex_date TEXT,
            factor REAL, cash_amount REAL,
            note TEXT, source TEXT,
            PRIMARY KEY (figi, action_type, ex_date)
        );
        """
    )
    # 2:1 split at t=2024-01-02.
    conn.executemany(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("X", "2024-01-01", 100, 105, 99, 100, 1_000),
            ("X", "2024-01-02", 50, 52, 49, 50, 1_000),
            ("X", "2024-01-03", 51, 53, 50, 51, 1_000),
        ],
    )
    conn.commit()
    conn.close()

    written = derive_splits.run_derivation(str(db_path))
    assert written == 1

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT figi, action_type, ex_date, factor, source "
        "FROM corporate_actions"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "X"
    assert rows[0][1] == "split"
    assert rows[0][2] == "2024-01-02"
    assert rows[0][3] == 2.0
    assert rows[0][4].startswith("derived:bars+facevalue:")
    conn.close()


def test_run_derivation_is_idempotent(tmp_path):
    """Re-running on the same DB writes nothing the second time."""
    import sqlite3

    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE bars (
            figi TEXT, ts TEXT, open REAL, high REAL,
            low REAL, close REAL, volume INTEGER,
            UNIQUE (figi, ts)
        );
        CREATE TABLE corporate_actions (
            figi TEXT, action_type TEXT, ex_date TEXT,
            factor REAL, cash_amount REAL,
            note TEXT, source TEXT,
            PRIMARY KEY (figi, action_type, ex_date)
        );
        """
    )
    conn.executemany(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("X", "2024-01-01", 100, 105, 99, 100, 1_000),
            ("X", "2024-01-02", 50, 52, 49, 50, 1_000),
        ],
    )
    conn.commit()
    conn.close()

    written1 = derive_splits.run_derivation(str(db_path))
    written2 = derive_splits.run_derivation(str(db_path))
    assert written1 == 1
    assert written2 == 0


# --------------------------------------------------------------------------- #
# MOEX ISS lookup (network-dependent — marked to skip on offline)
# --------------------------------------------------------------------------- #


def test_lookup_face_values_handles_missing_secid():
    """A 404 / network error returns empty dict (no exception)."""
    out = derive_splits.lookup_face_values(
        {"NONEXISTENT_FIGI": "DEFINITELY_NOT_A_REAL_SECID_XYZ123"},
        timeout=1.0,
    )
    assert out == {}
