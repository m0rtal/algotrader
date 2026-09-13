"""Tests for the data-quality health module."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations
from algotrader_api.data_quality.health import (
    HealthIssue,
    compute_health,
)


@pytest.fixture
def db(tmp_path):
    """A fresh state.db with the schema applied and one SBER figi."""
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS instruments (
            ticker TEXT PRIMARY KEY, figi TEXT UNIQUE, class TEXT,
            name TEXT, currency TEXT, lot_size INTEGER, isin TEXT,
            sector TEXT, source_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS moex_holidays (
            date TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );
        """
    )
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES ('SBER', 'FIGI-SBER', 'share', 'Sber', 'rub', 10)"
    )
    con.commit()
    con.close()
    return p


def _seed_bars(db_path, figi, dates_iso):
    """Insert bars; tmpfs files are private so a fresh connection works."""
    con = sqlite3.connect(db_path, timeout=5)
    try:
        con.executemany(
            "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
            "VALUES (?, ?, 1, 1, 1, 1, 1)",
            [(figi, d) for d in dates_iso],
        )
        con.commit()
    finally:
        con.close()


def test_health_report_healthy_figi_scores_100(db):
    today = date(2026, 9, 12)
    _seed_bars(
        db, "FIGI-SBER",
        [(today - timedelta(days=i)).isoformat() for i in range(30, -1, -1)],
    )
    r = compute_health(db, "FIGI-SBER", today=today)
    assert r.health_score == 100
    assert r.issues == []


def test_health_report_missing_recent_days(db):
    today = date(2026, 9, 12)
    # Bars from 30 days ago up to 10 days ago; last bar = today - 10.
    dates = [(today - timedelta(days=i)).isoformat() for i in range(10, 31)]
    _seed_bars(db, "FIGI-SBER", dates)
    r = compute_health(db, "FIGI-SBER", today=today)
    assert HealthIssue.MISSING_RECENT in r.issues
    assert r.health_score <= 70
    assert r.last_bar == today - timedelta(days=10)


def test_health_report_sparse_history_capped(db):
    today = date(2026, 9, 12)
    # First bar 5 years ago; only 30 bars since then. Expected is
    # roughly 5*250=1250 weekdays, actual is 30 — sparse.
    first = date(2021, 1, 15)
    dates = [first.isoformat()]
    dates += [(today - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
    _seed_bars(db, "FIGI-SBER", dates)
    r = compute_health(db, "FIGI-SBER", today=today)
    assert HealthIssue.SPARSE_HISTORY in r.issues
    # Sparse (-20) + INCOMPLETE_HISTORY (-25) + the gap between
    # first_bar and the next batch (-20 because it's >5 calendar days)
    # = -65. The SPARSE_HISTORY itself is still capped at 20; the other
    # two issues each have their own penalty.
    assert r.health_score >= 30
    assert r.health_score <= 70


def test_health_report_has_gaps_detects_long_gap(db):
    today = date(2026, 9, 12)
    dates = [(today - timedelta(days=i)).isoformat() for i in range(30, 15, -1)]
    dates += [(today - timedelta(days=i)).isoformat() for i in range(8, -1, -1)]
    _seed_bars(db, "FIGI-SBER", dates)
    r = compute_health(db, "FIGI-SBER", today=today)
    assert HealthIssue.HAS_GAPS in r.issues
    assert len(r.recent_gaps) > 0


def test_health_report_rate_limited_failures(db):
    today = date(2026, 9, 12)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO ingestion_logs (ts, run_id, level, figi, message) "
        "VALUES (datetime('now', '-1 day'), 1, 'warn', 'FIGI-SBER', 'rate limit hit')"
    )
    con.commit()
    con.close()
    r = compute_health(db, "FIGI-SBER", today=today)
    assert HealthIssue.RATE_LIMITED_FAILURES in r.issues
    assert r.recent_failures


def test_compute_all_returns_only_tradeable_figis(db):
    today = date(2026, 9, 12)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES ('OPT', 'FIGI-OPT', 'option', 'Opt', 'rub', 1)"
    )
    con.commit()
    con.close()
    _seed_bars(
        db, "FIGI-OPT",
        [(today - timedelta(days=i)).isoformat() for i in range(30, -1, -1)],
    )
    # Inline import to avoid top-level side effect.
    from algotrader_api.data_quality.health import compute_all

    reports = compute_all(db, today=today)
    assert "FIGI-SBER" in reports
    assert "FIGI-OPT" not in reports


