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
    _get_meta_moex,
    _last_trading_day,
)
from algotrader_api.ingestion.no_trade_evidence import (  # noqa: E402
    _extract_zero_trade_rows,
    _moex_session,
    record_no_trade_evidence,
)
from algotrader_api.ingestion.writer_lock import (  # noqa: E402
    WriterLockBusy,
    writer_lock,
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


def _format_defer(exc: "WriterLockBusy") -> str:
    """Render a single bounded ``DEFER writer-lock-busy ...`` line.

    The line carries only the safe metadata exposed by
    :class:`WriterLockBusy` (role, phase, timeout, reason, result).
    It must not include the database path, figi, ticker, or any
    secret. Used by the CLI when either the ``listed-till`` lock or
    the ``evidence`` lock acquisition times out.
    """
    return (
        f"DEFER writer-lock-busy role={exc.role} phase={exc.phase} "
        f"reason={exc.reason} timeout={exc.timeout_seconds:g}s "
        f"result={exc.result}"
    )


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
                            con.execute(
                                "UPDATE instruments SET listed_till = ? "
                                "WHERE figi = ?",
                                (lt_iso, figi),
                            )
                            con.commit()
                    except WriterLockBusy as exc:
                        print(_format_defer(exc))
                        return 75
                else:
                    print(f"  [dry] {ticker}: DELISTED {lt_iso} (board {board})")
            lo = max(floor, date.fromisoformat(r["max_ts"]) + timedelta(days=1))
            if lo > win_hi:
                continue
            try:
                bars = _fetch_moex_range(
                    market, board, ticker,
                    lo, win_hi, last_trading_day=win_hi,
                )
            except Exception as e:  # noqa: BLE001 — keep walking the list
                print(f"  [{i}/{len(todo)}] {ticker}: fetch failed: {e!r}")
                continue
            zrows = _extract_zero_trade_rows(bars)
            if not zrows:
                continue
            if args.dry_run:
                print(f"  [dry] {ticker}: {len(zrows)} zero-trade days "
                      f"({zrows[0]['ts']}..{zrows[-1]['ts']})")
                figis_written += 1
                rows_written += len(zrows)
                continue
            # Coordination (Task 3): evidence write uses the public
            # ``record_no_trade_evidence`` wrapper, which acquires the
            # shared lock with
            # ``role="no-trade-evidence" / phase="evidence"``. The
            # ``listed_till`` lock (if any) was released above so the
            # two never nest. MOEX fetch and ``time.sleep`` run OUTSIDE
            # the lock.
            try:
                n = record_no_trade_evidence(
                    con, db_path=str(db_path), figi=figi, rows=zrows,
                    board=board, isin=str(r["isin"] or ""),
                )
            except WriterLockBusy as exc:
                print(_format_defer(exc))
                return 75
            if n:
                figis_written += 1
                rows_written += n
                print(f"  [{i}/{len(todo)}] {ticker}: +{n} evidence rows")
            if args.sleep:
                time.sleep(args.sleep)
        dt = time.time() - t0
        print(f"done: figis_with_evidence={figis_written} rows={rows_written} "
              f"delisted={figis_delisted} no_meta={figis_nometa} elapsed_s={dt:.1f}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
