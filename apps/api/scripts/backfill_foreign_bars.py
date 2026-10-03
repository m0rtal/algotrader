#!/usr/bin/env python
"""Catch-up fetcher for broker-only instruments (no MOEX board).

Why this exists
---------------
The daily worker's Tinkoff fallback phase runs after a long MOEX walk
on a single long-lived gRPC channel. Observed repeatedly on 2026-10-01:
every fallback fetch timed out (2003 `tinkoff_rpc_timeout` entries) —
the channel does not survive the idle stretch + concurrent load, and
in-process channel resets did not recover it. The same fetches from a
FRESH process run at ~0.5 s per instrument (40/40 verified).

This script is that fresh process: it fetches bars for two cohorts of
broker-only instruments (no MOEX primary board):

* stale — last bar before the last completed session, and
* incomplete — fresh tail but bars < 95% of the cached expected_bars
  (early-history or mid-history gaps).

It uses the same helpers as the daily worker (identity, windows, writer,
and the half-open date_to correction). `--days` controls the trailing
window depth (90 for the 30-min freshness cron; use a large value like
4000 for an occasional full-history sweep). It is idempotent
(INSERT OR IGNORE).

Usage:
    python apps/api/scripts/backfill_foreign_bars.py --dry-run --limit 20
    python apps/api/scripts/backfill_foreign_bars.py                 # freshness pass
    python apps/api/scripts/backfill_foreign_bars.py --days 4000     # history sweep
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

API_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(API_SRC))

from algotrader_api.db.bars_sqlite import replace_bars_for_figi, get_connection  # noqa: E402
from algotrader_api.ingestion import retry as retry_mod  # noqa: E402
from algotrader_api.ingestion.backfill import (  # noqa: E402
    _fetch_tinkoff_fallback_impl,
    _get_meta_moex,
    _last_trading_day,
)
from algotrader_api.ingestion.client import make_client  # noqa: E402
from algotrader_api.ingestion.writer_lock import WriterLockBusy  # noqa: E402


def _format_defer(exc: "WriterLockBusy") -> str:
    """Render a single bounded ``DEFER writer-lock-busy ...`` line.

    Carries only the safe metadata exposed by
    :class:`WriterLockBusy` (role, phase, timeout, reason,
    result). Must not include the database path, figi, ticker,
    or any secret. Used by the CLI when the shared writer lock
    acquisition times out inside ``replace_bars_for_figi``.
    """
    return (
        f"DEFER writer-lock-busy role={exc.role} phase={exc.phase} "
        f"reason={exc.reason} timeout={exc.timeout_seconds:g}s "
        f"result={exc.result}"
    )

DEFAULT_DB = "/home/hermes/algotrader/apps/api/data/state.db"
DEFAULT_DAYS = 90
COVERAGE_THRESHOLD = 0.95
FETCH_TIMEOUT_S = 900


async def run(db_path: str, limit: int, dry_run: bool, sleep_s: float,
              reopen_every: int, days: int) -> int:
    last_session = _last_trading_day(date.today(), db_path)
    floor = last_session - timedelta(days=days)

    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT i.figi, i.ticker, i.expected_bars,
                  (SELECT MAX(ts) FROM bars WHERE figi = i.figi) AS max_ts,
                  (SELECT COUNT(*) FROM bars WHERE figi = i.figi) AS bars_count
           FROM instruments i
           WHERE i.class IN ('share', 'etf', 'bond')"""
    ).fetchall()
    # Two cohorts need help:
    #  1. stale: last bar before the last completed session;
    #  2. incomplete: fresh tail but bars < 95% of expected (mid-history
    #     or early-history gaps, e.g. an ETF with only its last year).
    todo = []
    for r in rows:
        if not r["max_ts"]:
            continue
        stale = r["max_ts"] < last_session.isoformat()
        exp = r["expected_bars"] or 0
        incomplete = exp > 0 and r["bars_count"] < COVERAGE_THRESHOLD * exp
        if stale or incomplete:
            todo.append(r)
    if limit:
        todo = todo[:limit]
    print(f"candidates (stale+incomplete): {len(todo)} "
          f"(last_session={last_session}, window_days={days})")

    meta_cache: dict = {}
    meta_lock = threading.Lock()

    # Pre-filter: only instruments without a MOEX primary board go
    # through the Tinkoff path here. Those WITH a board are the daily
    # worker's MOEX walk.
    tinkoff_only = []
    for r in todo:
        ticker = r["ticker"] or ""
        if not ticker:
            continue
        meta = _get_meta_moex(ticker, last_session,
                              meta_cache=meta_cache, meta_lock=meta_lock)
        if meta is None:
            tinkoff_only.append(r)
    print(f"broker-only (no MOEX board): {len(tinkoff_only)}")

    fetched = written = errors = reopened = 0
    client = make_client(sqlite_path=db_path)
    per_client = 0
    try:
        for i, r in enumerate(tinkoff_only, 1):
            if per_client >= reopen_every:
                # Reopen periodically: bounds any channel degradation
                # that accumulates over a long run.
                try:
                    await client.aclose()
                except Exception:
                    pass
                client = make_client(sqlite_path=db_path)
                per_client = 0
                reopened += 1
            per_client += 1
            figi, ticker = r["figi"], r["ticker"]
            # Window: cut at the last bar only when the tail is stale;
            # for a fresh-but-incomplete figi the gap is elsewhere in
            # history, so fetch the whole trailing window (duplicate
            # inserts are ignored cheaply).
            if r["max_ts"] >= last_session.isoformat():
                lo = floor
            else:
                lo = max(floor, date.fromisoformat(r["max_ts"]) + timedelta(days=1))
            if lo > last_session:
                continue
            try:
                bars = await asyncio.wait_for(
                    _fetch_tinkoff_fallback_impl(
                        client, retry_mod, figi, ticker, lo, last_session,
                    ),
                    # Generous per-figi budget: a 4000-day window is up
                    # to ~570 chunks x ~0.15 s ≈ 85 s of legitimate work.
                    # 45 s killed such figis mid-fetch and discarded all
                    # in-memory rows (observed: KBWB lost its 2015-2021
                    # downloads to the old 45-s cap).
                    timeout=FETCH_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                errors += 1
                print(f"  [{i}/{len(tinkoff_only)}] {ticker}: timeout "
                      f"{FETCH_TIMEOUT_S}s; reopening client")
                # A stuck fetch may indicate a poisoned channel; rebuild.
                try:
                    await client.aclose()
                except Exception:
                    pass
                client = make_client(sqlite_path=db_path)
                per_client = 0
                reopened += 1
                continue
            except Exception as e:  # noqa: BLE001 — keep walking
                errors += 1
                print(f"  [{i}/{len(tinkoff_only)}] {ticker}: {type(e).__name__} {e!s:.80}")
                continue
            if not bars:
                continue
            fetched += 1
            if dry_run:
                print(f"  [dry] {ticker}: {len(bars)} bars "
                      f"({bars[0]['ts']}..{bars[-1]['ts']})")
                written += len(bars)
                continue
            # Coordination (Task 5): the shared writer lock is
            # acquired INSIDE ``replace_bars_for_figi``; when it
            # times out the helper raises ``WriterLockBusy`` (a
            # subclass of ``WriterLockError``, not the regular
            # SQLite busy). We catch it at this CLI boundary
            # and exit 75 with a bounded DEFER line. Other
            # ``WriterLockError`` (invalid role/phase, unsafe
            # lock path) is a real failure and propagates — it
            # must never be turned into a silent deferral.
            last_err = None
            for attempt in range(6):
                try:
                    replace_bars_for_figi(db_path, figi, bars, replace=False)
                    last_err = None
                    break
                except WriterLockBusy as exc:
                    print(_format_defer(exc))
                    return 75
                except sqlite3.OperationalError as e:
                    last_err = e
                    time.sleep(5 * (attempt + 1))
            if last_err is not None:
                errors += 1
                print(f"  [{i}/{len(tinkoff_only)}] {ticker}: DB write failed "
                      f"after retries: {last_err!s:.80}")
                continue
            written += len(bars)
            if sleep_s:
                time.sleep(sleep_s)
    finally:
        try:
            await client.aclose()
        except Exception:
            pass
        con.close()

    print(f"done: fetched={fetched} rows={written} errors={errors} "
          f"client_reopens={reopened}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.0)
    ap.add_argument("--reopen-every", type=int, default=200,
                    help="rebuild the broker client every N fetched instruments")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help="trailing window depth in calendar days "
                         "(default %(default)s; use e.g. 4000 for a full-history sweep)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not Path(args.db).exists():
        print(f"ERROR: {args.db} does not exist", file=sys.stderr)
        return 2
    return asyncio.run(run(args.db, args.limit, args.dry_run,
                           args.sleep, args.reopen_every, args.days))


if __name__ == "__main__":
    sys.exit(main())
