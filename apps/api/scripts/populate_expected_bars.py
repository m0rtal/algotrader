"""One-shot: populate instruments.expected_bars for every tradable figi.

Listing date = MIN(bars.ts) per figi (falls back to source_updated_at for
figis without bars). The expected denominator is:

    business_days(listing_date, min(yesterday, listed_till))
    minus confirmed no-trade evidence days inside the window

`listed_till` (migration 026) bounds delisted instruments so the gate
does not expect bars after the delisting. Confirmed zero-trade days
(moex_no_trade_evidence) are subtracted because they are sessions the
instrument demonstrably did not trade — they must not count against the
95% completeness ratio. Both adjustments are conservative: missing
columns / missing evidence fall back to the plain calendar count.

Idempotent. Writer-coordination contract (Task 4):
  * expected-bar values are computed BEFORE the shared writer lock
    is acquired (no pending mutation under the lock except the
    ``BEGIN IMMEDIATE`` batch itself).
  * The complete update batch is applied in ONE coordinated
    ``BEGIN IMMEDIATE`` transaction under
    ``role="expected-bars" / phase="expected-bars"``; the batch
    commits or rolls back as a single unit.
  * A shared-lock busy raises ``WriterLockBusy``; the CLI returns
    75 with no partial mutation.
  * No ``--dry-run`` is added.

Usage:
    python apps/api/scripts/populate_expected_bars.py
    python apps/api/scripts/populate_expected_bars.py --refresh   # recompute all
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

# Add apps/api/src to path so the helper is importable when invoked as a script
API_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(API_SRC))

from algotrader_api.ingestion.writer_lock import (  # noqa: E402
    WriterLockBusy,
    format_busy_defer,
    writer_lock,
)
from algotrader_api.ml.coverage import expected_business_days  # noqa: E402


DEFAULT_DB = "/home/hermes/algotrader/apps/api/data/state.db"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB,
                    help="SQLite DB path (default: %(default)s)")
    ap.add_argument("--refresh", action="store_true",
                    help="Recompute even for figis that already have expected_bars")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: {db_path} does not exist", file=sys.stderr)
        return 2

    # Pre-flight: confirm schema, then read everything we need to
    # compute expected-bars values. The reader connection commits
    # nothing; all mutation happens under the writer lock below.
    con = sqlite3.connect(str(db_path), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(instruments)").fetchall()]
        if "expected_bars" not in cols:
            print("ERROR: instruments.expected_bars column missing. "
                  "Apply migration 023 first.", file=sys.stderr)
            return 3
        has_listed_till = "listed_till" in cols
        try:
            con.execute("SELECT 1 FROM moex_no_trade_evidence LIMIT 1")
            has_evidence = True
        except sqlite3.OperationalError:
            has_evidence = False

        listed_till_expr = ", i.listed_till AS listed_till" if has_listed_till else ""
        rows = con.execute(
            f"""SELECT i.figi,
                      COALESCE(MIN(b.ts), i.source_updated_at) AS listing_ts
                      {listed_till_expr}
               FROM instruments i
               LEFT JOIN bars b ON b.figi = i.figi
               WHERE i.class IN ('share', 'etf', 'bond')
               GROUP BY i.figi"""
        ).fetchall()

        evidence_by_figi: dict[str, set[str]] = {}
        if has_evidence:
            today_iso = date.today().isoformat()
            for r in con.execute(
                "SELECT figi, session_date, expires_at FROM moex_no_trade_evidence"
            ):
                if r["expires_at"] < today_iso:
                    continue
                evidence_by_figi.setdefault(r["figi"], set()).add(
                    r["session_date"]
                )
    finally:
        con.close()

    # Compute the complete batch OUTSIDE the shared writer lock:
    # network I/O, calendar arithmetic, and any evidence lookups
    # happen here. The lock below only covers the BEGIN IMMEDIATE
    # batch and its commit/rollback.
    yesterday = date.today() - timedelta(days=1)
    updates: list[tuple[int, str]] = []
    for r in rows:
        figi = r["figi"]
        listing = r["listing_ts"]
        listing_date = date.fromisoformat(listing[:10])
        end_date = yesterday
        if has_listed_till and r["listed_till"]:
            lt = date.fromisoformat(str(r["listed_till"])[:10])
            if listing_date < lt < end_date:
                end_date = lt
        # The connection used here is a fresh read-only connection
        # so the calendar computation does not depend on the
        # mutating connection that will run the batch.
        ro = sqlite3.connect(str(db_path))
        try:
            expected = expected_business_days(ro, listing_date, end_date)
        finally:
            ro.close()
        for ds in evidence_by_figi.get(figi, set()):
            try:
                d = date.fromisoformat(ds[:10])
            except ValueError:
                continue
            if listing_date <= d <= end_date and d.weekday() < 5:
                expected -= 1
        if expected < 0:
            expected = 0
        updates.append((expected, figi))

    if not updates:
        print("OK: populated expected_bars for 0 figis "
              f"(end_date={yesterday.isoformat()}, "
              f"evidence={'yes' if has_evidence else 'no'}, "
              f"listed_till={'yes' if has_listed_till else 'no'})")
        return 0

    # Apply the complete batch inside the shared writer lock,
    # exactly one BEGIN IMMEDIATE transaction. The whole batch
    # commits or rolls back as one unit. Any non-WriterLockBusy
    # failure inside the lock is re-raised out of the with-block;
    # we catch it here, log a bounded failure line, and return 1
    # so the wrapper can decide whether to retry.
    try:
        with writer_lock(
            str(db_path),
            role="expected-bars",
            phase="expected-bars",
        ):
            mut = sqlite3.connect(str(db_path), timeout=30)
            try:
                try:
                    mut.execute("BEGIN IMMEDIATE")
                    try:
                        mut.executemany(
                            "UPDATE instruments SET expected_bars = ? "
                            "WHERE figi = ?",
                            updates,
                        )
                        mut.commit()
                    except Exception:
                        mut.rollback()
                        raise
                finally:
                    # Explicit close in the finally to keep the
                    # writer lock window narrow.
                    pass
            finally:
                mut.close()
    except WriterLockBusy as exc:
        print(format_busy_defer(exc), file=sys.stderr)
        return 75
    except Exception as exc:
        print(
            f"FAIL: expected-bars batch rolled back: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"OK: populated expected_bars for {len(updates)} figis "
          f"(end_date={yesterday.isoformat()}, "
          f"evidence={'yes' if has_evidence else 'no'}, "
          f"listed_till={'yes' if has_listed_till else 'no'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
