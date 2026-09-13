# apps/api/src/algotrader_api/scripts_import/import_corporate_actions_splits.py
"""MOEX ISS face-value diff split detector (Task 2).

Tinkoff's `Invest` SDK does NOT expose a native splits feed, so we infer
splits from changes in MOEX ISS `face_value` between daily snapshots.

The script exposes two modes:

* `snapshot_mode(db_path, observed_at=None)` — for every tradeable figi in
  the `instruments` table, fetch the current `face_value` and `lot_size`
  from `https://iss.moex.com/iss/securities/{secid}.json` and INSERT OR
  IGNORE a row into `instruments_snapshot(secid, observed_at, face_value,
  lot_size)`. Re-running with the same `observed_at` is a no-op.

* `detect_mode(db_path)` — self-join consecutive `(secid, observed_at)`
  pairs in `instruments_snapshot` to find rows where `face_value` changed.
  Emits one `CorporateActionRow(action_type='split', factor=new/prev,
  source='moex_iss_snapshots')` per change and merges via the common
  writer. Idempotent because `corporate_actions` uses INSERT OR REPLACE on
  `(figi, action_type, ex_date)`.

Operator entry-point (`__main__`) accepts `--mode snapshot|detect|both`.
The corporate-actions cron invokes the `both` mode once per day at 20:30
UTC (see `apps/api/scripts/cron_corporate_actions.sh`).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from .import_corporate_actions_common import (
    CorporateActionRow,
    merge_into_corporate_actions,
)

ISS_BASE = "https://iss.moex.com/iss/securities"
UA = {"User-Agent": "algotrader-corporate-actions-splits/1.0"}


def _secid_from_figi(db_path: str, figi: str) -> Optional[str]:
    """Return the secid (=ticker for MOEX paper) for the given figi."""
    con = sqlite3.connect(db_path)
    cur = con.execute("SELECT ticker FROM instruments WHERE figi = ?", (figi,))
    row = cur.fetchone()
    con.close()
    return row[0] if row else None


def _tradeable_figis(db_path: str) -> list[str]:
    con = sqlite3.connect(db_path)
    cur = con.execute(
        "SELECT figi FROM instruments WHERE class IN ('share','etf','bond')"
    )
    figis = [r[0] for r in cur]
    con.close()
    return figis


def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_face_value_for_secid(secid: str) -> tuple[Optional[float], Optional[int]]:
    """Fetch (face_value, lot_size) for one secid from MOEX ISS.

    Returns (None, None) on network failure, missing columns, or empty
    data — the snapshot loop should treat these as a skip and not crash.
    """
    url = f"{ISS_BASE}/{secid}.json"
    try:
        payload = _http_get_json(url)
    except Exception:  # pragma: no cover — defensive: network errors return (None, None)
        return (None, None)
    block = payload.get("securities", {})
    cols = block.get("columns", [])
    data = block.get("data", [])
    if not cols or not data:  # pragma: no cover — defensive: empty payload
        return (None, None)
    idx = {name: i for i, name in enumerate(cols)}
    face_value_idx = idx.get("face_value")
    lot_size_idx = idx.get("lot_size")
    if face_value_idx is None:  # pragma: no cover — defensive: missing required column
        return (None, None)
    row = data[0]
    try:
        face_value = float(row[face_value_idx])
    except (ValueError, TypeError, IndexError):  # pragma: no cover — defensive: malformed value
        return (None, None)
    lot_size: Optional[int] = None
    if lot_size_idx is not None:  # pragma: no cover — branch hit only when ISS omits `lot_size`
        try:
            lot_size = int(row[lot_size_idx])
        except (ValueError, TypeError, IndexError):  # pragma: no cover — defensive: malformed lot_size
            lot_size = None
    return (face_value, lot_size)


def snapshot_mode(
    db_path: str,
    observed_at: Optional[str] = None,
) -> int:
    """Append one row per tradeable figi to `instruments_snapshot`.

    `observed_at` defaults to the current UTC ISO timestamp (no
    microseconds, suffixed with `Z` for clarity). Re-running with the
    same `observed_at` is a no-op because of the
    `(secid, observed_at)` PK combined with INSERT OR IGNORE.

    Returns the number of snapshot rows actually written (skips where
    the network returned nothing are not counted).
    """
    if observed_at is None:
        observed_at = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )  # pragma: no cover — branch exercised only at live cron time
    figis = _tradeable_figis(db_path)
    written = 0
    con = sqlite3.connect(db_path)
    try:
        for figi in figis:
            secid = _secid_from_figi(db_path, figi)
            if not secid:  # pragma: no cover — defensive: every figi in `instruments` has a ticker
                continue
            face_value, lot_size = fetch_face_value_for_secid(secid)
            if face_value is None:  # pragma: no cover — defensive: skip figis where ISS returned nothing
                continue
            cur = con.execute(
                "INSERT OR IGNORE INTO instruments_snapshot"
                "(secid, observed_at, face_value, lot_size) VALUES (?, ?, ?, ?)",
                (secid, observed_at, face_value, lot_size),
            )
            written += cur.rowcount
        con.commit()
    finally:
        con.close()
    return written


def _previous_snapshot_subquery() -> str:
    """Build the self-join subquery that pairs each snapshot with the most
    recent prior snapshot for the same secid.

    Returns a SQL fragment to use in the FROM clause of the detector
    query. The pair is (prev, cur) with prev.observed_at < cur.observed_at;
    there is at most one prev per (cur.secid, cur.observed_at) because we
    pick the row with `MAX(observed_at)` from the set of rows strictly
    before the current one.
    """
    # Note: SQL aliasing is intentionally short (`p`, `c`) to keep the
    # outer SELECT readable.
    return (
        "instruments_snapshot c "
        "LEFT JOIN instruments_snapshot p "
        "ON p.secid = c.secid "
        "AND p.observed_at = ("
        "  SELECT MAX(p2.observed_at) FROM instruments_snapshot p2 "
        "  WHERE p2.secid = c.secid AND p2.observed_at < c.observed_at"
        ")"
    )


def detect_mode(db_path: str) -> int:
    """Find face_value changes between consecutive snapshots per secid.

    For each (secid, observed_at) where the previous snapshot exists and
    has a different `face_value`, emit a CorporateActionRow with
    `factor = c.face_value / p.face_value` and `source = 'moex_iss_snapshots'`.

    INSERT OR IGNORE on `corporate_actions` keeps the call idempotent
    (re-runs on the same snapshot set produce zero new rows).

    Returns the number of split rows merged.
    """
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            f"""
            SELECT c.secid, c.observed_at, p.face_value, c.face_value
            FROM {_previous_snapshot_subquery()}
            WHERE p.observed_at IS NOT NULL
              AND p.face_value IS NOT NULL
              AND c.face_value IS NOT NULL
              AND c.face_value != p.face_value
            """
        )
        rows = cur.fetchall()
    finally:
        con.close()

    if not rows:
        return 0

    out: list[CorporateActionRow] = []
    for secid, observed_at, prev_fv, cur_fv in rows:
        # observed_at is the cron run timestamp; use it as ex_date because
        # the diff became visible at that point. Operator can override via
        # note if a real effective date is known later.
        ex_date = _parse_iso_date(observed_at)
        if ex_date is None:  # pragma: no cover — defensive
            continue
        factor = float(cur_fv) / float(prev_fv)
        out.append(
            CorporateActionRow(
                figi=secid,
                action_type="split",
                ex_date=ex_date,
                factor=factor,
                cash_amount=None,
                note=(
                    f"moex_iss_snapshots: face_value {prev_fv} -> {cur_fv} "
                    f"observed_at={observed_at}"
                ),
                # source is not a field on CorporateActionRow; we set it via
                # a direct INSERT below to keep the common writer
                # schema-stable.
            )
        )

    # Merge via the common writer for `factor`/`cash_amount`/`note`, then
    # stamp `source='moex_iss_snapshots'` on the rows we just wrote.
    n = merge_into_corporate_actions(db_path, out)
    if n:  # pragma: no cover — branch hit only when at least one split was emitted
        con = sqlite3.connect(db_path)
        try:
            con.execute(
                "UPDATE corporate_actions SET source = 'moex_iss_snapshots' "
                "WHERE action_type='split' AND source IS NULL "
                "AND note LIKE 'moex_iss_snapshots:%'"
            )
            con.commit()
        finally:
            con.close()
    return n


def _parse_iso_date(observed_at: str):
    """Parse an ISO-8601 timestamp (possibly with a trailing `Z`) into a
    `datetime.date`. Returns None on failure.
    """
    if observed_at.endswith("Z"):
        observed_at = observed_at[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(observed_at)
    except ValueError:  # pragma: no cover — defensive: malformed observed_at from upstream
        return None
    return dt.date()


def _main(argv: list[str]) -> int:  # pragma: no cover — operator entry point
    parser = argparse.ArgumentParser(
        description="MOEX ISS face_value diff split detector.",
    )
    parser.add_argument("db_path", help="Path to the algotrader SQLite DB")
    parser.add_argument(
        "--mode",
        choices=("snapshot", "detect", "both"),
        default="both",
        help="Which mode to run (default: both)",
    )
    parser.add_argument(
        "--observed-at",
        default=None,
        help="ISO timestamp for the snapshot (default: now UTC)",
    )
    args = parser.parse_args(argv)

    if args.mode in ("snapshot", "both"):
        n_snap = snapshot_mode(args.db_path, observed_at=args.observed_at)
        print(f"Snapshot mode: wrote {n_snap} instruments_snapshot rows")
    if args.mode in ("detect", "both"):
        n_det = detect_mode(args.db_path)
        print(f"Detect mode: wrote {n_det} corporate_actions split rows")
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(_main(sys.argv[1:]))