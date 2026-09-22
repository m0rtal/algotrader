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
import sqlite3
import sys
import threading
import time
from contextlib import closing
from datetime import date, timedelta
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
    "backfill_moex",      # replaces daily_backfill + full_history (MOEX ISS, dynamic listed_from→yesterday)
    "gap_recovery",
    "corporate_actions",
    "dividends",
    "freshness_check",
    "guardian",
)

# Phases whose failure aborts the rest of the chain. universe_sync and
# backfill_moex are the data-acquisition core — without them there is
# nothing to operate on, so subsequent phases would silently produce
# empty/incorrect results. The rest are best-effort: a failure in
# gap_recovery or dividends should still let the freshness_check and
# guardian run so the operator sees the real picture in the morning.
_CRITICAL_PHASES = frozenset({"migrations", "universe_sync", "backfill_moex"})


# Heartbeat for supervisor watchdog (autonomous-chain-recovery
# phase 2). Worker inserts a `pipeline` row every 5 min so the
# supervisor's 30-sec DB poll can detect a stalled process and
# SIGKILL it for restart. The constant is module-level so tests
# can monkey-patch it down to ~0.1s.
HEARTBEAT_INTERVAL_SECONDS = 300


def heartbeat_loop(db_path: str, interval_seconds: float) -> None:
    """Emit pipeline rows with phase='worker.heartbeat' every interval.

    Daemon-friendly: catches and logs all exceptions, never raises.
    Designed to run in a daemon thread started by main(); dies when
    the process exits.

    Tests call this directly with a short interval to assert ≥1 row
    is inserted.
    """
    import sqlite3
    while True:
        try:
            conn = sqlite3.connect(db_path, timeout=5.0)
            try:
                conn.execute(
                    "INSERT INTO pipeline (phase, started_at, "
                    "finished_at, rows_processed, status, detail) "
                    "VALUES ('worker.heartbeat', datetime('now'), "
                    "datetime('now'), 0, 'ok', ?)",
                    (f"pid={os.getpid()}",),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "worker.heartbeat.emit_failed error=%s", exc,
            )
        time.sleep(interval_seconds)


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


def _step_backfill_moex(db_path: str) -> tuple[bool, str]:
    """Walk every tradeable figi from MOEX listed_from to min(yesterday, listed_till).

    Replaces Tinkoff daily_backfill + full_history. MOEX ISS gives
    us ~13 years of history per ticker vs. Tinkoff's ~5. Insertion
    is INSERT OR IGNORE so existing Tinkoff bars (2021+) are preserved.
    Sanctions-delisted tickers where MOEX has no boards fall back to
    Tinkoff sandbox.

    Delta-fetch is the default: only figis whose earliest bar is later
    than MOEX's listed_from get processed. New figis (added by
    universe_sync) get a full walk.
    """
    pre_count = snapshot_bars_count(db_path)
    try:
        from algotrader_api.ingestion.backfill import BackfillRunner

        client = client_mod.make_client(sqlite_path=db_path)
        runner = BackfillRunner(
            client=client,
            db_path=db_path,
            event_sink=_async_noop_sink,
        )
        written = asyncio.run(
            runner.backfill_from_moex(recent_tail_days=5)
        )
        pre, post, delta = assert_bars_increased(
            db_path, phase="backfill_moex", pre_count=pre_count,
        )
        return True, f"backfill_moex: pre={pre} post={post} delta=+{delta}"
    except AssertionError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"backfill_moex failed: {exc}"


