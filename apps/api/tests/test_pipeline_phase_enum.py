"""Regression test for PR #119: PhaseStatus literal missing the
phases that the worker writes to ``pipeline``.

Previously the enum only declared
``["discover_universe", "fetch_bars", "backfill_universe"]`` and
the worker writes at least three more
(``completeness_backfill``, ``guardian_daily``, ``worker.heartbeat``)
which made ``GET /api/pipeline`` return 500 whenever any of them
had a row, dragging the whole dashboard's load time into the
20s range via Vite proxy retries.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


ALL_PHASES = (
    "discover_universe",
    "fetch_bars",
    "backfill_universe",
    "completeness_backfill",
    "guardian_daily",
    "worker.heartbeat",
)


@pytest.fixture
def pipeline_db(fresh_db: str) -> str:
    """Insert one row per known phase into the pipeline table."""
    con = sqlite3.connect(fresh_db)
    for phase in ALL_PHASES:
        con.execute(
            "INSERT INTO pipeline (phase, started_at, finished_at, rows_processed, status, detail) "
            "VALUES (?, '2026-01-01 00:00:00', '2026-01-01 00:00:01', 0, 'ok', NULL)",
            (phase,),
        )
    con.commit()
    con.close()
    return fresh_db


def test_get_pipeline_serves_every_worker_written_phase(client, pipeline_db: str) -> None:
    """Each phase the worker writes must round-trip through
    PhaseStatus without a 500 — the enum must cover the union of
    canonical and worker-side phase names.
    """
    r = client.get("/api/pipeline")
    assert r.status_code == 200, r.text
    phases = [p["phase"] for p in r.json()["phases"]]
    for expected in ALL_PHASES:
        assert expected in phases, f"phase {expected!r} missing from {phases!r}"
