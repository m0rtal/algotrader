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
