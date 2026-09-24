import sqlite3, pytest

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
    """)
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
