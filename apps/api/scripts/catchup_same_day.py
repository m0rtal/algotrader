#!/usr/bin/env python
"""Same-day MOEX catch-up: fetch TODAY's session after market close.

The daily worker's windows always end at `today - 1` ("bars publish at
end of day"), so the current session's bars arrive only on the NEXT
day's cycle — up to ~20 h of lag. This script fetches the CURRENT day's
published bars after the main session closes (guard: >= 16:15 UTC) and
inserts them, bringing same-day freshness to ~1-2 h.

The 16:15 UTC time guard is an EARLIEST attempt, not a proof that
publication has finished. If MOEX ISS has not yet published today's
bars, the per-ticker ``_fetch_moex_range`` call returns an empty list
and the script moves on; the parent retry schedule owns the next
attempt.

Scope: MOEX-board instruments (Russian shares/bonds/ETFs), i.e. figis
with a primary board. Broker-only foreign instruments keep their own
schedule (Tinkoff same-day candles can still be forming in the
evening; they are handled by backfill_foreign_bars.py).

Identity safety: a ticker shared by two figis (e.g. RU primary +
US-listed mirror — sanctioned relistings) must NOT cause the US figi
to receive the Russian security's bars. Every candidate is filtered
by ``i.isin`` at the SQL layer; ``fetch_issuer_identity`` enriches
each ticker with the ISIN MOEX considers authoritative; and each
returned row's raw ``_secid``/``_boardid`` is checked against the
requested ticker/board before any write.

A disk cache of MOEX board metadata (--cache path, default
data/moex_board_cache.json) keeps repeat runs fast: the first run probes
~3800 tickers (~20 min), later runs only probe unknown tickers.
A legacy cache entry lacking the verified ``isin`` key is NOT trusted —
the script re-probes ``fetch_issuer_identity`` and refuses to write
until the probe succeeds.

Usage:
    python apps/api/scripts/catchup_same_day.py --dry-run --limit 20
    python apps/api/scripts/catchup_same_day.py            # evening cron
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

API_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(API_SRC))

from algotrader_api.db.bars_sqlite import replace_bars_for_figi  # noqa: E402
from algotrader_api.ingestion.backfill import (  # noqa: E402
    _fetch_moex_range,
    _get_meta_moex,
    _last_trading_day,
)
from algotrader_api.ingestion.no_trade_evidence import fetch_issuer_identity  # noqa: E402

DEFAULT_DB = "/home/hermes/algotrader/apps/api/data/state.db"
DEFAULT_CACHE = "/home/hermes/algotrader/apps/api/data/moex_board_cache.json"
# Main session closes 18:39 MSK = 15:39 UTC; ISS publishes day bars
# shortly after. 16:15 UTC = 19:15 MSK is a safe earliest run time.
EARLIEST_UTC_HOUR = 16
EARLIEST_UTC_MINUTE = 15


def _load_cache(path: Path) -> dict:
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_cache(path: Path, cache: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as fh:
            json.dump(cache, fh, separators=(",", ":"))
        tmp.replace(path)
    except OSError:
        pass


def main() -> int:
    import threading

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--date", default=None,
                    help="session date ISO (default: today)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.02)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="skip the after-close time guard (for tests/backfill)")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: {db_path} does not exist", file=sys.stderr)
        return 2
    session = date.fromisoformat(args.date) if args.date else date.today()

    now_utc = datetime.now(timezone.utc)
    if (not args.force and session == date.today()
            and (now_utc.hour, now_utc.minute) < (EARLIEST_UTC_HOUR, EARLIEST_UTC_MINUTE)):
        print(f"too early: {now_utc.isoformat()} < "
              f"{EARLIEST_UTC_HOUR:02d}:{EARLIEST_UTC_MINUTE:02d} UTC "
              f"(session bars not published yet); use --force to override")
        return 0

    con = sqlite3.connect(str(db_path), timeout=60)
    con.row_factory = sqlite3.Row
    try:
        last_session = _last_trading_day(date.today(), str(db_path))
        if session <= last_session:
            print(f"session {session} is not newer than last completed "
                  f"session {last_session}; nothing to do")
            return 0
        # Active instruments: any bar within the last 10 calendar days.
        # We do NOT filter on a populated ISIN at the SQL layer — instruments
        # with a NULL/empty ISIN are still listed here so we can account for
        # them as ``skipped_identity`` rather than silently dropping them.
        # Without that count, a degenerate ticker/ISIN column would go
        # unnoticed and the script would look like it processed every
        # candidate while quietly ignoring the unsafe ones.
        cutoff = (last_session - timedelta(days=10)).isoformat()
        rows = con.execute(
            """SELECT i.figi, i.ticker, i.isin
               FROM instruments i
               WHERE i.class IN ('share', 'etf', 'bond')
                 AND EXISTS (SELECT 1 FROM bars b
                             WHERE b.figi = i.figi AND b.ts >= ?)""",
            (cutoff,),
        ).fetchall()
        if args.limit:
            rows = rows[: args.limit]
        print(f"active instruments: {len(rows)} (session {session})")

        cache_path = Path(args.cache)
        cache = _load_cache(cache_path)
        meta_lock = threading.Lock()
        fetched = written = skipped = skipped_identity = errors = probed = 0
        for i, r in enumerate(rows, 1):
            figi, ticker = r["figi"], r["ticker"] or ""
            if not ticker:
                continue
            inst_isin = (r["isin"] or "").strip()
            # A NULL/empty instrument ISIN makes identity verification
            # impossible — without it we cannot tell the RU figi from a
            # foreign mirror sharing the same ticker. Count it explicitly
            # so the operator can see the row was considered and refused.
            if not inst_isin:
                skipped_identity += 1
                continue
            meta = cache.get(ticker)
            if meta is None and ticker not in cache:
                # Unknown ticker: probe (and remember even a miss).
                meta = _get_meta_moex(ticker, last_session,
                                      meta_cache={}, meta_lock=meta_lock)
                cache[ticker] = meta  # dict or None
                probed += 1
                if probed % 100 == 0:
                    _save_cache(cache_path, cache)
            if meta is None:
                skipped += 1
                continue
            # Identity verification. _get_meta_moex returns board but no
            # ISIN — we need the ISIN MOEX considers authoritative to
            # cross-check the instrument record. A cached meta lacking
            # ``isin`` is a legacy cache (older versions of this script);
            # re-probe ``fetch_issuer_identity`` and refuse to trust the
            # cache entry until the probe succeeds. A failed probe stays
            # out of the cache so the next run retries.
            if isinstance(meta, dict) and not (meta.get("isin") or "").strip():
                ident = fetch_issuer_identity(ticker)
                if ident and (ident.get("isin") or "").strip() \
                        and (ident.get("board") or "") == (meta.get("board") or ""):
                    meta["isin"] = (ident.get("isin") or "").strip()
                    cache[ticker] = meta
                else:
                    # Failed probe OR board mismatch — drop the cache
                    # entry so the next run retries. A board mismatch (the
                    # upstream reports a different board than the cached
                    # one) means the ticker/board pairing MOEX considers
                    # authoritative no longer matches what we probed;
                    # trust nothing until the picture reconciles.
                    cache.pop(ticker, None)
                    skipped_identity += 1
                    continue
            # ISIN must match exactly between the instrument record and
            # what MOEX reports for the ticker. A mismatch means this
            # figi is the wrong one for this ticker (e.g. a US-listed
            # mirror sharing the Russian security's ticker) — skipping
            # is the only safe action.
            if (meta.get("isin") or "") != inst_isin:
                skipped_identity += 1
                continue
            try:
                bars = _fetch_moex_range(
                    meta["market"], meta["board"], ticker,
                    session, session, last_trading_day=session,
                )
            except Exception as e:  # noqa: BLE001 — keep walking
                errors += 1
                print(f"  [{i}/{len(rows)}] {ticker}: fetch failed: {e!s:.90}")
                continue
            # Verify each row's raw _secid / _boardid actually match the
            # ticker / board we asked for. MOEX can serve rows from a
            # different board (cross-listed mirror leakage); those are
            # the exact cross-contamination we are guarding against.
            verified = []
            for b in bars:
                if str(b.get("_secid") or "") != ticker:
                    continue
                if str(b.get("_boardid") or "") != str(meta.get("board") or ""):
                    continue
                verified.append(b)
            day_rows = [b for b in verified if str(b.get("ts"))[:10] == session.isoformat()]
            if not day_rows:
                continue
            for b in day_rows:
                b["figi"] = figi
            fetched += 1
            if args.dry_run:
                print(f"  [dry] {ticker}: {len(day_rows)} same-day bars")
                written += len(day_rows)
                continue
            last_err = None
            for attempt in range(6):
                try:
                    replace_bars_for_figi(str(db_path), figi, day_rows,
                                          replace=False, source="moex")
                    last_err = None
                    break
                except sqlite3.OperationalError as e:
                    last_err = e
                    time.sleep(5 * (attempt + 1))
            if last_err is not None:
                errors += 1
                print(f"  [{i}/{len(rows)}] {ticker}: db write failed: "
                      f"{last_err!s:.80}")
                continue
            written += len(day_rows)
            if args.sleep:
                time.sleep(args.sleep)
        _save_cache(cache_path, cache)
        print(f"done: figis_with_bars={fetched} rows={written} "
              f"skipped_no_board={skipped} skipped_identity={skipped_identity} "
              f"errors={errors} probed={probed} cache={len(cache)}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
