"""Worker entrypoint - separate process for Tinkoff data ingestion.

Two modes:
- scheduled: invoked by systemd timer at 23:00 MSK daily
- manual: invoked by POST /api/admin/fetch via subprocess
- backfill: invoked by systemd timer at 02:00 MSK for the full
  universe + historical bars lifecycle via BackfillRunner.

Exit codes:
- 0: success
- 1: token file missing or unreadable
- 2: phase failed (will be retried by systemd)

Usage:
    python -m algotrader_api.worker [scheduled|manual|backfill]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Ensure src/ is on sys.path when run directly: python worker.py
_SRC = Path(__file__).parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from algotrader_api.config import get_settings  # noqa: E402
from algotrader_api.db.migrations import MIGRATIONS_DIR  # noqa: E402
from algotrader_api.db import sqlite as sqlitedb  # noqa: E402
from algotrader_api.ingestion import (  # noqa: E402
    client as client_mod,
    pipeline as pipeline_mod,
)
from algotrader_api.observability.logging import get_logger, setup_logging  # noqa: E402
from algotrader_api.observability.tracing import setup_tracing, shutdown_tracing  # noqa: E402
from algotrader_api.pipeline.assertions import (
    assert_bars_increased,
    snapshot_bars_count,
)
from algotrader_api.ingestion.backfill import BackfillRunner  # noqa: E402,F401

logger = get_logger("algotrader_api.worker")


async def _async_noop_sink(_event) -> None:
    """No-op EventSink for the daily-chain BackfillRunner (drops events)."""
    return None


async def run_worker(mode: str) -> int:
    """Scheduled/manual mode: delegate to the BackfillRunner.

    Pre-`remove-duckdb-and-parquet`, this orchestrated a separate
    universe + `bars.run_bars_phase` (parquet writes) flow. The
    BackfillRunner now owns the whole lifecycle (universe discovery +
    bars writes into SQLite) so the worker just instantiates and
    drives it.
    """
    from algotrader_api.ingestion.backfill import BackfillRunner

    settings = get_settings()
    sqlitedb.run_migrations(settings.sqlite_path, MIGRATIONS_DIR)

    setup_logging(level=settings.log_level, health_sample_rate=1.0)  # no sampling in worker
    setup_tracing(
        service_name="algotrader-worker",
        otlp_endpoint=settings.otel_endpoint,
        resource_attributes={"mode": mode, "component": "data-fetch"},
    )

    logger.info("worker.start", mode=mode, sqlite=settings.sqlite_path)

    # Token check
    token = client_mod.read_token_file()
    if not token:
        logger.error("worker.token.missing")
        return 1

    # Build client (real or fake based on env)
    try:
        client = client_mod.make_client(sqlite_path=settings.sqlite_path)
    except RuntimeError as e:
        logger.error("worker.client.init_failed", error=str(e))
        return 2

    rc = 0
    try:
        run_id = pipeline_mod.start_phase(settings.sqlite_path, "fetch_universe_bars")
        events: list = []

        async def collect(ev):
            events.append(ev)

        runner = BackfillRunner(
            client=client,
            db_path=settings.sqlite_path,
            event_sink=collect,
        )
        try:
            await runner.run(
                history_years=settings.history_years,
                incremental_threshold_days=2,
            )
            pipeline_mod.end_phase(
                settings.sqlite_path,
                run_id,
                status="ok",
                rows_processed=runner.total_bars,
            )
        except Exception as e:
            logger.error("worker.run.failed", error=str(e))
            pipeline_mod.end_phase(
                settings.sqlite_path,
                run_id,
                status="err",
                detail=str(e),
            )
            rc = 2
    finally:
        try:
            await client.aclose()
        except Exception:
            pass
        shutdown_tracing()
        logger.info("worker.stop", exit_code=rc)

    return rc


def run_backfill() -> int:
    """Run the persistent backfill lifecycle once and exit.

    Designed for the systemd-timer mode: 02:00 MSK daily. Reads the
    broker token from the app's own SQLite secrets table (never env,
    never file), runs the runner with default settings, exits 0 on
    success or non-zero on failure (for systemd to retry).

    Live SSE is unnecessary here - systemd captures stdout/stderr via
    journald, and operator UI uses the HTTP /api/admin/backfill/status
    endpoint on the API server.
    """
    from algotrader_api.ingestion.client import make_client
    from algotrader_api.ingestion.backfill import BackfillRunner
    from algotrader_api.db.secrets import get_broker_token

    settings = get_settings()
    db_path = settings.sqlite_path

    token = get_broker_token(db_path)
    if not token:
        logger.error("worker.backfill.no_token")
        return 2  # distinct exit code so systemd can alert

    try:
        client = make_client(sqlite_path=db_path, use_fake=False)
    except RuntimeError as e:
        logger.error("worker.backfill.client_init_failed", error=str(e))
        return 3

    events: list = []

    async def collect(ev):
        events.append(ev)
        # Mirror to journald so operators see live progress without the UI.
        logger.info(
            "worker.backfill.event",
            type=ev.type,
            run_id=ev.run_id,
            payload=ev.payload,
        )

    runner = BackfillRunner(
        client=client,
        db_path=db_path,
        event_sink=collect,
    )

    async def _drive() -> int:
        try:
            await runner.run()
        except Exception as e:
            logger.error("worker.backfill.runner_failed", error=str(e))
            return 1
        # Final event tells us how it went.
        done = next((e for e in reversed(events) if e.type == "done"), None)
        status = (done.payload.get("status") if done else "unknown") if done else "unknown"
        tickers_done = done.payload.get("tickers_done", 0) if done else 0
        logger.info(
            "worker.backfill.complete",
            status=status,
            tickers_done=tickers_done,
        )
        return 0 if status == "ok" else 1

    return asyncio.run(_drive())


def main() -> int:
    parser = argparse.ArgumentParser(description="algotrader data-fetch worker")
    parser.add_argument(
        "mode",
        nargs="?",
        default="daily",
        choices=["scheduled", "manual", "backfill", "guardian", "daily"],
        help=(
            "Invocation mode. 'scheduled'/'manual' are one-shot fetch flows; "
            "'backfill' runs the persistent universe + historical backfill "
            "lifecycle (systemd-timer driven); 'guardian' runs the daily "
            "data-quality sweep; 'daily' runs the full refresh chain "
            "(migrations → universe sync → daily backfill → corporate "
            "actions → dividends → guardian) — the recommended cron mode."
        ),
    )
    args = parser.parse_args()
    if args.mode == "backfill":
        return run_backfill()
    if args.mode == "guardian":
        return run_guardian()
    if args.mode == "daily":
        return run_daily_chain()
    return asyncio.run(run_worker(args.mode))


async def _drive_guardian(db_path: str) -> int:
    """Run the daily guardian. Exit 1 if anomalies were raised so
    systemd can alert."""
    from algotrader_api.data_quality.service import run_daily_guardian

    summary = await run_daily_guardian(db_path)
    rc = 1 if summary.anomalies_raised else 0
    logger.info(
        "worker.guardian.complete",
        rc=rc,
        figis_checked=summary.figis_checked,
        figis_recovered=summary.figis_recovered,
        anomalies=summary.anomalies_raised,
        duration_seconds=summary.duration_seconds,
    )
    return rc


def run_guardian() -> int:
    """Daily data-quality sweep: universe sync → health → recovery → pipeline row."""
    settings = get_settings()
    sqlitedb.run_migrations(settings.sqlite_path, MIGRATIONS_DIR)

    setup_logging(level=settings.log_level, health_sample_rate=1.0)
    setup_tracing(
        service_name="algotrader-worker",
        otlp_endpoint=settings.otel_endpoint,
        resource_attributes={"mode": "guardian", "component": "data-quality"},
    )

    logger.info("worker.guardian.start", sqlite=settings.sqlite_path)

    try:
        rc = asyncio.run(_drive_guardian(settings.sqlite_path))
    finally:
        shutdown_tracing()
    return rc


# --------------------------------------------------------------------------- #
# Daily refresh chain — single cron entry that runs the whole pipeline
# --------------------------------------------------------------------------- #


_DAILY_CHAIN_PHASES = (
    "migrations",
    "universe_sync",
    "daily_backfill",
    "full_history",
    "gap_recovery",
    "corporate_actions",
    "dividends",
    "freshness_check",
    "guardian",
)


def _log_chain_phase(db_path: str, phase: str, result: str, *, detail: str = "") -> None:
    """Best-effort write to pipeline_log. Never raises."""
    import sqlite3
    import time
    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS pipeline_log ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "phase TEXT NOT NULL, "
                "started_at TEXT NOT NULL, "
                "finished_at TEXT NOT NULL, "
                "result TEXT NOT NULL, "
                "detail TEXT"
                ")"
            )
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            conn.execute(
                "INSERT INTO pipeline_log (phase, started_at, finished_at, result, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (phase, now, now, result, detail),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("worker.daily.pipeline_log_failed", phase=phase, error=str(exc))


def _step_migrations(db_path: str) -> tuple[bool, str]:
    try:
        sqlitedb.run_migrations(db_path, MIGRATIONS_DIR)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"migrations failed: {exc}"


def _step_universe_sync(db_path: str) -> tuple[bool, str]:
    """Real universe sync: discover tradeable instruments via broker
    and upsert into SQLite. The Data tab still offers a manual sync
    button for operator-triggered runs; the cron path uses the
    broker client configured in secrets.
    """
    try:
        from algotrader_api.ingestion import universe as _universe  # noqa: F401
        from algotrader_api.ingestion.universe_sync import run_universe_sync

        client = client_mod.make_client(sqlite_path=db_path, )
        rows = asyncio.run(run_universe_sync(db_path, client))
        return True, f"universe: {rows} instruments synced from broker"
    except Exception as exc:  # noqa: BLE001
        return False, f"universe sync failed: {exc}"


def _step_daily_backfill(db_path: str) -> tuple[bool, str]:
    """Append today's bar for every tradeable figi via BackfillRunner.

    Pre/post bars_count assertion: if the runner returns without
    adding bars (rate-limit, dead ticker, broker hiccup), the
    assertion fails and the chain aborts.
    """
    pre_count = snapshot_bars_count(db_path)
    try:
        from algotrader_api.ingestion.backfill import BackfillRunner

        client = client_mod.make_client(sqlite_path=db_path, )
        runner = BackfillRunner(client=client, db_path=db_path,
                                event_sink=_async_noop_sink)
        asyncio.run(runner.run(history_years=0,
                               incremental_threshold_days=1))
        pre, post, delta = assert_bars_increased(
            db_path, phase="daily_backfill", pre_count=pre_count,
        )
        return True, f"daily backfill: pre={pre} post={post} delta=+{delta}"
    except AssertionError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"daily backfill failed: {exc}"


def _step_full_history(db_path: str) -> tuple[bool, str]:
    """Walk every figi from first_bar_ts-30d to yesterday.

    Idempotent. The existing recover_stale logic in the guardian
    handles what this can't (tickers with no bars at all).
    """
    try:
        from algotrader_api.ingestion.backfill import BackfillRunner

        client = client_mod.make_client(sqlite_path=db_path, )
        runner = BackfillRunner(client=client, db_path=db_path,
                                event_sink=_async_noop_sink)
        count = asyncio.run(runner.run_full_history())
        return True, f"full history: {count} figis processed"
    except Exception as exc:
        return False, f"full history failed: {exc}"


def _step_gap_recovery(db_path: str) -> tuple[bool, str]:
    """Detect missing trading days per figi and fill them via
    BackfillRunner._backfill_one with explicit from_/to_."""
    try:
        from algotrader_api.data_quality.gap_recovery import (
            find_gaps, recover_gaps,
        )
        from algotrader_api.ingestion.backfill import BackfillRunner

        client = client_mod.make_client(sqlite_path=db_path, )
        runner = BackfillRunner(client=client, db_path=db_path,
                                event_sink=_async_noop_sink)
        gaps = find_gaps(db_path)
        if not gaps:
            return True, "gap recovery: no gaps"
        result = asyncio.run(recover_gaps(db_path, runner, gaps))
        return True, (
            f"gap recovery: {sum(result.values())} bars filled "
            f"across {len(gaps)} gaps"
        )
    except Exception as exc:
        return False, f"gap recovery failed: {exc}"


def _step_corporate_actions(db_path: str) -> tuple[bool, str]:
    """Re-run split derivation against the freshly-updated bars,
    then apply_all_pending to forward-adjust bars."""
    try:
        import importlib
        import sqlite3
        derive_splits = importlib.import_module(
            "algotrader_api.scripts_import.derive_splits"
        )
        from algotrader_api.data_quality.forward_adjustment import (
            apply_all_pending,
        )
        written = derive_splits.run_derivation(db_path)
        conn = sqlite3.connect(db_path)
        try:
            adjusted = apply_all_pending(conn)
            conn.commit()
        finally:
            conn.close()
        return True, f"splits derived={written} bars adjusted={adjusted}"
    except Exception as exc:
        return False, f"corporate actions failed: {exc}"


def _step_dividends(db_path: str) -> tuple[bool, str]:
    """Fetch and persist dividends from the Tinkoff investAPI.

    Aborts the chain when 0 rows land AND the table is empty or
    stale. Returns (True, detail) when 0 new rows land but recent
    data is present.
    """
    try:
        from algotrader_api.scripts_import.import_dividends_tinkoff import (
            fetch_and_persist,
        )
        from algotrader_api.dividends.freshness import (
            dividends_freshness_check,
        )
        client = client_mod.make_client(sqlite_path=db_path)
        written = fetch_and_persist(db_path, client=client)
        if written == 0:
            dividends_freshness_check(db_path, stale_threshold_days=7)
        return True, f"dividends: tinkoff={written} (no new)"
    except AssertionError as exc:
        return False, f"dividends stale: {exc}"
    except Exception as exc:
        return False, f"dividends failed: {exc}"


def _step_freshness_check(db_path: str) -> tuple[bool, str]:
    """Pipeline-level freshness assertion across all data domains.

    Raises if the most-recent chain run is older than 24h AND any
    domain is stale. The cron is daily; this is a sanity gate for
    operator awareness.
    """
    try:
        from algotrader_api.dividends.freshness import (
            pipeline_freshness_check,
        )
        pipeline_freshness_check(db_path, max_chain_age_hours=24)
        return True, "freshness: ok"
    except AssertionError as exc:
        return False, f"freshness: {exc}"
    except Exception as exc:
        return False, f"freshness failed: {exc}"


def _step_guardian(db_path: str) -> tuple[bool, str]:
    """Final health sweep + completeness pass."""
    try:
        from algotrader_api.data_quality.service import run_daily_guardian
        summary = asyncio.run(run_daily_guardian(db_path))
        return True, (
            f"guardian: checked={summary.figis_checked}, "
            f"recovered={summary.figis_recovered}, "
            f"anomalies={summary.anomalies_raised}"
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"guardian failed: {exc}"


_STEP_FUNCS = {
    "migrations": _step_migrations,
    "universe_sync": _step_universe_sync,
    "daily_backfill": _step_daily_backfill,
    "full_history": _step_full_history,
    "gap_recovery": _step_gap_recovery,
    "corporate_actions": _step_corporate_actions,
    "dividends": _step_dividends,
    "freshness_check": _step_freshness_check,
    "guardian": _step_guardian,
}


def run_daily_chain() -> int:
    """Run the full daily refresh chain. Exits 0 on success, non-zero on
    the first failed phase. Phases run strictly in order."""
    settings = get_settings()
    db_path = settings.sqlite_path

    setup_logging(level=settings.log_level, health_sample_rate=1.0)
    setup_tracing(
        service_name="algotrader-worker",
        otlp_endpoint=settings.otel_endpoint,
        resource_attributes={"mode": "daily", "component": "refresh-chain"},
    )

    logger.info("worker.daily.start", sqlite=db_path)

    rc = 0
    try:
        for phase in _DAILY_CHAIN_PHASES:
            logger.info("worker.daily.phase_start", phase=phase)
            step_fn = _STEP_FUNCS[phase]
            ok, detail = step_fn(db_path)
            result = "ok" if ok else "error"
            _log_chain_phase(db_path, phase, result, detail=detail)
            logger.info(
                "worker.daily.phase_complete",
                phase=phase,
                result=result,
                detail=detail,
            )
            if not ok:
                logger.error("worker.daily.phase_failed_aborting", phase=phase)
                rc = 1
                break
    finally:
        shutdown_tracing()

    logger.info("worker.daily.complete", rc=rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
