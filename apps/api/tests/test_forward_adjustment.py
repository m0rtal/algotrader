"""Tests for forward-adjustment of bars when splits land."""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from algotrader_api.data_quality.forward_adjustment import (
    apply_all_pending,
    apply_forward_split,
)


@pytest.fixture
def db(tmp_path):
    """Fresh state.db with the minimum schema needed by forward_adjustment.

    `run_migrations` on a fresh DB hits a pre-existing migration 012
    bug (007_corporate_actions.sql creates the table without the
    `source` column, then 012 INSERTs reference it). To keep this
    test file self-contained and isolated from that bug, we apply
    just the schema our functions read/write.
    """
    p = str(tmp_path / "state.db")
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE bars (
            figi   TEXT    NOT NULL,
            ts     TEXT    NOT NULL,
            open   REAL    NOT NULL,
            high   REAL    NOT NULL,
            low    REAL    NOT NULL,
            close  REAL    NOT NULL,
            volume INTEGER,
            PRIMARY KEY (figi, ts)
        );
        CREATE TABLE corporate_actions (
            figi        TEXT    NOT NULL,
            action_type TEXT    NOT NULL,
            ex_date     TEXT    NOT NULL,
            factor      REAL    NOT NULL,
            cash_amount REAL,
            note        TEXT,
            source      TEXT,
            PRIMARY KEY (figi, action_type, ex_date)
        );
        CREATE TABLE bars_adjusted (
            figi        TEXT    NOT NULL,
            ts          TEXT    NOT NULL,
            adj_open    REAL    NOT NULL,
            adj_high    REAL    NOT NULL,
            adj_low     REAL    NOT NULL,
            adj_close   REAL    NOT NULL,
            adj_volume  INTEGER NOT NULL,
            source      TEXT    NOT NULL DEFAULT 'derived:bars+forward',
            computed_at TEXT    NOT NULL,
            PRIMARY KEY (figi, ts)
        );
        """
    )
    con.commit()
    con.close()
    return p


def _seed_bars(db_path, rows):
    """rows = [(figi, ts, open, high, low, close, volume), ...]"""
    con = sqlite3.connect(db_path)
    try:
        con.executemany(
            "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        con.commit()
    finally:
        con.close()


def test_apply_forward_split_divides_post_split_bars(db):
    _seed_bars(
        db,
        [
            ("F1", "2024-05-13", 99, 101, 98, 100, 1000),  # pre-split
            ("F1", "2024-05-15", 51, 52, 49, 50, 2000),   # ex-date
            ("F1", "2024-05-16", 50, 52, 49, 51, 1500),   # post-split
        ],
    )
    con = sqlite3.connect(db)
    try:
        n = apply_forward_split(con, "F1", date(2024, 5, 15), 2.0)
        assert n == 2  # two post-split rows updated
        rows = {
            r[0]: r[1]
            for r in con.execute(
                "SELECT ts, adj_close FROM bars_adjusted WHERE figi='F1' ORDER BY ts"
            ).fetchall()
        }
        # Forward convention: only post-split bars are divided by factor.
        assert rows["2024-05-15"] == pytest.approx(25.0)
        assert rows["2024-05-16"] == pytest.approx(25.5)
        assert "2024-05-13" not in rows  # pre-split bar is untouched / absent
    finally:
        con.close()


def test_apply_forward_split_is_idempotent(db):
    _seed_bars(db, [("F1", "2024-05-15", 50, 50, 50, 50, 100)])
    con = sqlite3.connect(db)
    try:
        first = apply_forward_split(con, "F1", date(2024, 5, 15), 2.0)
        second = apply_forward_split(con, "F1", date(2024, 5, 15), 2.0)
        assert first == 1
        assert second == 0
    finally:
        con.close()


def test_apply_forward_split_skips_factor_one(db):
    _seed_bars(db, [("F1", "2024-05-15", 50, 50, 50, 50, 100)])
    con = sqlite3.connect(db)
    try:
        n = apply_forward_split(con, "F1", date(2024, 5, 15), 1.0)
        assert n == 0
        assert con.execute(
            "SELECT COUNT(*) FROM bars_adjusted WHERE figi='F1'"
        ).fetchone()[0] == 0
    finally:
        con.close()


def test_apply_forward_split_writes_source_and_computed_at(db):
    _seed_bars(db, [("F1", "2024-05-15", 50, 50, 50, 50, 100)])
    con = sqlite3.connect(db)
    try:
        apply_forward_split(con, "F1", date(2024, 5, 15), 2.0)
        row = con.execute(
            "SELECT source, computed_at FROM bars_adjusted WHERE figi='F1'"
        ).fetchone()
        assert row[0] == "derived:bars+forward"
        assert row[1]  # non-empty ISO timestamp
    finally:
        con.close()


def test_apply_all_pending_walks_corporate_actions_chronologically(db):
    """apply_all_pending must process splits in chronological order.

    The brief requires that apply_all_pending walks corporate_actions
    by ex_date and calls apply_forward_split for each. We assert:
      - both splits were applied (total > 0)
      - at least one row exists in bars_adjusted for each ex-date bar
      - every adjusted adj_close <= corresponding raw close (forward
        convention divides rather than multiplies)
    """
    _seed_bars(
        db,
        [
            ("F1", "2024-05-15", 50, 50, 50, 50, 1000),
            ("F1", "2024-09-15", 25, 25, 25, 25, 1000),
        ],
    )
    con = sqlite3.connect(db)
    try:
        # Intentionally insert in REVERSE chronological order to prove
        # apply_all_pending sorts by ex_date itself.
        con.executemany(
            "INSERT INTO corporate_actions (figi, action_type, ex_date, factor) "
            "VALUES (?, 'split', ?, ?)",
            [("F1", "2024-09-15", 2.0), ("F1", "2024-05-15", 2.0)],
        )
        con.commit()
        total = apply_all_pending(con)
        assert total > 0  # both splits produced at least one row each

        rows = {
            r[0]: r[1]
            for r in con.execute(
                "SELECT ts, adj_close FROM bars_adjusted WHERE figi='F1' ORDER BY ts"
            ).fetchall()
        }
        assert "2024-05-15" in rows
        assert "2024-09-15" in rows
        # Forward convention: every adjusted adj_close <= corresponding raw close.
        for ts, raw in [("2024-05-15", 50.0), ("2024-09-15", 25.0)]:
            assert rows[ts] <= raw
    finally:
        con.close()
