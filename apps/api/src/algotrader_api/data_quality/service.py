"""Daily data-quality guardian orchestrator.

Sequence: acquire lock → universe sync → health → recovery →
completeness → anomalies → release lock → pipeline row.

Designed for cron at 23:00 MSK via systemd. The worker.py
guardian mode calls `run_daily_guardian(db_path)` directly.

Issue #5: the orchestrator used to run with no single-flight lock.
Two parallel systemd timer invocations would both pass through
``compute_all`` and ``recover_stale``, both INSERT a ``guardian_daily``
pipeline row, and both race on the ``_mark_*_exhausted`` UPDATEs.
The fix acquires a SQLite-backed lock on a sentinel row in
``guardian_lock`` (migration 007) using ``BEGIN IMMEDIATE`` so
the second worker blocks, fails the INSERT, and raises
``GuardianLocked``. The lock is released in a ``finally`` block
so a failed cycle never deadlocks the next one.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from ..db.sqlite import execute as _sqlite_exec
from ..ingestion import universe as _universe
from ..ingestion.backfill import BackfillRunner
from .completeness import run_completeness_pass
from .health import compute_all
from .recovery import recover_stale


_LOG = logging.getLogger("algotrader_api.data_quality.service")


# Stale-lock TTL (autonomous-chain-recovery). 6h is 10× a typical
# guardian run duration (~2-5 min), so a healthy worker is never
# mistaken for stale. Older than this AND the holder PID is dead
# → auto-clear.
STALE_TTL_SECONDS = 6 * 3600


class GuardianLocked(RuntimeError):
    """Raised when another guardian run holds the single-flight lock.

    Caller should treat this as a normal "the previous run is still
    in-flight" signal — not a bug. The supervisor / systemd timer
    is responsible for not starting a second run while one is active,
    but the lock is the safety net for the case where two timers
    fire close together or a previous run overran its window.
    """


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


# ── single-flight lock helpers (issue #5) ─────────────────────────


def _acquire_guardian_lock(db_path: str) -> None:
    """Acquire the single-flight lock. Raises ``GuardianLocked`` if held.

    Uses a SQLite ``BEGIN IMMEDIATE`` transaction so the second
    worker's INSERT serialises behind the first's commit. The
    second worker's INSERT sees the row already exists, the
    helper detects the holder_pid mismatch, and raises
    ``GuardianLocked``.

    The lock row has ``CHECK (id = 1)`` and is inserted with
    ``OR IGNORE`` so re-running on a DB that already holds the
    lock from a crashed process returns no row, which we treat
    as "locked" — a manual ``DELETE FROM guardian_lock`` clears
    the stale sentinel.

    Stale-lock auto-recovery (autonomous-chain-recovery): if the
    sentinel is older than ``STALE_TTL_SECONDS`` AND the holder
    PID is no longer alive (``os.kill(pid, 0)`` raises
    ``ProcessLookupError``), the sentinel is deleted and the
    caller re-acquires. This prevents a crashed worker from
    blocking the chain indefinitely. Single-flight for live
    holders is preserved (cross-user ``PermissionError`` is
    treated as "alive" to prevent stealing).
    """
    import sqlite3
    from datetime import datetime, timedelta

    con = sqlite3.connect(db_path, timeout=30.0)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "INSERT OR IGNORE INTO guardian_lock (id, holder_pid, started_at) "
            "VALUES (1, ?, datetime('now'))",
            (os.getpid(),),
        )
        row = con.execute(
            "SELECT holder_pid, started_at FROM guardian_lock WHERE id = 1"
        ).fetchone()
        if row is None:
            con.rollback()
            raise GuardianLocked("guardian_lock row missing after INSERT")
        if row[0] == os.getpid():
            # We already own it (re-acquire by same PID is a no-op).
            con.commit()
            return

        # Another worker holds the lock. Check if the holder is
        # stale — TTL exceeded AND pid is no longer alive.
        try:
            started_at = datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            started_at = datetime.utcnow()
        age_seconds = (datetime.utcnow() - started_at).total_seconds()

        pid_alive = True
        try:
            os.kill(row[0], 0)
        except ProcessLookupError:
            pid_alive = False
        except PermissionError:
            # Foreign PID we can't signal — treat as alive to
            # avoid cross-user lock stealing.
            pid_alive = True

        if age_seconds > STALE_TTL_SECONDS and not pid_alive:
            # Stale sentinel from a dead worker. Clear and re-acquire.
            con.execute("DELETE FROM guardian_lock WHERE id = 1")
            con.execute(
                "INSERT INTO guardian_lock (id, holder_pid, started_at) "
                "VALUES (1, ?, datetime('now'))",
                (os.getpid(),),
            )
            con.commit()
            _LOG.warning(
                "guardian.lock.stale_cleared holder_pid=%s age_seconds=%s",
                row[0],
                int(age_seconds),
            )
            # Pipeline observability: record the recovery so QA
            # can audit chain autonomy. Failure here is non-fatal.
            try:
                _sqlite_exec(
                    db_path,
                    "INSERT INTO pipeline (phase, started_at, finished_at, "
                    "rows_processed, status, detail) VALUES "
                    "('guardian_recovery', datetime('now'), "
                    "datetime('now'), 1, 'ok', ?)",
                    (
                        f"auto_cleared_stale_pid={row[0]} "
                        f"age_seconds={int(age_seconds)}",
                    ),
                )
            except Exception as exc:
                _LOG.warning(
                    "guardian_recovery.pipeline_insert_failed error=%s",
                    exc,
                )
            return

        # Live (or recent) holder — refuse. Do NOT force-take.
        con.rollback()
        raise GuardianLocked(
            f"guardian lock held by pid={row[0]} since {row[1]} "
            f"(age={int(age_seconds)}s, alive={pid_alive})"
        )
    except sqlite3.OperationalError as exc:
        # SQLITE_BUSY (5) on a held BEGIN IMMEDIATE — translate.
        con.rollback()
        raise GuardianLocked(f"guardian lock held by another worker: {exc}") from exc
    finally:
        con.close()


def _release_guardian_lock(db_path: str) -> None:
    """Release the lock by deleting the sentinel row.

    Deleting (rather than clearing the holder_pid) means a stale
    lock from a crashed worker that somehow committed-and-died is
    naturally gone on the next acquire attempt — the INSERT OR
    IGNORE succeeds. Operator can verify the lock is clear with
    ``SELECT COUNT(*) FROM guardian_lock``.
    """
    import sqlite3

    con = sqlite3.connect(db_path)
    try:
        con.execute("DELETE FROM guardian_lock WHERE id = 1 AND holder_pid = ?", (os.getpid(),))
        con.commit()
    finally:
        con.close()


async def run_daily_guardian(
    db_path: str, runner: BackfillRunner | None = None
) -> GuardianSummary:
    """Run the daily guardian: lock → universe → health → recovery → completeness.

    `runner` is provided by the caller (the systemd worker). When
    None, we build one from settings + the live broker client.

    Issue #5: a single-flight lock on ``guardian_lock`` serialises
    concurrent invocations. The second worker raises
    ``GuardianLocked`` rather than racing through the body and
    leaving phantom pipeline rows behind.
    """
    t0 = time.time()
    summary = GuardianSummary()

    _acquire_guardian_lock(db_path)
    try:
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
            _LOG.warning(
                "guardian.anomaly.stale_recovery_exhausted figi=%s",
                figi,
            )

        # 5. Completeness backfill pass — chain after recovery, before
        # the final summary row. Same `reports` dict feeds both passes;
        # the completeness pass only acts on figis with
        # INCOMPLETE_HISTORY, the rest are silent no-ops.
        completeness_summary = await run_completeness_pass(
            db_path, client, runner, reports,
        )

        # 6. Pipeline row for the completeness pass — same schema as
        # the guardian_daily row below (migration 002_pipeline.sql):
        # id, phase, started_at, finished_at, rows_processed, status, detail.
        _sqlite_exec(
            db_path,
            "INSERT INTO pipeline (phase, started_at, finished_at, rows_processed, status, detail) "
            "VALUES ('completeness_backfill', datetime('now'), datetime('now'), ?, 'ok', ?)",
            (
                completeness_summary.bars_added,
                f"examined={completeness_summary.figis_examined} "
                f"gaps={completeness_summary.gaps_found} "
                f"bars_added={completeness_summary.bars_added} "
                f"exhausted={completeness_summary.exhausted}",
            ),
        )

        # 7. Final summary pipeline row.
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
    finally:
        # Release on every exit path — success, exception, or cancellation.
        # A cycle that crashes without releasing would deadlock the next run
        # until the operator manually deletes the row.
        _release_guardian_lock(db_path)

    summary.duration_seconds = time.time() - t0
    return summary
