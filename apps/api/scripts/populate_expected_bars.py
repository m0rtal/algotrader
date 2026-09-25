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
    try:
        # Check migration 023 applied
        cols = [r[1] for r in con.execute("PRAGMA table_info(instruments)").fetchall()]
        if "expected_bars" not in cols:
            print("ERROR: instruments.expected_bars column missing. "
                  "Apply migration 023 first.", file=sys.stderr)
            return 3

        rows = con.execute(
            "SELECT figi, source_updated_at FROM instruments "
            "WHERE source_updated_at IS NOT NULL"
        ).fetchall()
        yesterday = date.today() - timedelta(days=1)
        updated = 0
        for figi, listing in rows:
            listing_date = date.fromisoformat(listing[:10])  # "2024-01-15T10:00:00"
            expected = expected_business_days(con, listing_date, yesterday)
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
