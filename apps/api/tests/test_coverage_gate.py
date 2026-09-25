"""Tests for apps/api/src/algotrader_api/ml/features.py — the
consumption-time coverage gate.

Spec: openspec/changes/coverage-and-quality/specs/data-quality/spec.md
(Requirement: Pre-Consumption Coverage Gate)
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from algotrader_api.ml.features import (
    InsufficientDataError,
    build_features,
    check_coverage,
    auto_recovery,
)


@pytest.fixture
def db_with_figis(tmp_path: Path) -> sqlite3.Connection:
    """In-memory SQLite with instruments + bars seeded for 3 figis:
    - FULL: max_ts=yesterday, bars_count = 100% of expected_bars
    - STALE: max_ts=5 days ago, bars_count = 100% of expected_bars
    - INCOMPLETE: max_ts=yesterday, bars_count = 50% of expected_bars
    """
    db = tmp_path / "test.db"
    con = sqlite3.connect(str(db))
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    five_days_ago = (date.today() - timedelta(days=5)).isoformat()
    # Spread n across month + day so each (figi, ts) pair is unique even
    # when COUNT(seq) > 28. Format: "YYYY-MM-DD" with MM in [01..12] and
    # DD in [01..28] cycling; beyond N=336 we re-use the cycle, but each
    # figi only inserts at most 100 rows so collisions stay per-figi.
    # Concatenated as "YYYY-MM-DD" padded to 10 chars.
    con.executescript(f"""
        CREATE TABLE instruments (
            figi TEXT PRIMARY KEY,
            ticker TEXT,
            class TEXT,
            source_updated_at TEXT,
            expected_bars INTEGER
        );
        CREATE TABLE bars (
            figi TEXT, ts TEXT, open REAL, high REAL, low REAL, close REAL,
            volume INTEGER, source TEXT DEFAULT 'moex',
            PRIMARY KEY (figi, ts)
        );
        INSERT INTO instruments VALUES
            ('FULL01', 'FULL01', 'share', '2020-01-01T00:00:00', 100),
            ('STALE01', 'STALE01', 'share', '2020-01-01T00:00:00', 100),
            ('INCOMPL01', 'INCOMPL01', 'share', '2020-01-01T00:00:00', 100);
        -- FULL01: 100 bars, max_ts=yesterday. Use year suffix to avoid
        -- duplicate (figi, ts) collisions: n=1..100 -> ts in
        -- 2020-01-01..2020-04-09 (day-by-day sequence).
        INSERT INTO bars
            SELECT 'FULL01',
                   date('2020-01-01', '+' || (n - 1) || ' days'),
                   100, 101, 99, 100, 1000, 'moex'
            FROM (
                WITH RECURSIVE seq(n) AS (SELECT 1 UNION SELECT n+1 FROM seq WHERE n < 100)
                SELECT n FROM seq
            );
        UPDATE bars SET ts = '{yesterday}' WHERE figi='FULL01'
            AND rowid = (SELECT MAX(rowid) FROM bars WHERE figi='FULL01');
        -- STALE01: 100 bars, max_ts=5 days ago. Same day-by-day sequence.
        INSERT INTO bars
            SELECT 'STALE01',
                   date('2020-01-01', '+' || (n - 1) || ' days'),
                   100, 101, 99, 100, 1000, 'moex'
            FROM (
                WITH RECURSIVE seq(n) AS (SELECT 1 UNION SELECT n+1 FROM seq WHERE n < 100)
                SELECT n FROM seq
            );
        UPDATE bars SET ts = '{five_days_ago}' WHERE figi='STALE01'
            AND rowid = (SELECT MAX(rowid) FROM bars WHERE figi='STALE01');
        -- INCOMPL01: 50 bars, max_ts=yesterday. Day-by-day sequence 1..50.
        INSERT INTO bars
            SELECT 'INCOMPL01',
                   date('2020-01-01', '+' || (n - 1) || ' days'),
                   100, 101, 99, 100, 1000, 'moex'
            FROM (
                WITH RECURSIVE seq(n) AS (SELECT 1 UNION SELECT n+1 FROM seq WHERE n < 50)
                SELECT n FROM seq
            );
        UPDATE bars SET ts = '{yesterday}' WHERE figi='INCOMPL01'
            AND rowid = (SELECT MAX(rowid) FROM bars WHERE figi='INCOMPL01');
    """)
    yield con
    con.close()


def _make_feature_matrix():
    """Return a minimal feature matrix DataFrame-like (use a list-of-dicts)."""
    return {"figis": ["FULL01", "STALE01", "INCOMPL01"], "rows": []}


def test_build_features_returns_matrix_when_all_figis_full(db_with_figis):
    """When all figis have full coverage, build_features returns the matrix."""
    # Use only FULL01
    result = build_features(
        conn=db_with_figis,
        figis=["FULL01"],
        window=None,
    )
    # Returns a non-None feature matrix (exact format is implementation choice)
    assert result is not None


def test_build_features_raises_insufficient_data_when_any_figi_stale(db_with_figis):
    """When one figi is stale (max_ts < yesterday), InsufficientDataError is raised."""
    with pytest.raises(InsufficientDataError) as exc_info:
        build_features(
            conn=db_with_figis,
            figis=["FULL01", "STALE01"],
            window=None,
        )
    failing = exc_info.value.failing_figis
    assert any(f["figi"] == "STALE01" for f in failing)
    assert any(f["reason"] == "stale" for f in failing)


def test_build_features_raises_insufficient_data_when_any_figi_incomplete(db_with_figis):
    """When one figi has bars_count < 95% of expected_bars, error raised."""
    with pytest.raises(InsufficientDataError) as exc_info:
        build_features(
            conn=db_with_figis,
            figis=["FULL01", "INCOMPL01"],
            window=None,
        )
    failing = exc_info.value.failing_figis
    assert any(f["figi"] == "INCOMPL01" for f in failing)
    assert any(f["reason"] == "incomplete" for f in failing)


def test_insufficient_data_triggers_auto_recovery(db_with_figis):
    """When insufficient data is detected, auto_recovery is called exactly once."""
    with patch("algotrader_api.ml.features.auto_recovery") as mock_recovery:
        mock_recovery.return_value = []  # recovery succeeds — no failing figis left
        try:
            build_features(
                conn=db_with_figis,
                figis=["FULL01", "STALE01"],
                window=None,
            )
        except InsufficientDataError:
            pass  # OK if recovery succeeds we don't reach here; verify below
    # auto_recovery called exactly once (not twice, not zero times)
    assert mock_recovery.call_count == 1


def test_insufficient_data_propagates_when_recovery_fails(db_with_figis):
    """When auto_recovery returns failing figis, InsufficientDataError propagates."""
    with patch("algotrader_api.ml.features.auto_recovery") as mock_recovery:
        # Recovery returns the same failing figi — no progress
        mock_recovery.return_value = [
            {"figi": "STALE01", "max_ts": "2024-09-01", "bars_count": 100,
             "expected": 100, "reason": "stale"}
        ]
        with pytest.raises(InsufficientDataError) as exc_info:
            build_features(
                conn=db_with_figis,
                figis=["FULL01", "STALE01"],
                window=None,
            )
        # Error indicates attempted_recovery=True
        assert exc_info.value.attempted_recovery is True


def test_insufficient_data_error_includes_failing_figis_list(db_with_figis):
    """The InsufficientDataError carries a list of failing figis with reasons."""
    with pytest.raises(InsufficientDataError) as exc_info:
        build_features(
            conn=db_with_figis,
            figis=["FULL01", "STALE01", "INCOMPL01"],
            window=None,
        )
    failing = exc_info.value.failing_figis
    # Both STALE01 and INCOMPL01 should be in the failing list
    figis_in_error = {f["figi"] for f in failing}
    assert "STALE01" in figis_in_error
    assert "INCOMPL01" in figis_in_error
    # Each entry has at least figi + reason fields
    for entry in failing:
        assert "figi" in entry
        assert "reason" in entry
        assert entry["reason"] in ("stale", "incomplete", "both")
