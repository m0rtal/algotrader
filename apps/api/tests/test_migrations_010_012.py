import sqlite3
import pathlib


def test_seed_2022_restricted_periods_inserts_36_rows(tmp_path: pathlib.Path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        open("src/algotrader_api/db/migrations/010_restricted_periods.sql").read()
    )
    conn.executescript(
        open("src/algotrader_api/db/migrations/011_seed_restricted_periods.sql").read()
    )
    rows = conn.execute("SELECT date FROM restricted_periods ORDER BY date").fetchall()
    # 25 entries: trading-day halts only (weekends were not trading
    # days, so no need to record them as "missing"). See migration 011.
    assert len(rows) == 25
    assert rows[0][0] == "2022-02-24"
    assert rows[-1][0] == "2022-03-31"
    conn.close()


def test_seed_1998_redenomination_inserts_split_per_figi(tmp_path: pathlib.Path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    # Minimal schema for the redenomination seed.
    conn.executescript(
        """
        CREATE TABLE bars (
            figi    TEXT NOT NULL,
            ts      DATE NOT NULL,
            open    REAL NOT NULL,
            high    REAL NOT NULL,
            low     REAL NOT NULL,
            close   REAL NOT NULL,
            volume  INTEGER,
            PRIMARY KEY (figi, ts)
        );
        CREATE TABLE corporate_actions (
            figi        TEXT    NOT NULL,
            action_type TEXT    NOT NULL,
            ex_date     DATE    NOT NULL,
            factor      REAL    NOT NULL,
            cash_amount REAL,
            note        TEXT, source TEXT,
            PRIMARY KEY (figi, action_type, ex_date)
        );
        CREATE TABLE instruments (figi TEXT PRIMARY KEY, ticker TEXT, class TEXT);
        INSERT INTO instruments (figi, ticker, class) VALUES
            ('F1', 'GAZP', 'share'),
            ('F2', 'SBER', 'share'),
            ('F3', 'LKOH', 'share'),
            ('F4', 'YNDX', 'share');  -- YNDX IPO 2013 — should be excluded
        INSERT INTO bars (figi, ts, open, high, low, close, volume) VALUES
            ('F1', '1997-12-30', 100, 100, 100, 100, 1000),
            ('F2', '1996-06-15', 100, 100, 100, 100, 1000),
            ('F3', '1997-11-01', 100, 100, 100, 100, 1000),
            ('F4', '2013-06-05', 100, 100, 100, 100, 1000);
        """
    )
    conn.executescript(
        open("src/algotrader_api/db/migrations/012_seed_1998_redenomination.sql").read()
    )
    rows = conn.execute(
        "SELECT figi, factor FROM corporate_actions "
        "WHERE action_type='split' AND source LIKE '%redenomination%' "
        "ORDER BY figi"
    ).fetchall()
    # YNDX (post-1998 listing) must NOT receive the split:
    assert [r[0] for r in rows] == ["F1", "F2", "F3"]
    assert rows[0][1] == 1000.0
    conn.close()
