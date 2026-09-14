"""Tests for restricted_periods exclusion in data quality health."""
import datetime
import sqlite3
import pathlib

from algotrader_api.data_quality.health import compute_health


def test_restricted_dates_excluded_from_expected_bars(tmp_path: pathlib.Path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE instruments ("
        "  ticker TEXT PRIMARY KEY, figi TEXT NOT NULL UNIQUE,"
        "  class TEXT NOT NULL, name TEXT NOT NULL,"
        "  currency TEXT NOT NULL, lot_size INTEGER NOT NULL);"
        "INSERT INTO instruments (figi, ticker, class, name, currency, lot_size) "
        "VALUES ('F1','GAZP','share','Gazprom','RUB',10);"
        "CREATE TABLE instrument_metadata (figi TEXT PRIMARY KEY, last_bar_ts TEXT, "
        "  total_bars INTEGER, last_run_status TEXT);"
        "CREATE TABLE ingestion_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "  figi TEXT, run_started_at TEXT, ts TEXT, level TEXT, message TEXT, "
        "  error_class TEXT);"
        "CREATE TABLE moex_holidays (date TEXT PRIMARY KEY, name TEXT);"
        "CREATE TABLE bars (figi TEXT, ts TEXT, open REAL, high REAL,"
        "  low REAL, close REAL, volume INTEGER);"
        "INSERT INTO bars VALUES ('F1','2022-02-23',100,100,100,100,1),"
        "('F1','2022-02-25',100,100,100,100,1),"  # 2022-02-24 is restricted
        "('F1','2022-03-31',100,100,100,100,1),"  # 2022-03-21..31 restricted
        "('F1','2022-04-01',100,100,100,100,1);"
        "CREATE TABLE restricted_periods (date TEXT PRIMARY KEY, reason TEXT, source TEXT);"
        "INSERT INTO restricted_periods VALUES ('2022-02-24','halt','x'),"
        "('2022-02-25','halt','x');"
    )
    conn.commit()
    conn.close()

    today = datetime.date(2022, 4, 2)
    report_with = compute_health(db_path=str(db), figi="F1", today=today)

    # Drop the restricted periods and recompute to compare:
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM restricted_periods")
    conn.commit()
    conn.close()
    report_without = compute_health(db_path=str(db), figi="F1", today=today)

    # Invariant: restricted_periods must shrink expected_bars.
    # Two restricted dates (02-24 Thu, 02-25 Fri) are weekdays, so the
    # count drops by exactly 2.
    assert report_with.actual_bars == 4
    assert report_without.expected_bars - report_with.expected_bars == 2
