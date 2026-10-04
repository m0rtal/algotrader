#!/usr/bin/env python
"""One-shot: backfill historical no-trade evidence for stale figis.

Why: the recent-tail pass records confirmed zero-trade sessions only for
the trailing few days. Instruments that stopped trading weeks or months
ago (dormant or delisted bonds, delisted series) keep a stale
`MAX(bars.ts)` and keep failing the ML coverage gate, even though MOEX
ISS can confirm that every session between their last bar and their last
board listing was a no-trade day.

Two cases, both handled here:

1. Still-listed instruments (`_get_meta_moex` finds a primary board):
   fetch the window between each figi's last bar and the last completed
   session, extract confirmed zero-trade rows, record evidence.
2. Delisted instruments (no `is_traded=1` board; the latest
   `listed_till` is in the past): store `instruments.listed_till` (used
   by the coverage gate as the effective end of expected sessions) and
   record evidence for the window up to the delisting date.

Real bars always win: `record_no_trade_evidence` skips any
(figi, date) that already has a bar. Read-only with respect to `bars`;
writes only `moex_no_trade_evidence` and `instruments.listed_till`.

Usage:
    python apps/api/scripts/backfill_no_trade_evidence.py --dry-run --limit 20
    python apps/api/scripts/backfill_no_trade_evidence.py            # full run
    python apps/api/scripts/backfill_no_trade_evidence.py --days 400 # wider window
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import threading
import time
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

API_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(API_SRC))

from algotrader_api.ingestion.backfill import (  # noqa: E402
    _fetch_moex_range,
    _fetch_year_moex_outcome,
    _get_meta_moex,
    _last_trading_day,
    _reduce_outcomes,
)
from algotrader_api.ingestion.no_trade_evidence import (  # noqa: E402
    _extract_zero_trade_rows,
    _moex_session,
    record_historical_no_trade_evidence,
    record_no_trade_evidence,
)
from algotrader_api.ingestion.writer_lock import (  # noqa: E402
    WriterLockBusy,
    format_busy_defer,
    is_sqlite_busy,
    writer_lock,
    writer_lock_path,
)

DEFAULT_DB = "/home/hermes/algotrader/apps/api/data/state.db"

# MOEX bond boards (URL market segment) — anything else is the shares
# segment. Extend if MOEX adds new bond boards.
_BONDS_BOARDS = {
    "TQOB", "TQCB", "TQIR", "TQRD", "TQOD", "TQBD", "TQBY", "TQOU",
    "TQOY", "TQBE", "TQBS",
}


def _probe_board_last(ticker: str) -> tuple[str, str] | None:
    """Return ``(board, listed_till)`` for the board with the LATEST
    ``listed_till`` across every MOEX board of ``ticker``.

    Used for delisted instruments where no ``is_traded=1`` board exists.
    Returns ``None`` on any upstream failure or when no board carries a
    usable ``listed_till``.
    """
    url = f"https://iss.moex.com/iss/securities/{urllib.parse.quote(ticker)}.json"
    try:
        data = _moex_session().get(url, timeout=(5, 30)).json()
    except Exception:
        return None
    boards_block = data.get("boards", {})
    boards = boards_block.get("data") or []
    cols = boards_block.get("columns") or []
    if not boards or not cols:
        return None
    idx = {c: i for i, c in enumerate(cols)}
    if "boardid" not in idx or "listed_till" not in idx:
        return None
    best: tuple[str, str] | None = None
    for b in boards:
        lt = b[idx["listed_till"]]
        if not lt:
            continue
        lt_s = str(lt)[:10]
        if best is None or lt_s > best[1]:
            best = (str(b[idx["boardid"]]), lt_s)
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=0,
                    help="process at most N stale figis (0 = all)")
    ap.add_argument("--days", type=int, default=365,
                    help="look back at most N calendar days (default 365)")
    ap.add_argument("--sleep", type=float, default=0.25,
                    help="pause between figis to be polite to MOEX ISS")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: {db_path} does not exist", file=sys.stderr)
        return 2

    con = sqlite3.connect(str(db_path), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        last_session = _last_trading_day(date.today(), str(db_path))
        floor = last_session - timedelta(days=args.days)
        rows = con.execute(
            """SELECT i.figi, i.ticker, i.isin,
                      (SELECT MAX(ts) FROM bars WHERE figi = i.figi) AS max_ts
               FROM instruments i
               WHERE i.class IN ('share', 'etf', 'bond')"""
        ).fetchall()
        # Stale = has bars but none on/after the last completed session,
        # and within the lookback window.
        todo = []
        for r in rows:
            if not r["max_ts"] or r["max_ts"] >= last_session.isoformat():
                continue
            if r["max_ts"] < floor.isoformat():
                continue
            todo.append(r)
        if args.limit:
            todo = todo[: args.limit]

        print(f"stale figis in window: {len(todo)} "
              f"(from {floor.isoformat()} to {last_session.isoformat()})")
        meta_cache: dict = {}
        meta_lock = threading.Lock()
        figis_written = 0
        rows_written = 0
        figis_nometa = 0
        figis_delisted = 0
        t0 = time.time()
        for i, r in enumerate(todo, 1):
            figi = r["figi"]
            ticker = r["ticker"] or ""
            if not ticker:
                continue
            meta = _get_meta_moex(
                ticker, last_session,
                meta_cache=meta_cache, meta_lock=meta_lock,
            )
            win_hi = last_session
            if meta is not None:
                board, market = meta["board"], meta["market"]
            else:
                # No primary board: check for a delisted instrument.
                probe = _probe_board_last(ticker)
                if probe is None:
                    figis_nometa += 1
                    continue
                board, lt_iso = probe
                lt = date.fromisoformat(lt_iso)
                if lt >= last_session:
                    # Still listed but probe says no board -> treat as
                    # unknown, do not guess.
                    figis_nometa += 1
                    continue
                if r["max_ts"] and r["max_ts"] > lt_iso:
                    # Local bars exist AFTER the last MOEX board day:
                    # the instrument trades through the broker (foreign
                    # securities with closed MOEX boards — TSLA, BABA).
                    # Its MOEX listed_till is not a delisting for our
                    # pipeline; do not record it.
                    figis_nometa += 1
                    continue
                market = "bonds" if board in _BONDS_BOARDS else "shares"
                win_hi = lt
                figis_delisted += 1
                if not args.dry_run:
                    # Coordination (Task 3): ``instruments.listed_till``
                    # UPDATE acquires the shared writer lock under
                    # ``role="no-trade-evidence" / phase="listed-till"``.
                    # The evidence write below uses a SEPARATE
                    # ``role="no-trade-evidence" / phase="evidence"``
                    # acquisition so the two writes never nest. MOEX
                    # fetch and ``time.sleep`` run AFTER both locks
                    # are released.
                    try:
                        with writer_lock(
                            str(db_path),
                            role="no-trade-evidence",
                            phase="listed-till",
                        ):
                            try:
                                con.execute(
                                    "UPDATE instruments SET listed_till = ? "
                                    "WHERE figi = ?",
                                    (lt_iso, figi),
                                )
                                con.commit()
                            except BaseException as exc:
                                try:
                                    con.rollback()
                                except BaseException:
                                    pass  # Preserve the original failure; still unlock.
                                if is_sqlite_busy(exc):
                                    raise WriterLockBusy(
                                        role="no-trade-evidence", phase="listed-till",
                                        database_path=str(db_path),
                                        lock_path=str(writer_lock_path(db_path)),
                                        timeout_seconds=30.0, reason="sqlite-busy",
                                    ) from exc
                                raise
                    except WriterLockBusy as exc:
                        print(format_busy_defer(exc))
                        return 75
                else:
                    print(f"  [dry] {ticker}: DELISTED {lt_iso} (board {board})")
            lo = max(floor, date.fromisoformat(r["max_ts"]) + timedelta(days=1))
            if lo > win_hi:
                continue
            # Task 2: walk the outcome-emitting fetcher per touched
            # calendar year. The historical evidence helper gates on
            # the per-fetch outcome (``complete`` vs degraded) and
            # threads the explicit ``ticker`` from the ``instruments``
            # row through to the per-row identity filter — it does
            # NOT guess the ticker from the first row. The bar-list
            # consumer contract is preserved (the existing
            # ``_fetch_moex_range`` is replaced by
            # ``_fetch_year_moex_outcome`` per year; the rows it
            # returned are the rows the helper sees, no second fetch
            # per figi). On a non-``complete`` outcome, the helper
            # emits exactly one ``moex_historical_evidence_rejected``
            # line and continues to the next figi. No new exit code;
            # the existing ``return 75`` on ``WriterLockBusy`` is
            # unchanged. Dry-run short-circuits BEFORE the outcome
            # gate so an unrelated test stubbing the fetcher with
            # ``complete`` still walks the existing dry-run print.
            years = list(range(lo.year, win_hi.year + 1))
            all_rows: list[dict] = []
            overall = "complete"
            fetch_failed = False
            for y in years:
                try:
                    year_rows, year_outcome = _fetch_year_moex_outcome(
                        market, board, ticker, y,
                        last_trading_day=win_hi,
                    )
                except Exception as e:  # noqa: BLE001 — keep walking the list
                    print(f"  [{i}/{len(todo)}] {ticker}: fetch failed for year {y}: {e!r}")
                    fetch_failed = True
                    break
                all_rows.extend(year_rows)
                overall = _reduce_outcomes(overall, year_outcome)
            if fetch_failed:
                continue
            zrows = _extract_zero_trade_rows(all_rows)
            if args.dry_run:
                if not zrows:
                    print(f"  [dry] {ticker}: no zero-trade days in window")
                else:
                    print(f"  [dry] {ticker}: {len(zrows)} zero-trade days "
                          f"({zrows[0]['ts']}..{zrows[-1]['ts']})")
                figis_written += 1
                rows_written += len(zrows)
                continue
            if overall != "complete":
                # The helper emits its own structured rejection line;
                # we only need the figi context for the operator log.
                print(
                    f"  [{i}/{len(todo)}] {ticker}: "
                    f"moex_historical_evidence_rejected figi={figi} "
                    f"reason={overall} rows={len(all_rows)}"
                )
                continue
            if not zrows:
                continue
            # Identity guard (R1): the helper compares the upstream
            # ISIN it receives against the figi's stored ISIN. The
            # ``isin`` argument MUST carry the upstream-verified
            # metadata ISIN (not the local one), so the CLI does the
            # same MOEX identity probe the historical walker does
            # before invoking the helper. When the upstream ISIN is
            # empty (probe failed or no primary board) AND the local
            # ISIN is populated, we skip the figi with the same
            # ``identity_mismatch`` log line the helper would emit
            # — fail-closed, no DB write, no fabricated identity.
            local_isin = str(r["isin"] or "").strip()
            from algotrader_api.ingestion.no_trade_evidence import (
                fetch_issuer_identity as _fii,
            )
            ident = _fii(ticker)
            upstream_isin = (
                (ident.get("isin") or "").strip() if ident else ""
            )
            if upstream_isin != local_isin:
                logging.getLogger("algotrader.ingestion").info(
                    "moex_historical_evidence_rejected figi=%s "
                    "reason=identity_mismatch rows=%s",
                    figi, len(all_rows),
                )
                print(
                    f"  [{i}/{len(todo)}] {ticker}: "
                    f"moex_historical_evidence_rejected figi={figi} "
                    f"reason=identity_mismatch rows={len(all_rows)}"
                )
                continue
            # Coordination (Task 3): evidence write goes through
            # ``record_historical_no_trade_evidence`` which acquires
            # the shared lock with
            # ``role="no-trade-evidence" / phase="evidence"``. The
            # ``listed_till`` lock (if any) was released above so the
            # two never nest. MOEX fetch and ``time.sleep`` run
            # OUTSIDE the lock. The ``WriterLockBusy`` propagation
            # is unchanged — the existing ``return 75`` path still
            # applies; the helper does not invent a new exit code.
            try:
                n = record_historical_no_trade_evidence(
                    con, db_path=str(db_path), figi=figi, ticker=ticker,
                    rows=all_rows, board=board,
                    isin=upstream_isin,
                    outcome=overall,
                    from_d=lo, to_d=win_hi, today=date.today(),
                )
            except WriterLockBusy as exc:
                print(format_busy_defer(exc))
                return 75
            if n:
                figis_written += 1
                rows_written += n
                print(f"  [{i}/{len(todo)}] {ticker}: +{n} evidence rows")
            if args.sleep:
                time.sleep(args.sleep)
        dt = time.time() - t0
        print(f"done: figis_with_evidence={figis_written} rows={rows_written} "
              f"delisted={figis_delisted} no_meta={figis_nometa} "
              f"elapsed_s={dt:.1f}")
        # Canonical: the CLI exits 0 regardless of how many figis
        # were degraded. The diagnostic
        # ``moex_historical_evidence_rejected`` line above carries
        # the operator signal; cron / supervisor policy reads the
        # log, not the exit code. The only non-zero exit is the
        # existing ``return 75`` on ``WriterLockBusy`` — no new
        # exit code is introduced for partial outcomes.
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
