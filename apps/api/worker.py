"""Worker entrypoint — separate process for Tinkoff data ingestion.

Two modes:
- scheduled: invoked by systemd timer at 23:00 MSK daily
- manual: invoked by POST /api/admin/fetch via subprocess

Exit codes:
- 0: success
- 1: token file missing or unreadable
- 2: phase failed (will be retried by systemd)

Usage:
    python -m algotrader_api.worker [scheduled|manual]
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
    bars,
    client as client_mod,
    pipeline as pipeline_mod,
    rate_limit,
    retry,
    universe,
)
from algotrader_api.observability.correlation import correlation_id  # noqa: E402
from algotrader_api.observability.logging import get_logger, setup_logging  # noqa: E402
from algotrader_api.observability.tracing import setup_tracing, shutdown_tracing  # noqa: E402

logger = get_logger("algotrader_api.worker")


async def run_worker(mode: str) -> int:
    settings = get_settings()
    sqlitedb.run_migrations(settings.sqlite_path, MIGRATIONS_DIR)

    # Initialize observability (correlation_id is auto-assigned per run)
    setup_logging(level=settings.log_level, health_sample_rate=1.0)  # no sampling in worker
    setup_tracing(
        service_name="algotrader-worker",
        otlp_endpoint=settings.otel_endpoint,
        resource_attributes={"mode": mode, "component": "data-fetch"},
    )

    logger.info("worker.start", mode=mode, sqlite=settings.sqlite_path, bars=settings.bars_dir)

    # Token check
    token = client_mod.read_token_file()
    if not token:
        logger.error("worker.token.missing")
        return 1

    # Build client (real or fake based on env)
    try:
        client = client_mod.make_client()
    except RuntimeError as e:
        logger.error("worker.client.init_failed", error=str(e))
        return 2

    rate_limiter = rate_limit.RateLimiter()
    retry_policy = retry.AdaptiveRetry()

    rc = 0
    try:
        # Phase 1: discover universe
        universe_run = pipeline_mod.start_phase(settings.sqlite_path, "discover_universe")
        try:
            universe_rows = await universe.discover_universe(client)
            universe.upsert_instruments(settings.sqlite_path, universe_rows)
            pipeline_mod.end_phase(
                settings.sqlite_path,
                universe_run,
                status="ok",
                rows_processed=len(universe_rows),
            )
        except Exception as e:
            logger.error("worker.universe.failed", error=str(e))
            pipeline_mod.end_phase(
                settings.sqlite_path,
                universe_run,
                status="err",
                detail=str(e),
            )
            rc = 2

        # Phase 2: fetch bars (only if universe succeeded)
        if rc == 0:
            bars_run = pipeline_mod.start_phase(settings.sqlite_path, "fetch_bars")
            try:
                # Read instruments from SQLite
                all_rows = sqlitedb.execute(
                    settings.sqlite_path,
                    "SELECT ticker, figi, class FROM instruments",
                )
                instruments = [dict(r) for r in all_rows]
                total_rows, rate_hits = await bars.run_bars_phase(
                    client,
                    instruments=instruments,
                    bars_dir=settings.bars_dir,
                    history_years=settings.history_years,
                    rate_limiter=rate_limiter,
                    retry_policy=retry_policy,
                )
                pipeline_mod.end_phase(
                    settings.sqlite_path,
                    bars_run,
                    status="ok",
                    rows_processed=total_rows,
                    detail=f"rate_limit_hits={rate_hits}",
                )
            except Exception as e:
                logger.error("worker.bars.failed", error=str(e))
                pipeline_mod.end_phase(
                    settings.sqlite_path,
                    bars_run,
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


def main() -> int:
    parser = argparse.ArgumentParser(description="algotrader data-fetch worker")
    parser.add_argument(
        "mode",
        nargs="?",
        default="scheduled",
        choices=["scheduled", "manual"],
        help="Invocation mode",
    )
    args = parser.parse_args()
    return asyncio.run(run_worker(args.mode))


if __name__ == "__main__":
    sys.exit(main())
