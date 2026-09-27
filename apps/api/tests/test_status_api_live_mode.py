"""Tests for ``last_cycle_age_seconds`` on GET /api/admin/backfill/status.

Spec: openspec/changes/archive/2026-09-27-autonomous-data-pipeline/
      specs/data-quality/spec.md — Requirement #3 (Status API for UI).

The Status API exposes ``last_cycle_age_seconds`` so the UI banner
(Task 4) can detect a stuck pipeline without polling the broker. The
value is the integer number of seconds since the most recent
``pipeline_runs.finished_at`` row with rc=0 (success) — falling back
to the most recent row of any rc when no successful cycle exists yet.

These tests exercise the route via the FastAPI TestClient using a tmp
DB so we never touch the real broker or database. The pipeline_runs
table is created by migration 024 (Task 2 — already on this branch).

TDD discipline: this file was written RED first; implementation
landed in apps/api/src/algotrader_api/routes/backfill.py afterwards.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


def _insert_run(db_path: str, *, finished_minutes_ago: int, rc: int) -> None:
    """Insert one pipeline_runs row finished ``finished_minutes_ago`` ago."""
    finished = datetime.now() - timedelta(minutes=finished_minutes_ago)
    started = finished - timedelta(seconds=10)
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO pipeline_runs (started_at, finished_at, rc, stale_2d_count) "
        "VALUES (?, ?, ?, 0)",
        (started.isoformat(), finished.isoformat(), rc),
    )
    con.commit()
    con.close()


def _drop_pipeline_runs(db_path: str) -> None:
    """Simulate a pre-Task-2 schema where pipeline_runs does not exist."""
    con = sqlite3.connect(db_path)
    con.execute("DROP TABLE IF EXISTS pipeline_runs")
    con.commit()
    con.close()


def test_status_returns_last_cycle_age_seconds_after_run(client, fresh_db):
    """When pipeline_runs has a recent successful row, the endpoint
    returns an integer near ``(now - finished_at).total_seconds()``."""
    _insert_run(fresh_db, finished_minutes_ago=4, rc=0)

    r = client.get("/api/admin/backfill/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "last_cycle_age_seconds" in body, body
    age = body["last_cycle_age_seconds"]
    assert isinstance(age, int), f"expected int, got {type(age).__name__}: {age!r}"
    # ~4 minutes ago = 240s; allow generous slack for test-runner overhead.
    assert 200 <= age <= 360, f"unexpected age {age}"


def test_status_returns_null_when_no_pipeline_runs_table(client, fresh_db):
    """If pipeline_runs does not exist (pre-Task-2 schema on a remote
    dev deploy), the endpoint MUST return ``null`` and continue
    gracefully — no 500."""
    _drop_pipeline_runs(fresh_db)

    r = client.get("/api/admin/backfill/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("last_cycle_age_seconds") is None, body


def test_status_returns_null_when_no_runs_yet(client, fresh_db):
    """On a fresh deploy where migration 024 ran but the worker has
    not yet completed a cycle, ``last_cycle_age_seconds`` is null."""
    # pipeline_runs exists (migrations ran) but has zero rows.
    con = sqlite3.connect(fresh_db)
    n = con.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0]
    con.close()
    assert n == 0, "fixture pollution: pipeline_runs should be empty"

    r = client.get("/api/admin/backfill/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("last_cycle_age_seconds") is None, body


def test_status_prefers_most_recent_successful_run_over_failed(client, fresh_db):
    """When the most-recent overall run failed but an older run
    succeeded, the age is measured from the successful one. When the
    most-recent overall run succeeded (regardless of older failures),
    the age is measured from that one."""
    # Older success (15 min ago) + newer failure (1 min ago).
    _insert_run(fresh_db, finished_minutes_ago=15, rc=0)
    _insert_run(fresh_db, finished_minutes_ago=1, rc=1)

    r = client.get("/api/admin/backfill/status")
    assert r.status_code == 200, r.text
    age = r.json()["last_cycle_age_seconds"]
    assert isinstance(age, int)
    # Must reflect the SUCCESSFUL run (~15 min), not the failed one (~1 min).
    assert 14 * 60 <= age <= 16 * 60 + 30, (
        f"expected ~900s (15 min) age based on the successful run, got {age}"
    )

    # Now insert a newer successful run — the endpoint must switch to it.
    _insert_run(fresh_db, finished_minutes_ago=2, rc=0)
    r2 = client.get("/api/admin/backfill/status")
    assert r2.status_code == 200, r2.text
    age2 = r2.json()["last_cycle_age_seconds"]
    assert isinstance(age2, int)
    assert 60 <= age2 <= 240, (
        f"expected ~120s (2 min) age based on the newer successful run, got {age2}"
    )


def test_status_age_does_not_drift_when_table_empty_after_clear(client, fresh_db):
    """If rows exist, then are deleted between requests, the next
    request must return ``null`` (NOT a stale age from a now-gone
    row). Regression guard against caching / closures being held
    over by the route."""
    _insert_run(fresh_db, finished_minutes_ago=3, rc=0)
    r1 = client.get("/api/admin/backfill/status")
    assert r1.status_code == 200
    age1 = r1.json()["last_cycle_age_seconds"]
    assert isinstance(age1, int) and age1 > 0

    # Wipe the table; the next call must report null, not the old age.
    con = sqlite3.connect(fresh_db)
    con.execute("DELETE FROM pipeline_runs")
    con.commit()
    con.close()

    r2 = client.get("/api/admin/backfill/status")
    assert r2.status_code == 200, r2.text
    assert r2.json().get("last_cycle_age_seconds") is None, (
        f"expected null after clearing pipeline_runs, got {r2.json()}"
    )
