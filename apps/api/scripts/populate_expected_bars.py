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

Idempotent.

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
    # Row factory: the loops below address columns by name.
    con.row_factory = sqlite3.Row
    try:
        # Check migration 023 applied
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

        # Listing date = MIN(bars.ts) for figis with bars (most reliable
        # source); falls back to instruments.source_updated_at for figis
        # without bars. ADAPT-10 (live smoke 2026-09-25): the original
        # implementation used source_updated_at only, which is the LAST
        # UPDATE timestamp (when Tinkoff ISS last touched the row), not
        # the listing date. For most figis source_updated_at > yesterday,
        # so expected_bars was 0 for the entire population.
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

        # Confirmed, unexpired no-trade evidence per figi, so the window
        # subtraction below is O(1) per figi.
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

        yesterday = date.today() - timedelta(days=1)
        updated = 0
        for r in rows:
            figi = r["figi"]
            listing = r["listing_ts"]
            listing_date = date.fromisoformat(listing[:10])  # "2024-01-15T10:00:00"
            # Delisted instruments stop expecting sessions at listed_till.
            end_date = yesterday
            if has_listed_till and r["listed_till"]:
                lt = date.fromisoformat(str(r["listed_till"])[:10])
                if lt < end_date:
                    end_date = lt
            expected = expected_business_days(con, listing_date, end_date)
            # Subtract confirmed no-trade sessions inside the window.
            for ds in evidence_by_figi.get(figi, set()):
                try:
                    d = date.fromisoformat(ds[:10])
                except ValueError:
                    continue
                if listing_date <= d <= end_date and d.weekday() < 5:
                    expected -= 1
            if expected < 0:
                expected = 0
            con.execute(
                "UPDATE instruments SET expected_bars = ? WHERE figi = ?",
                (expected, figi),
            )
            updated += 1
        con.commit()
        print(f"OK: populated expected_bars for {updated} figis "
              f"(end_date={yesterday.isoformat()}, "
              f"evidence={'yes' if has_evidence else 'no'}, "
              f"listed_till={'yes' if has_listed_till else 'no'})")
        return 0
    except Exception as e:
        con.rollback()
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
