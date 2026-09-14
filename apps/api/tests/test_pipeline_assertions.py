"""Tests for algotrader_api.pipeline.assertions."""
import sqlite3
import pathlib

import pytest

from algotrader_api.pipeline.assertions import assert_bars_increased


def test_assert_bars_increased_passes_when_count_grows(tmp_path: pathlib.Path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE bars (figi TEXT, ts TEXT);"
        "INSERT INTO bars VALUES ('F1','2024-01-01'),('F1','2024-01-02');"
    )
    conn.commit()
    conn.close()
    pre, post, delta = assert_bars_increased(str(db))
    assert pre == 2
    assert post == 2
    assert delta == 0  # No increase — assert returns 0 delta without raising


def test_assert_bars_increased_raises_when_count_shrinks(tmp_path: pathlib.Path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE bars (figi TEXT, ts TEXT);"
        "INSERT INTO bars VALUES ('F1','2024-01-01'),('F1','2024-01-02'),"
        "('F1','2024-01-03');"
    )
    conn.commit()
    # Caller captured pre=3 before the phase ran; one row was lost:
    conn.execute("DELETE FROM bars WHERE ts='2024-01-03'")
    conn.commit()
    conn.close()
    with pytest.raises(AssertionError, match="shrunk"):
        assert_bars_increased(str(db), pre_count=3)


def test_assert_bars_increased_records_pipeline_log(tmp_path: pathlib.Path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE bars (figi TEXT, ts TEXT);"
        "CREATE TABLE pipeline_log ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  phase TEXT, started_at TEXT, finished_at TEXT, result TEXT, detail TEXT);"
    )
    conn.commit()
    conn.close()
    pre, post, delta = assert_bars_increased(str(db), phase="daily_backfill")
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT phase, result, detail FROM pipeline_log WHERE phase='daily_backfill'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][1] == "ok"
    assert "pre=0" in rows[0][2] and "post=0" in rows[0][2]
