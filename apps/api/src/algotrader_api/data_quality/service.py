"""Daily data-quality guardian orchestrator.

Sequence: universe sync → health → recovery → anomalies → pipeline row.

Designed for cron at 23:00 MSK via systemd. The worker.py
guardian mode calls `run_daily_guardian(db_path)` directly.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ..db.sqlite import execute as _sqlite_exec
from ..ingestion import universe as _universe
from ..ingestion.backfill import BackfillRunner
from .health import compute_all
from .recovery import recover_stale


@dataclass
class GuardianSummary:
    figis_checked: int = 0
    figis_recovered: int = 0
    anomalies_raised: int = 0
    duration_seconds: float = 0.0


def _make_client_from_settings():
    from ..config import get_settings
    from ..ingestion.client import make_client

    return make_client(sqlite_path=get_settings().sqlite_path, use_fake=False)


async def run_daily_guardian(
    db_path: str, runner: BackfillRunner | None = None
) -> GuardianSummary:
    """Run the daily guardian: universe sync → health → recovery → anomalies.

    `runner` is provided by the caller (the systemd worker). When
    None, we build one from settings + the live broker client.
    """
    t0 = time.time()
    summary = GuardianSummary()

    # 1. Universe sync.
    client = _make_client_from_settings()
    rows = await _universe.discover_universe(client)
    _universe.upsert_instruments(db_path, rows)

    # 2. Health pass.
    reports = compute_all(db_path)
    summary.figis_checked = len(reports)

    # 3. Recovery.
    if runner is None:
        runner = BackfillRunner(
            client=client, db_path=db_path, event_sink=lambda _: None
        )
    recovery = recover_stale(db_path, runner, reports)
    summary.figis_recovered = len(recovery.queued)
    summary.anomalies_raised = len(recovery.skipped_exhausted)

    # 4. Anomalies (also logged inside recover_stale).
    for figi in recovery.skipped_exhausted:
        logging.warning(
            "guardian.anomaly.stale_recovery_exhausted",
            figi=figi,
            message="figi has been failing 3+ cycles; operator investigation needed",
        )

    # 5. Pipeline row. Schema (from migration 002_pipeline.sql):
    # id, phase, started_at, finished_at, rows_processed, status, detail.
    _sqlite_exec(
        db_path,
        "INSERT INTO pipeline (phase, started_at, finished_at, rows_processed, status, detail) "
        "VALUES ('guardian_daily', datetime('now'), datetime('now'), ?, 'ok', ?)",
        (
            summary.figis_recovered,
            f"figis_checked={summary.figis_checked} "
            f"figis_recovered={summary.figis_recovered} "
            f"anomalies={summary.anomalies_raised}",
        ),
    )

    summary.duration_seconds = time.time() - t0
    return summary
