import sqlite3, pytest
from pathlib import Path
from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations
from algotrader_api.data_quality.forward_adjustment import apply_all_pending


@pytest.fixture
def seeded_db(tmp_path):
    """DB with bars table, corporate_actions table, bars_adjusted table.
    Migration 015 already creates bars_adjusted; migration 005 creates bars;
    migration 007 creates corporate_actions."""
    p = str(tmp_path / "s.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    con = sqlite3.connect(p)
    # Seed figis
    con.executescript("""
        INSERT INTO instruments(figi, ticker, class, name, currency, lot_size)
        VALUES ('F1','SBER','share','Sber','RUB',1);
        INSERT INTO bars(figi, ts, open, high, low, close, volume, source)
        VALUES
          ('F1','2026-09-22',100,105,99,104,1000,'moex'),
          ('F1','2026-09-23',104,108,103,107,1100,'moex'),
          ('F1','2026-09-24',107,110,106,109,1200,'moex');
        INSERT INTO corporate_actions(figi, ex_date, action_type, factor)
        VALUES ('F1','2026-09-21','split',2.0);
    """)
    con.commit(); con.close()
    return p


def test_apply_all_pending_writes_bars_adjusted(seeded_db):
    con = sqlite3.connect(seeded_db)
    try:
        n = apply_all_pending(con)
        con.commit()
        assert n >= 1
        rows = con.execute(
            "SELECT ts, adj_close, adj_volume "
            "FROM bars_adjusted WHERE figi='F1' ORDER BY ts"
        ).fetchall()
        assert [r[0] for r in rows] == ['2026-09-22','2026-09-23','2026-09-24']
        # Factor 2 means adj_close = raw_close / 2.
        assert rows[0][1] == pytest.approx(52.0)   # 104 / 2
        assert rows[1][1] == pytest.approx(53.5)   # 107 / 2
        assert rows[2][1] == pytest.approx(54.5)   # 109 / 2
        # Volume is NOT adjusted (Chicago-Booth convention).
        assert rows[0][2] == 1000
        assert rows[1][2] == 1100
    finally:
        con.close()


def test_apply_all_pending_is_idempotent(seeded_db):
    con = sqlite3.connect(seeded_db)
    try:
        apply_all_pending(con)
        con.commit()
        before = con.execute(
            "SELECT ts, adj_close FROM bars_adjusted WHERE figi='F1' ORDER BY ts"
        ).fetchall()
        # Re-run, expect no row count change and same adj_close
        # values bit-for-bit.
        apply_all_pending(con)
        con.commit()
        after = con.execute(
            "SELECT ts, adj_close FROM bars_adjusted WHERE figi='F1' ORDER BY ts"
        ).fetchall()
        assert len(before) == len(after) == 3
        for b, a in zip(before, after):
            assert b == a  # exact match, no float drift
    finally:
        con.close()
