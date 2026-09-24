import sqlite3, pytest
from pathlib import Path
from algotrader_api.db.migrations import MIGRATIONS_DIR

ML_FEATURES_VIEW_SQL = (
    Path(MIGRATIONS_DIR) / "021_ml_features_view.sql"
).read_text(encoding="utf-8")

@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "s.db")
    con = sqlite3.connect(p)
    con.executescript("""
        CREATE TABLE instruments (figi TEXT PRIMARY KEY, ticker TEXT NOT NULL,
                                 class TEXT NOT NULL);
        INSERT INTO instruments VALUES
            ('F1','SBER','share'),
            ('F2','GAZP','share'),
            ('F3','FXRU','future');  -- non-tradeable
        CREATE TABLE bars (figi TEXT NOT NULL, ts TEXT NOT NULL,
                           open REAL, high REAL, low REAL, close REAL,
                           volume INTEGER,
                           source TEXT NOT NULL DEFAULT 'moex',
                           PRIMARY KEY (figi, ts));
        INSERT INTO bars VALUES
            ('F1','2026-09-22',100,105,99,104,1000,'moex'),
            ('F1','2026-09-23',104,108,103,107,1100,'moex'),
            ('F2','2026-09-23',200,205,198,204,500,'tinkoff'),
            ('F3','2026-09-23',50,51,49,49,200,'moex');
        CREATE TABLE corporate_actions
            (figi TEXT NOT NULL, ex_date TEXT NOT NULL,
             factor REAL NOT NULL,
             PRIMARY KEY(figi, ex_date, factor));
        INSERT INTO corporate_actions VALUES ('F1','2026-09-21',2.0);
        CREATE TABLE dividends (figi TEXT NOT NULL, ex_date TEXT NOT NULL,
                               amount_per_share_rub REAL NOT NULL,
                               PRIMARY KEY (figi, ex_date));
        INSERT INTO dividends VALUES ('F1','2026-04-15',5.0);
        -- Out-of-window trailing-12m row: 2024-12-10 is ~21 months
        -- before 2026-09-23, so it MUST be excluded from the
        -- dividend_paid_ttm_rub sum. Without this row, a buggy
        -- implementation that summed all dividends would still pass
        -- the dividend test (it would coincidentally return 5.0 in
        -- both the buggy and correct paths). This row guards the
        -- window condition.
        --
        -- Note: we deliberately avoid 2025-12-10 here because SQLite's
        -- `date('2026-09-23', '-12 months')` returns '2025-09-23', so
        -- 2025-12-10 falls INSIDE the trailing-12m window (~9.5
        -- months before). 2024-12-10 is unambiguously outside.
        INSERT INTO dividends VALUES ('F1','2024-12-10',3.0);
    """)
    # The ml_features view is a SQLite view, not a table, so each
    # test that touches it must create it against the freshly-seeded
    # schema. The migration file is the single source of truth — we
    # execute its raw SQL after the inline CREATE statements. The
    # view's DROP IF EXISTS makes this safe across re-runs.
    con.executescript(ML_FEATURES_VIEW_SQL)
    con.commit(); con.close(); yield p


def test_view_exists(db):
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='view' AND name='ml_features'"
    ).fetchall()
    assert rows == [('ml_features',)]


def test_returns_one_row_per_figi_ts(db):
    con = sqlite3.connect(db)
    n = con.execute(
        "SELECT COUNT(*) FROM ml_features WHERE is_tradeable=1"
    ).fetchone()[0]
    # F3 is future class, filtered out. F1 has 2 ts, F2 has 1 ts = 3.
    assert n == 3


def test_split_halves_adj_close(db):
    con = sqlite3.connect(db)
    v = con.execute(
        "SELECT adj_close FROM ml_features "
        "WHERE figi='F1' AND ts='2026-09-23'"
    ).fetchone()[0]
    assert v == pytest.approx(53.5)


def test_split_after_ts_does_not_retroactively_affect_adj_close(db):
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO corporate_actions VALUES ('F2','2026-09-30',2.0)"
    )
    con.commit()
    v = con.execute(
        "SELECT adj_close FROM ml_features "
        "WHERE figi='F2' AND ts='2026-09-23'"
    ).fetchone()[0]
    assert v == pytest.approx(204.0)


def test_no_split_gives_factor_1_0(db):
    con = sqlite3.connect(db)
    v = con.execute(
        "SELECT cumulative_split_factor FROM ml_features "
        "WHERE figi='F2' AND ts='2026-09-23'"
    ).fetchone()[0]
    assert v == pytest.approx(1.0)


def test_dividend_in_window_sums(db):
    con = sqlite3.connect(db)
    v = con.execute(
        "SELECT dividend_paid_ttm_rub FROM ml_features "
        "WHERE figi='F1' AND ts='2026-09-23'"
    ).fetchone()[0]
    assert v == pytest.approx(5.0)


def test_no_dividend_returns_null_not_zero(db):
    con = sqlite3.connect(db)
    v = con.execute(
        "SELECT dividend_paid_ttm_rub FROM ml_features "
        "WHERE figi='F2' AND ts='2026-09-23'"
    ).fetchone()[0]
    assert v is None


def test_columns_present(db):
    """The view must expose BOTH the raw bars columns AND the
    ML-derived ones (cumulative_split_factor, adj_close,
    dividend_paid_ttm_rub, is_tradeable). Models that train on the
    raw series need open/high/low/close/volume/source as well as the
    adjusted close; guard the superset contract via PRAGMA table_info.
    """
    con = sqlite3.connect(db)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(ml_features)")}
    finally:
        con.close()
    required = {
        "figi", "ts", "open", "high", "low", "close", "volume", "source",
        "cumulative_split_factor", "adj_close",
        "dividend_paid_ttm_rub", "is_tradeable",
    }
    missing = required - cols
    assert not missing, f"ml_features is missing columns: {sorted(missing)}"
