"""Tests for the bars_adjusted backward-adjustment view.

Chicago Booth convention: pre-split prices get scaled DOWN to the
post-split scale. `adj_close = close / PRODUCT(factors of splits with
ex_date > ts)`.
"""
from __future__ import annotations

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import get_connection, run_migrations


def _migrated_db(tmp_path) -> str:
    """Run all migrations on a fresh tmp DB and return the path."""
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    return p


def _seed_split_history(db: str, figi: str, bars, ex_date: str = "2025-06-01") -> None:
    """Insert a minimal instrument + bars + one 2-for-1 split."""
    con = get_connection(db)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO instruments(figi, ticker, class, name, currency, lot_size) "
        "VALUES (?,?,?,?,?,?)",
        (figi, "X", "share", "X", "rub", 1),
    )
    cur.executemany(
        "INSERT INTO bars(figi, ts, open, high, low, close, volume) "
        "VALUES (?,?,?,?,?,?,?)",
        [(figi, d, o, h, l, c, v) for (d, o, h, l, c, v) in bars],
    )
    cur.execute(
        "INSERT INTO corporate_actions(figi, action_type, ex_date, factor, cash_amount, note) "
        "VALUES (?, 'split', ?, 2.0, NULL, 'test 2-for-1')",
        (figi, ex_date),
    )
    con.commit()


def _seed_bars_only(db: str, figi: str, bars) -> None:
    """Insert instrument + bars but NO corporate actions."""
    con = get_connection(db)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO instruments(figi, ticker, class, name, currency, lot_size) "
        "VALUES (?,?,?,?,?,?)",
        (figi, "X", "share", "X", "rub", 1),
    )
    cur.executemany(
        "INSERT INTO bars(figi, ts, open, high, low, close, volume) "
        "VALUES (?,?,?,?,?,?,?)",
        [(figi, d, o, h, l, c, v) for (d, o, h, l, c, v) in bars],
    )
    con.commit()


def test_bars_adjusted_view_exists_and_returns_columns(tmp_path):
    db = _migrated_db(tmp_path)
    _seed_split_history(
        db,
        "FIGI-VIEW",
        [
            ("2025-05-15", 200, 210, 195, 200, 1000),  # pre-split
            ("2025-05-29", 200, 210, 195, 200, 1000),  # pre-split
            ("2025-06-15", 100, 105, 97, 100, 1000),  # post-split raw
        ],
    )
    con = get_connection(db)
    rows = con.execute(
        "SELECT ts, close, adj_close FROM bars_adjusted "
        "WHERE figi=? ORDER BY ts",
        ("FIGI-VIEW",),
    ).fetchall()
    assert len(rows) == 3

    ts, close, adj_close = rows[0]
    assert ts == "2025-05-15"
    assert close == 200
    # Pre-split close adjusted to post-split scale: 200 / 2 = 100
    assert abs(adj_close - 100.0) < 1e-9

    # Post-split bar: no events after this ts, adj_close == close
    assert rows[2][1] == 100
    assert abs(rows[2][2] - 100.0) < 1e-9


def test_bars_adjusted_no_event_means_adj_equals_close(tmp_path):
    db = _migrated_db(tmp_path)
    _seed_bars_only(
        db,
        "FIGI-NOEVT",
        [("2025-05-15", 200, 210, 195, 200, 1000)],
    )
    # No corporate action row → factor defaults to 1.0 → adj_close == close
    con = get_connection(db)
    row = con.execute(
        "SELECT close, adj_close FROM bars_adjusted WHERE figi=?",
        ("FIGI-NOEVT",),
    ).fetchone()
    assert row[0] == row[1]


def test_bars_adjusted_compounds_multiple_splits(tmp_path):
    """Two splits (2-for-1 then 3-for-1) on the same figi must compose:
    pre-both-split bar = close / (2*3) = close / 6; between splits = close/2.
    """
    db = _migrated_db(tmp_path)
    con = get_connection(db)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO instruments(figi, ticker, class, name, currency, lot_size) "
        "VALUES (?,?,?,?,?,?)",
        ("FIGI-MULTI", "M", "share", "M", "rub", 1),
    )
    cur.executemany(
        "INSERT INTO bars(figi, ts, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
        [
            ("FIGI-MULTI", "2024-12-01", 600, 600, 600, 600, 100),  # before any split
            ("FIGI-MULTI", "2025-02-01", 450, 450, 450, 450, 100),  # between splits
            ("FIGI-MULTI", "2025-08-01", 100, 100, 100, 100, 100),  # after both splits
        ],
    )
    cur.executemany(
        "INSERT INTO corporate_actions(figi, action_type, ex_date, factor, cash_amount, note) "
        "VALUES (?,?,?,?,?,?)",
        [
            ("FIGI-MULTI", "split", "2025-01-01", 2.0, None, "2-for-1"),
            ("FIGI-MULTI", "split", "2025-07-01", 3.0, None, "3-for-1"),
        ],
    )
    con.commit()

    rows = con.execute(
        "SELECT ts, close, adj_close FROM bars_adjusted "
        "WHERE figi=? ORDER BY ts",
        ("FIGI-MULTI",),
    ).fetchall()

    # Before both splits: 600 / (2*3) = 100
    assert abs(rows[0][2] - 100.0) < 1e-9
    # Between splits (only the 3-for-1 is in the future): 450 / 3 = 150
    assert abs(rows[1][2] - 150.0) < 1e-9, (
        f"expected 450/3=150, got {rows[1][2]} (close={rows[1][1]})"
    )
    # After both: untouched = 100
    assert abs(rows[2][2] - 100.0) < 1e-9
