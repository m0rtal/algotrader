"""FastAPI app factory with lifespan and observability wiring."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from .config import get_settings
from .db import duck, sqlite as sqlitedb
from .db.migrations import MIGRATIONS_DIR
from .observability.correlation import CorrelationMiddleware
from .observability.logging import get_logger, setup_logging
from .observability.middleware import LatencyMiddleware
from .observability.tracing import setup_tracing, shutdown_tracing
from .routes import admin, bars, data_reads, health, pipeline as pipeline_route, settings as settings_route, signals as signals_route
from .seed import seed_bars, should_seed_synth

logger = get_logger("algotrader_api.main")


def _parse_resource_attributes(s: str) -> dict[str, str]:
    return dict(item.split("=", 1) for item in s.split(",") if "=" in item)


def _flag_orphan_ok_rows(bars_dir: str, sqlite_path: str) -> int:
    """Mark `instrument_metadata` rows as error if no parquet bars exist.

    Earlier broken backfill runs (e.g. run_id=27 from the sandbox→live
    transition) wrote `status='ok'` rows to `instrument_metadata`
    without actually persisting bars — either because `_extract_last_bar_ts`
    failed to handle the SDK's pre-converted dict candles and bailed early,
    or because the row was written to a `null` legacy parquet file.
    Those rows lie to the pending counter (which counts them as
    `up_to_date`) and to the Bars tab (which counts them in `Bars on
    disk` once a `total_bars` value was rounded). On backend startup
    we reconcile: anything with `status='ok'` and `total_bars > 0` that
    has zero bars in the DuckDB view gets flipped to `error` so the
    next backfill run will retry it.
    """
    from .db import duck as duck_mod
    from .db.sqlite import execute as _exec

    conn = duck_mod.get_connection(bars_dir)
    have_bars_rows = conn.execute(
        "SELECT DISTINCT ticker FROM bars WHERE ticker IS NOT NULL"
    ).fetchall()
    have_bars = {r[0] for r in have_bars_rows if r and r[0]}

    rows = _exec(
        sqlite_path,
        "SELECT m.figi, i.ticker "
        "FROM instrument_metadata m "
        "JOIN instruments i ON i.figi = m.figi "
        "WHERE m.last_run_status='ok'",
        (),
    )
    orphan_ids = [
        figi for figi, ticker in rows if ticker not in have_bars
    ]
    if not orphan_ids:
        return 0
    placeholders = ",".join(["?"] * len(orphan_ids))
    _exec(
        sqlite_path,
        f"UPDATE instrument_metadata "
        f"SET last_run_status='error', last_error='bars_missing_on_disk' "
        f"WHERE figi IN ({placeholders})",
        tuple(orphan_ids),
    )
    return len(orphan_ids)


def create_app() -> FastAPI:
    """Build the FastAPI app (factory pattern for testing)."""
    settings = get_settings()

    # Observability setup before app starts
    setup_logging(
        level=settings.log_level,
        log_format="json",
        health_sample_rate=settings.log_sample_health,
    )
    setup_tracing(
        service_name=settings.otel_service_name,
        otlp_endpoint=settings.otel_endpoint,
        resource_attributes=_parse_resource_attributes(settings.otel_resource_attributes),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Ensure data dirs exist
        Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
        Path(settings.bars_dir).mkdir(parents=True, exist_ok=True)

        # Configure DB layer with runtime paths and apply schema migrations
        try:
            sqlitedb.run_migrations(settings.sqlite_path, MIGRATIONS_DIR)
            logger.info("migrations.applied", path=settings.sqlite_path)
        except Exception as e:
            logger.error("migrations.failed", error=str(e), path=settings.sqlite_path)
            raise
        duck.get_connection(settings.bars_dir)  # warm DuckDB connection

        # Integrity check: any `instrument_metadata` row that claims `ok`
        # but the corresponding parquet file has no bars (or has the
        # NULL-ticker legacy row) is false-positive bookkeeping left over
        # from earlier broken runs. Flip them to `error` so the pending
        # counter classifies them for retry on the next run instead of
        # silently satisfying the UI as "up to date".
        try:
            _flag_orphan_ok_rows(settings.bars_dir, settings.sqlite_path)
        except Exception as e:  # noqa: BLE001
            logger.warn("backfill.orphan_check_failed", error=str(e))

        # Seed synthetic bars only when:
        # - no parquet files exist yet, AND
        # - ALGOTRADER_SYNTH_SEED is set OR the Tinkoff token file is missing
        #   (dev environment without secrets).
        existing = list(Path(settings.bars_dir).glob("*.parquet"))
        if not existing and should_seed_synth():
            logger.info("seed.start", bars_dir=settings.bars_dir)
            n = seed_bars(settings.bars_dir)
            logger.info("seed.done", bars_written=n)
            # Re-init DuckDB so the view picks up the freshly written parquet files
            duck.close()
            duck.get_connection(settings.bars_dir)
        elif not existing:
            logger.info(
                "seed.skipped",
                reason="no parquet files but token file present; worker will populate on next cron tick",
            )
            duck.get_connection(settings.bars_dir)

        # Wire route paths
        settings_route.set_sqlite_path(settings.sqlite_path)
        health.set_sqlite_path(settings.sqlite_path)
        health.set_bars_dir(settings.bars_dir)
        bars.set_bars_dir(settings.bars_dir)
        data_reads.set_bars_dir(settings.bars_dir)
        data_reads.set_sqlite_path(settings.sqlite_path)

        logger.info("service.start", host=settings.api_host, port=settings.api_port)
        yield
        logger.info("service.stop")
        duck.close()
        sqlitedb.close_all()
        shutdown_tracing()

    app = FastAPI(
        title="algotrader-api",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Middleware order matters — correlation first so other middlewares can read ID
    app.add_middleware(LatencyMiddleware)
    app.add_middleware(CorrelationMiddleware)

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Correlation-ID", "X-Latency-Ms"],
    )

    # Routes
    app.include_router(health.router)
    app.include_router(settings_route.router)
    app.include_router(bars.router)
    app.include_router(pipeline_route.router)
    app.include_router(signals_route.router)
    app.include_router(data_reads.router)
    app.include_router(admin.router)
    from .routes.backfill import router as backfill_router
    app.include_router(backfill_router)

    # OpenTelemetry FastAPI instrumentation
    FastAPIInstrumentor.instrument_app(app)

    return app


app = create_app()
