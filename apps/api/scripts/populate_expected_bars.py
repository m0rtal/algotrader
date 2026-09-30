"""One-shot: populate instruments.expected_bars for every tradable figi.

Reads listing_date from instruments.source_updated_at, computes
expected_business_days(listing_date, yesterday) via the coverage helper,
UPDATEs the column. Idempotent.

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

    con = sqlite3.connect(str(db_path), timeout=30)
    # sqlite3.Row lets us address evidence / listing fields by name in
    # the loops below; without this the con.execute(...).fetchall()
    # rows are plain tuples and `row["session_date"]` raises TypeError.
    con.row_factory = sqlite3.Row
    try:
        # Check migration 023 applied
        cols = [r[1] for r in con.execute("PRAGMA table_info(instruments)").fetchall()]
        if "expected_bars" not in cols:
            print("ERROR: instruments.expected_bars column missing. "
                  "Apply migration 023 first.", file=sys.stderr)
            return 3

        # Listing date = MIN(bars.ts) for figis with bars (most reliable
        # source); falls back to instruments.source_updated_at for figis
        # without bars. ADAPT-10 (live smoke 2026-09-25): the original
        # implementation used source_updated_at only, which is the LAST
        # UPDATE timestamp (when Tinkoff ISS last touched the row), not
        # the listing date. For most figis source_updated_at > yesterday,
        # so expected_bars was 0 for the entire population.
        rows = con.execute(
            """SELECT i.figi,
                      COALESCE(MIN(b.ts), i.source_updated_at) AS listing_ts
               FROM instruments i
               LEFT JOIN bars b ON b.figi = i.figi
               WHERE i.class IN ('share', 'etf', 'bond')
               GROUP BY i.figi"""
        ).fetchall()
        yesterday = date.today() - timedelta(days=1)
        # Confirmed, unexpired no-trade evidence for each figi reduces
        # the expected denominator by the count of past no-trade days
        # in [listing_date, yesterday]. Migration 025 may not have run
        # on older databases — guard with a try/except so this script
        # stays backward-compatible.
        try:
            evidence_rows = con.execute(
                """SELECT figi, session_date FROM moex_no_trade_evidence
                   WHERE expires_at >= date('now')"""
            ).fetchall()
        except sqlite3.OperationalError:
            evidence_rows = []
        evidence_by_figi: dict[str, set[str]] = {}
        for r in evidence_rows:
            evidence_by_figi.setdefault(r["figi"], set()).add(r["session_date"])
        updated = 0
        for figi, listing in rows:
            listing_date = date.fromisoformat(listing[:10])  # "2024-01-15T10:00:00"
            expected = expected_business_days(con, listing_date, yesterday)
            # Subtract past confirmed no-trade days in the [listing_date,
            # yesterday] window. Future-dated evidence is irrelevant here
            # because yesterday is the upper bound.
            evidence_dates = evidence_by_figi.get(figi, set())
            for ed in evidence_dates:
                try:
                    if listing_date <= date.fromisoformat(ed) <= yesterday:
                        expected -= 1
                except ValueError:
                    continue
            if expected < 0:
                expected = 0
            con.execute(
                "UPDATE instruments SET expected_bars = ? WHERE figi = ?",
                (expected, figi),
            )
            updated += 1
        con.commit()
        print(f"OK: populated expected_bars for {updated} figis "
              f"(end_date={yesterday.isoformat()})")
        return 0
    except Exception as e:
        con.rollback()
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
