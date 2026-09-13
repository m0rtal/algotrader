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

logger = get_logger("algotrader_api.worker")


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
        default="scheduled",
        choices=["scheduled", "manual", "backfill"],
        help=(
            "Invocation mode. 'scheduled'/'manual' are one-shot fetch flows; "
            "'backfill' runs the persistent universe + historical backfill "
            "lifecycle (systemd-timer driven)."
        ),
    )
    args = parser.parse_args()
    if args.mode == "backfill":
        return run_backfill()
    return asyncio.run(run_worker(args.mode))


if __name__ == "__main__":
    sys.exit(main())
