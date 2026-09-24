"""Tests for surfacing the dividends retry-queue size on the
``/api/admin/data-stale-breakdown`` operator screen.

Added in ml-throttling PR-3 (2026-09-24). The endpoint returns a
new ``dividends_pending_retry`` field that counts the rows in
``dividends_throttle_pending`` so operators see at a glance when
dividends are lagging behind.

We exercise the helper directly (the same way
``test_data_stale_breakdown.py`` does) instead of going through
the FastAPI TestClient — it removes the ``_get_sqlite_path()``
monkeypatch plumbing and exercises the same code path the route
handler calls.
"""
from __future__ import annotations

import sqlite3

import pytest

from algotrader_api.routes.admin import _stale_breakdown
from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations


@pytest.fixture
def db_path(tmp_path):
    """Fresh DB with all migrations applied (so the queue table exists)."""
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    yield p


def _seed_queue(db_path: str, figis: list[str]) -> None:
    """Insert a row per figi into ``dividends_throttle_pending``.

    The migration (022) defines the table with columns
    ``(figi, first_failed_at, last_failed_at, retry_count)`` and
    ``figi`` as PRIMARY KEY. Timestamps are not relevant to the
    count; we use a single fixed value for all rows.
    """
    con = sqlite3.connect(db_path)
    for figi in figis:
        con.execute(
            "INSERT INTO dividends_throttle_pending("
            "figi, first_failed_at, last_failed_at, retry_count) "
            "VALUES (?, ?, ?, 1)",
            (figi, "2026-09-24T10:00:00", "2026-09-24T10:00:00"),
        )
    con.commit()
    con.close()


def test_data_stale_breakdown_includes_dividends_pending_count(db_path):
    """When the retry queue has rows, the endpoint surfaces the count
    under ``dividends_pending_retry``.

    Pins the contract documented in the spec: the field must be
    present (not just non-None) and equal to ``COUNT(*)`` of
    ``dividends_throttle_pending``.
    """
    _seed_queue(db_path, ["F1", "F2", "F3"])
    out = _stale_breakdown(db_path)
    assert "dividends_pending_retry" in out, (
        "endpoint must surface dividends_pending_retry for operator visibility"
    )
    assert out["dividends_pending_retry"] == 3


def test_data_stale_breakdown_zero_when_queue_empty(db_path):
    """When the queue has no rows, the field is present and equals 0.

    An absent field would force the UI to special-case missing vs
    zero; an explicit 0 keeps the contract simple. The fail-soft
    try/except in the helper also returns 0 for pre-migration-022
    DBs, but on this fixture the table exists (just empty).
    """
    out = _stale_breakdown(db_path)
    assert "dividends_pending_retry" in out
    assert out["dividends_pending_retry"] == 0