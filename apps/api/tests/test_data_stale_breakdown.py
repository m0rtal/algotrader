"""Tests for the multi-level stale breakdown endpoint.

Added in PR #130 (2026-09-24). The endpoint surfaces three
buckets of staleness — fresh-or-today, stale-more-than-1-day,
stale-more-than-2-days — plus the age of the most recent
successful corporate_actions and dividends phase.

These tests pin the *bucket boundaries* (which figi belongs
where) so future changes don't silently redefine "stale" and
make the operator screen hide the same problem we just exposed.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pytest

from algotrader_api.routes.admin import _stale_breakdown
from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations


@pytest.fixture
def db_path(tmp_path):
    """Run migrations on a fresh DB; ensure ``pipeline_log`` exists."""
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    # ``pipeline_log`` is created lazily by the admin endpoint in
    # production (see ``data_pipeline_status``). Tests that exercise
    # the breakdown endpoint directly don't go through that code
    # path, so we make sure the table is here before seeding. The
    # endpoint still creates it idempotently at request time, so
    # this is just defence-in-depth for tests.
    con = sqlite3.connect(p)
    con.execute(
        "CREATE TABLE IF NOT EXISTS pipeline_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "phase TEXT NOT NULL, "
        "started_at TEXT NOT NULL, "
        "finished_at TEXT NOT NULL, "
        "result TEXT NOT NULL, "
        "detail TEXT"
        ")"
    )
    con.commit()
    con.close()
    yield p


def _seed_instruments(db_path: str, figis: list[tuple[str, str, str]]) -> None:
    """Insert tradable instruments. Format: [(figi, ticker, class), ...]

    The real ``instruments`` table (per migration 016) requires
    ``name``, ``currency`` and ``lot_size`` as NOT NULL even on
    minimal rows. We populate safe defaults so the row satisfies
    the schema without dragging production data into the test.
    """
    con = sqlite3.connect(db_path)
    for figi, ticker, cls in figis:
        con.execute(
            "INSERT INTO instruments(figi, ticker, class, name, currency, lot_size) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (figi, ticker, cls, f"Test {ticker}", "RUB", 1),
        )
    con.commit()
    con.close()


def _seed_bars(db_path: str, figi: str, ts_iso: str) -> None:
    """Insert a single bar at the given timestamp for figi."""
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO bars(figi, ts, source, open, high, low, close, volume) "
        "VALUES (?, ?, 'moex', 1, 1, 1, 1, 0)",
        (figi, ts_iso),
    )
    con.commit()
    con.close()


def _seed_pipeline_log(db_path: str, phase: str, finished_iso: str) -> None:
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO pipeline_log(phase, started_at, finished_at, result) "
        "VALUES (?, ?, ?, 'ok')",
        (phase, finished_iso, finished_iso),
    )
    con.commit()
    con.close()


# ─── Bucket boundaries ─────────────────────────────────────────────


def test_fresh_today_bucket(db_path):
    today = date.today().isoformat()
    _seed_instruments(db_path, [("F1", "T1", "share"), ("F2", "T2", "share")])
    _seed_bars(db_path, "F1", today)
    _seed_bars(db_path, "F2", f"{today}T15:30:00")
    out = _stale_breakdown(db_path)
    assert out["bars"]["fresh_or_today"] == 2
    assert out["bars"]["stale_more_than_1_day"] == 0
    assert out["bars"]["stale_more_than_2_days"] == 0
    assert out["bars"]["no_bars_ever"] == 0
    assert out["bars"]["tradable_total"] == 2


def test_yesterday_is_already_stale(db_path):
    """This test pins an important boundary: when the operator says
    "stale >1 day", they mean bars *older than yesterday*. Yesterday
    itself is fresh (lags by 0 at start-of-day, by 1 at end-of-day —
    both are 'one day old or fresher', which is the operator's
    'acceptable' band).

    The reason this matters: a recent spec change accidentally
    double-counted yesterday as both fresh and stale, hiding the
    real gap. This test guards against that.
    """
    today = date.today()
    two_days_ago = (today - timedelta(days=2)).isoformat()
    _seed_instruments(db_path, [("F1", "T1", "share")])
    _seed_bars(db_path, "F1", two_days_ago)
    out = _stale_breakdown(db_path)
    assert out["bars"]["fresh_or_today"] == 0
    assert out["bars"]["stale_more_than_1_day"] == 1
    assert out["bars"]["samples_stale_1d"][0][0] == "F1"


def test_two_days_ago_goes_into_stale_2d_bucket(db_path):
    """Bars three days old must NOT be confused with the 1-day bucket,
    otherwise the UI can't distinguish 'lagged yesterday once' from
    'has not caught up for several days'."""
    today = date.today()
    three_days_ago = (today - timedelta(days=3)).isoformat()
    _seed_instruments(db_path, [("F1", "T1", "share")])
    _seed_bars(db_path, "F1", three_days_ago)
    out = _stale_breakdown(db_path)
    assert out["bars"]["stale_more_than_2_days"] == 1
    assert out["bars"]["stale_more_than_1_day"] == 0
    assert out["bars"]["samples_stale_2d"][0][0] == "F1"


def test_no_bars_figi_is_separate_bucket(db_path):
    """Empty figis are a different problem (no source) from
    stale figis (have source, just falling behind). Mixing them
    would inflate the stale count and hide the real recovery
    action."""
    _seed_instruments(db_path, [("F1", "T1", "share"), ("F2", "T2", "share")])
    _seed_bars(db_path, "F1", date.today().isoformat())
    out = _stale_breakdown(db_path)
    assert out["bars"]["fresh_or_today"] == 1
    assert out["bars"]["no_bars_ever"] == 1
    assert out["bars"]["stale_more_than_1_day"] == 0
    assert out["bars"]["stale_more_than_2_days"] == 0


def test_non_tradable_classes_excluded(db_path):
    """Futures/options are explicitly excluded from backfill.
    Including them would inflate the stale count."""
    today = date.today().isoformat()
    _seed_instruments(
        db_path,
        [
            ("F1", "T1", "share"),
            ("F2", "T2", "future"),
            ("F3", "T3", "option"),
            ("F4", "T4", "etf"),
            ("F5", "T5", "bond"),
        ],
    )
    for figi in ("F1", "F2", "F3", "F4", "F5"):
        _seed_bars(db_path, figi, today)
    out = _stale_breakdown(db_path)
    assert out["bars"]["tradable_total"] == 3
    assert out["bars"]["fresh_or_today"] == 3


# ─── Pipeline ages ─────────────────────────────────────────────────


def test_pipeline_ages_returned_for_known_phases(db_path):
    """When pipeline_log has rows, both phases return an integer age
    in hours since the latest successful ``finished_at``."""
    now = datetime.now()
    _seed_pipeline_log(db_path, "corporate_actions",
                       (now - timedelta(hours=3)).isoformat())
    _seed_pipeline_log(db_path, "dividends",
                       (now - timedelta(hours=25)).isoformat())
    out = _stale_breakdown(db_path)
    ca_age = out["pipeline_age_hours"]["corporate_actions"]
    div_age = out["pipeline_age_hours"]["dividends"]
    assert ca_age is not None and 2.9 < ca_age < 3.1
    assert div_age is not None and 24.9 < div_age < 25.1


def test_pipeline_ages_none_when_no_rows(db_path):
    """No successful log rows → age is None (vs 0, which would lie
    saying 'just ran')."""
    out = _stale_breakdown(db_path)
    assert out["pipeline_age_hours"]["corporate_actions"] is None
    assert out["pipeline_age_hours"]["dividends"] is None


def test_pipeline_ages_only_use_successful_rows(db_path):
    """Failed phases ('error' result) must NOT be treated as fresh
    evidence the pipeline last ran successfully."""
    now = datetime.now()
    _seed_pipeline_log(db_path, "corporate_actions",
                       (now - timedelta(hours=1)).isoformat())
    # Also insert a failing log that's older — should not reset the
    # age to a smaller value; the SUCCESS row is what counts.
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO pipeline_log(phase, started_at, finished_at, result) "
        "VALUES (?, ?, ?, 'error')",
        ("corporate_actions",
         (now - timedelta(hours=0.5)).isoformat(),
         (now - timedelta(hours=0.5)).isoformat()),
    )
    con.commit()
    con.close()
    out = _stale_breakdown(db_path)
    # Age should still be ~1h because the successful run defines the
    # "last successful" timestamp.
    ca_age = out["pipeline_age_hours"]["corporate_actions"]
    assert ca_age is not None and 0.9 < ca_age < 1.1


# ─── Edge cases ─────────────────────────────────────────────────────


def test_corrupt_ts_string_goes_to_no_bars(db_path):
    """A figi whose ``MAX(ts)`` returns a non-dateable string is
    treated as 'no bars' rather than crashing. SQLite MAX can
    in principle return NULL or empty if the table is somehow
    in an inconsistent state."""
    _seed_instruments(db_path, [("F1", "T1", "share")])
    # No bars inserted for F1
    out = _stale_breakdown(db_path)
    assert out["bars"]["no_bars_ever"] == 1


def test_samples_capped_at_ten(db_path):
    """The 'samples_*' arrays are sized 10 to keep the payload
    small. Important because the DataTab only renders the first
    10 anyway."""
    today = date.today()
    week_ago = (today - timedelta(days=10)).isoformat()
    _seed_instruments(
        db_path,
        [(f"F{i}", f"T{i}", "share") for i in range(20)],
    )
    for i in range(20):
        _seed_bars(db_path, f"F{i}", week_ago)
    out = _stale_breakdown(db_path)
    assert len(out["bars"]["samples_stale_2d"]) == 10
    assert out["bars"]["stale_more_than_2_days"] == 20


def test_yesterday_constant_for_frontend(db_path):
    """The response includes 'as_of' and 'yesterday' so the UI
    can label the buckets without re-computing."""
    out = _stale_breakdown(db_path)
    assert out["as_of"] == date.today().isoformat()
    assert out["yesterday"] == (
        date.today() - timedelta(days=1)
    ).isoformat()