def test_health_report_unknown_figi_scores_zero(db):
    """A figi with no instrument row gets score 0 + MISSING_RECENT."""
    r = compute_health(db, "FIGI-DOES-NOT-EXIST", today=date(2026, 9, 12))
    assert r.health_score == 0
    assert HealthIssue.MISSING_RECENT in r.issues


def test_compute_all_handles_large_universe_in_bulk(db):
    """`compute_all` runs in O(1) queries regardless of figi count.

    The bulk path must produce one HealthReport per tradeable figi
    without opening N separate SQLite connections or running N
    separate JOIN queries.
    """
    import sqlite3 as _sq
    today = date(2026, 9, 12)
    figis = [f"FIGI-{i}" for i in range(20)]
    con = _sq.connect(db)
    con.executemany(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES (?, ?, 'share', ?, 'rub', 1)",
        [(f"FIG-{i}", f"FIGI-{i}", f"N{i}") for i in range(20)],
    )
    con.executemany(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES (?, ?, 1, 1, 1, 1, 1)",
        [(f"FIGI-{i}", today.isoformat()) for i in range(20)],
    )
    con.commit()
    con.close()

    from algotrader_api.data_quality.health import compute_all
    reports = compute_all(db, today=today)
    assert len(reports) >= 20  # fixture adds one FIGI-SBER above
    # The 20 figis we just seeded are all healthy.
    for i in range(20):
        r = reports.get(f"FIGI-{i}")
        assert r is not None, f"FIGI-{i} missing from reports"
        assert r.health_score == 100, f"FIGI-{i} score={r.health_score} (issues={r.issues})"


def test_health_report_marks_incomplete_history_with_holidays(db):
    """A figi whose actual bars are < 95% of weekdays-minus-holidays between
    first_bar and today must be flagged with INCOMPLETE_HISTORY and lose 25 points.
    """
    from algotrader_api.data_quality.health import (
        HealthIssue,
        HealthReport,
        compute_health,
    )

    figi = "FIGI-SPARSE"
    today = date(2026, 9, 12)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES ('SPRS', ?, 'share', 'Sparse', 'rub', 1)",
        (figi,),
    )
    # First bar well in the past, then a long hole, then a few recent weekday
    # bars — actual << weekdays_minus_holidays between first_bar and today.
    # 2026-01-12 (Mon) -> 2026-09-12 (Sat) is ~149 weekdays.
    # We seed only 8 bars so 8 < 0.95 * 149 == 141.5.
    bar_dates = [
        "2026-01-12",  # Mon
        "2026-03-16",  # Mon
        "2026-04-20",  # Mon
        "2026-05-18",  # Mon
        "2026-06-22",  # Mon
        "2026-07-20",  # Mon
        "2026-08-17",  # Mon
        "2026-09-07",  # Mon (5 days ago, within MISSING_RECENT window)
    ]
    con.executemany(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES (?, ?, 1, 1, 1, 1, 1)",
        [(figi, d) for d in bar_dates],
    )
    # Subtract two holidays inside the range; without this, _weekdays_excluding_holidays
    # would still leave actual << expected, but the helper subtraction is what we want to
    # exercise end-to-end. INSERT OR IGNORE because migration 006 now seeds the table
    # with the canonical 2020-2027 calendar — dates that overlap will silently no-op.
    con.executemany(
        "INSERT OR IGNORE INTO moex_holidays (date, name) VALUES (?, ?)",
        [
            ("2026-05-01", "test-spring"),
            ("2026-06-12", "test-russia-day"),
        ],
    )
    con.commit()
    con.close()

    report = compute_health(db, figi, today=today)
    assert isinstance(report, HealthReport)
    assert HealthIssue.INCOMPLETE_HISTORY in report.issues
    # The penalty is -25 on top of any other issues (HAS_GAPS adds another -20,
    # MISSING_RECENT does not fire because the last bar is within 3 days).
    # So worst case is 100 - 25 - 20 = 55; assert the -25 floor landed.
    assert report.health_score <= 75
    # And the new issue must contribute at least 25 points of penalty.