def _load_holiday_dates(
    con: sqlite3.Connection, start: date, end: date,
) -> set[date]:
    """Return the set of MOEX public-holiday dates in [start, end].

    Mirrors the same query used by ``find_gaps`` in data_quality.gap_recovery
    so the trailing-gap pass agrees with the historical-gap pass on what
    counts as a non-trading day. Returns an empty set if the table is
    missing (older schemas) so the recovery step degrades gracefully.
    """
    try:
        rows = con.execute(
            "SELECT date FROM moex_holidays WHERE date BETWEEN ? AND ?",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    except sqlite3.OperationalError:
        # Table not yet migrated -> treat as "no holidays known".
        return set()
    return {date.fromisoformat(r["date"]) for r in rows}


def _load_restricted_dates(
    con: sqlite3.Connection, start: date, end: date,
) -> set[date]:
    """Return the set of restricted_periods dates in [start, end]."""
    rows = con.execute(
        "SELECT date FROM restricted_periods WHERE date BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return {date.fromisoformat(r["date"]) for r in rows}


def _trailing_trading_days(
    last_ts: date,
    today: date,
    holidays: set[date],
    restricted: set[date],
) -> list[date]:
    """Return the trading days strictly after ``last_ts`` up to ``today``.

    A "trading day" is a weekday that is neither a MOEX holiday nor a
    restricted period. The returned list is sorted ascending and may be
    empty when ``last_ts >= today``.
    """
    if last_ts >= today:
        return []
    out: list[date] = []
    cur = last_ts + timedelta(days=1)
    while cur <= today:
        if cur.weekday() < 5 and cur not in holidays and cur not in restricted:
            out.append(cur)
        cur += timedelta(days=1)
    return out


def _collect_trailing_gaps(
    db_path: str, today: date,
) -> list[tuple[str, date, date, bool]]:
    """For every figi with bars, find the trailing missing-trading-days block.

    Returns a list of ``(figi, from_, to_, stale)`` tuples where ``stale``
    is True when ``last_ts < today - 7 days`` (the brief: prefer MOEX ISS
    for those figis instead of Tinkoff).

    The window is (last_ts, today] — strictly past the latest bar — so we
    never duplicate work that the historical ``find_gaps`` already covers.
    Returns an empty list when every figi is already up to date, or when
    the schema isn't present (e.g. during unit-test fixtures that only
    mock find_gaps/recover_gaps).

    Defensive against missing ``bars`` / ``moex_holidays`` /
    ``restricted_periods`` tables — the trailing pass must not break
    callers that mock gap_recovery but use a bare sqlite file.
    """
    with closing(sqlite3.connect(db_path)) as con:
        try:
            rows = con.execute(
                "SELECT figi, MAX(ts) AS last_ts FROM bars GROUP BY figi"
            ).fetchall()
        except sqlite3.OperationalError:
            # No bars table yet -> nothing to trail.
            return []
        if not rows:
            return []
        # Pull holiday + restricted calendars once for the whole (last_ts..today) span.
        earliest = min(date.fromisoformat(r["last_ts"]) for r in rows)
        holidays = _load_holiday_dates(con, earliest, today)
        restricted = _load_restricted_dates(con, earliest, today)

    out: list[tuple[str, date, date, bool]] = []
    for r in rows:
        last_ts = date.fromisoformat(r["last_ts"])
        trading_days = _trailing_trading_days(
            last_ts, today, holidays, restricted,
        )
        if not trading_days:
            continue
        stale = last_ts < (today - timedelta(days=7))
        out.append((r["figi"], trading_days[0], trading_days[-1], stale))
    return out


def _step_gap_recovery(db_path: str) -> tuple[bool, str]:
    """Detect missing trading days per figi and fill them via
    BackfillRunner._backfill_one with explicit from_/to_.

    Two passes run in sequence:
      1. Historical gaps via ``find_gaps`` / ``recover_gaps`` — covers
         any missing days inside [min(ts), max(ts)] for each figi.
      2. Trailing gaps — covers missing days strictly after ``max(ts)``
         up to today. Weekend + MOEX-holiday + restricted-period days
         are filtered out so we never burn a Tinkoff call on a closed
         exchange day. When ``last_ts < today - 7 days`` the trailing
         window is routed to MOEX ISS (cheaper, no rate-limit pressure)
         instead of Tinkoff.
    """
    try:
        from algotrader_api.data_quality.gap_recovery import (
            find_gaps, recover_gaps,
        )
        from algotrader_api.ingestion.backfill import BackfillRunner

        client = client_mod.make_client(sqlite_path=db_path, )
        runner = BackfillRunner(client=client, db_path=db_path,
                                event_sink=_async_noop_sink)
        gaps = find_gaps(db_path)
        if gaps:
            result = asyncio.run(recover_gaps(db_path, runner, gaps))
            hist_added = sum(result.values())
        else:
            hist_added = 0

        # Trailing gap pass — fetch days strictly after each figi's last bar.
        today = date.today()
        trailing = _collect_trailing_gaps(db_path, today)
        trailing_added = 0
        trailing_by_source: dict[str, int] = {"moex": 0, "tinkoff": 0}
        if trailing:
            # Resolve tickers once for all figis in the trailing set.
            figis = [t[0] for t in trailing]
            ticker_by_figi: dict[str, str] = {}
            con = sqlite3.connect(db_path)
            try:
                placeholders = ",".join("?" for _ in figis)
                ticker_rows = con.execute(
                    f"SELECT figi, ticker FROM instruments "
                    f"WHERE figi IN ({placeholders})",
                    tuple(figis),
                ).fetchall()
                ticker_by_figi = {r["figi"]: r["ticker"] for r in ticker_rows}
            finally:
                con.close()

            logger.info(
                "worker.gap_recovery.trailing",
                n_figis=len(trailing),
                dates=[(f, f_.isoformat(), t_.isoformat(), s)
                       for f, f_, t_, s in trailing],
            )

            async def _fill_trailing() -> dict[str, int]:
                added_total: dict[str, int] = {}
                for figi, from_, to_, stale in trailing:
                    ticker = ticker_by_figi.get(figi)
                    if ticker is None:
                        logger.warning(
                            "worker.gap_recovery.no_ticker",
                            figi=figi,
                        )
                        continue
                    source = "moex" if stale else "tinkoff"
                    logger.info(
                        "worker.gap_recovery.fill",
                        figi=figi, ticker=ticker,
                        from_=from_.isoformat(), to=to_.isoformat(),
                        source=source, stale=stale,
                    )
                    try:
                        n = await runner._backfill_one(
                            figi=figi, ticker=ticker,
                            from_=from_, to=to_, source=source,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "worker.gap_recovery.fill_failed",
                            figi=figi, error=str(exc),
                        )
                        continue
                    added_total[figi] = added_total.get(figi, 0) + int(n or 0)
                    trailing_by_source[source] += int(n or 0)
                return added_total

            trailing_added_total = asyncio.run(_fill_trailing())
            trailing_added = sum(trailing_added_total.values())

        total_added = hist_added + trailing_added
        if not gaps and not trailing:
            return True, "gap recovery: no gaps"
        return True, (
            f"gap recovery: {total_added} bars filled "
            f"(historical={hist_added} across {len(gaps)} gaps, "
            f"trailing={trailing_added} across {len(trailing)} figis "
            f"[moex={trailing_by_source['moex']}, "
            f"tinkoff={trailing_by_source['tinkoff']}])"
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
    "backfill_moex": _step_backfill_moex,
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

    # Heartbeat daemon (autonomous-chain-recovery phase 2):
    # supervisor polls the DB every 30s for heartbeat freshness and
    # SIGKILLs us if we stop emitting. Daemon thread dies with main.
    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        args=(db_path, HEARTBEAT_INTERVAL_SECONDS),
        daemon=True,
        name="worker-heartbeat",
    )
    heartbeat_thread.start()

    rc = 0
    failed_phases: list[str] = []
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
                failed_phases.append(phase)
                if phase in _CRITICAL_PHASES:
                    logger.error(
                        "worker.daily.critical_phase_failed_aborting",
                        phase=phase,
                        detail=detail,
                    )
                    rc = 1
                    break
                # Best-effort phase: log + continue so the operator
                # still gets freshness_check + guardian verdicts.
                logger.warning(
                    "worker.daily.best_effort_phase_failed_continuing",
                    phase=phase,
                    detail=detail,
                )
    finally:
        shutdown_tracing()

    # Non-zero if any best-effort phase failed even though we continued.
    # Systemd uses this to flag the chain as degraded.
    if rc == 0 and failed_phases:
        rc = 1

    logger.info(
        "worker.daily.complete",
        rc=rc,
        failed_phases=failed_phases,
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
