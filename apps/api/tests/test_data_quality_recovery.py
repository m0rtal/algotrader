"""Tests for the data-quality recovery module."""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations
from algotrader_api.data_quality.health import HealthIssue, HealthReport
from algotrader_api.data_quality.recovery import recover_stale


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS instrument_metadata (
            figi TEXT PRIMARY KEY, last_bar_ts TEXT,
            last_backfilled_at TEXT, total_bars INTEGER,
            last_run_status TEXT, last_run_at TEXT, last_error TEXT
        );
        """
    )
    con.executemany(
        "INSERT INTO instrument_metadata (figi, last_run_status, total_bars) VALUES (?, ?, ?)",
        [
            ("FIGI-A", "ok", 100),
            ("FIGI-B", "error", 0),
            ("FIGI-C", "stale_recovery_exhausted", 0),
            ("FIGI-D", "error", 0),
            ("FIGI-E", "error", 0),
        ],
    )
    con.commit()
    con.close()
    return p


def _report(figi, score, issues):
    return HealthReport(figi=figi, ticker=figi, health_score=score, issues=issues)


def test_recover_stale_queues_only_unhealthy_non_exhausted_non_ratelimit_only(db):
    runner = MagicMock()
    runner.run = MagicMock()
    reports = {
        "FIGI-A": _report("FIGI-A", 100, []),
        "FIGI-B": _report("FIGI-B", 70, [HealthIssue.MISSING_RECENT]),
        "FIGI-C": _report("FIGI-C", 30, [HealthIssue.MISSING_RECENT]),  # exhausted
        "FIGI-D": _report("FIGI-D", 70, [HealthIssue.RATE_LIMITED_FAILURES]),  # ratelimit only
        "FIGI-E": _report("FIGI-E", 40, [HealthIssue.MISSING_RECENT, HealthIssue.RATE_LIMITED_FAILURES]),
    }
    summary = recover_stale(db, runner, reports)
    assert "FIGI-B" in summary.queued
    assert "FIGI-E" in summary.queued
    assert "FIGI-C" in summary.skipped_exhausted
    assert "FIGI-D" in summary.skipped_ratelimit_only
    assert "FIGI-A" not in summary.queued


def test_recover_stale_sorts_by_score_worst_first(db):
    runner = MagicMock()
    runner.run = MagicMock()
    reports = {
        "FIGI-LOW": _report("FIGI-LOW", 30, [HealthIssue.MISSING_RECENT]),
        "FIGI-HIGH": _report("FIGI-HIGH", 80, [HealthIssue.HAS_GAPS]),
        "FIGI-MID": _report("FIGI-MID", 50, [HealthIssue.SPARSE_HISTORY]),
    }
    summary = recover_stale(db, runner, reports)
    assert summary.queued == ["FIGI-LOW", "FIGI-MID", "FIGI-HIGH"]


def test_recover_stale_returns_empty_when_all_healthy(db):
    runner = MagicMock()
    runner.run = MagicMock()
    summary = recover_stale(db, runner, {"FIGI-A": _report("FIGI-A", 100, [])})
    assert summary.queued == []
    runner.run.assert_not_called()


def test_recover_stale_calls_runner_with_limit_to(db):
    runner = MagicMock()
    runner.run = MagicMock()
    reports = {
        "FIGI-A": _report("FIGI-A", 70, [HealthIssue.MISSING_RECENT]),
        "FIGI-B": _report("FIGI-B", 50, [HealthIssue.MISSING_RECENT]),
    }
    recover_stale(db, runner, reports)
    runner.run.assert_called_once()
    kwargs = runner.run.call_args.kwargs
    assert kwargs["limit_to"] == ["FIGI-B", "FIGI-A"]  # sorted by score
