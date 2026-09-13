"""Recovery queue for the data-quality guardian.

Picks figis that need refetching and passes them to the existing
BackfillRunner. No new runner logic — just a prioritised view of
the same universe.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..ingestion.backfill import BackfillRunner
from .health import HealthIssue, HealthReport


@dataclass
class RecoverySummary:
    queued: list[str] = field(default_factory=list)
    skipped_exhausted: list[str] = field(default_factory=list)
    skipped_ratelimit_only: list[str] = field(default_factory=list)


_RATELIMIT_ONLY = frozenset({HealthIssue.RATE_LIMITED_FAILURES})


def _is_exhausted(db_path: str, figi: str) -> bool:
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT last_run_status FROM instrument_metadata WHERE figi = ?",
            (figi,),
        ).fetchone()
        return bool(row and row[0] == "stale_recovery_exhausted")
    finally:
        con.close()


def recover_stale(
    db_path: str,
    runner: BackfillRunner,
    reports: dict[str, HealthReport],
) -> RecoverySummary:
    """Pick figis that need refetching and pass them to the runner.

    Skipped reasons are tracked separately so the operator can see
    in the pipeline run summary why a figi was held back.
    """
    summary = RecoverySummary()
    queue: list[str] = []
    for figi, report in reports.items():
        if report.health_score == 100:
            continue
        if _is_exhausted(db_path, figi):
            summary.skipped_exhausted.append(figi)
            continue
        if set(report.issues) == _RATELIMIT_ONLY:
            summary.skipped_ratelimit_only.append(figi)
            continue
        queue.append(figi)

    queue.sort(key=lambda f: reports[f].health_score)
    summary.queued = queue
    if queue:
        runner.run(
            history_years=5,
            incremental_threshold_days=2,
            limit_to=queue,  # see BackfillRunner extension
        )
    return summary
