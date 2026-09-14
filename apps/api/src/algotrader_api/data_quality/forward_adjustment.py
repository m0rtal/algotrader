"""Forward-adjustment of bars when a split lands.

Forward convention: post-split bars are divided by the factor.
Idempotent: re-running on already-adjusted bars is a no-op.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone


def _already_applied(conn: sqlite3.Connection, figi: str, factor: float) -> bool:
    """True iff the most-recent bars_adjusted row for figi is already
    equal to bars.close / factor within 1e-6 tolerance."""
    row = conn.execute(
        """
        SELECT ba.adj_close, b.close
        FROM bars_adjusted ba
        JOIN bars b ON b.figi = ba.figi AND b.ts = ba.ts
        WHERE ba.figi = ?
        ORDER BY ba.ts DESC
        LIMIT 1
        """,
        (figi,),
    ).fetchone()
    if row is None:
        return False
    adj_close, raw_close = row[0], row[1]
    return abs(adj_close - raw_close / factor) < 1e-6


def apply_forward_split(
    conn: sqlite3.Connection,
    figi: str,
    ex_date: date,
    factor: float,
) -> int:
    """Update bars_adjusted.adj_* for figi where ts >= ex_date.

    For each row:
      - adj_open  = adj_open / factor
      - adj_high  = adj_high / factor
      - adj_low   = adj_low / factor
      - adj_close = adj_close / factor
      - adj_volume = adj_volume (unchanged — splits don't multiply volume)

    Returns the number of rows updated.

    Idempotency guard: if the most-recent adj_close for this figi
    is already equal to close/factor within 1e-6 tolerance, the
    function returns 0 (no-op).
    """
    if factor == 1.0:
        return 0

    if _already_applied(conn, figi, factor):
        return 0

    ex_date_iso = ex_date.isoformat()
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Copy raw bars into bars_adjusted for any ts >= ex_date that
    # doesn't yet have an adjusted row. Without this, fresh bars
    # (post-split, never adjusted) would be invisible.
    conn.execute(
        """
        INSERT INTO bars_adjusted (
            figi, ts, adj_open, adj_high, adj_low, adj_close, adj_volume, source, computed_at
        )
        SELECT b.figi, b.ts, b.open, b.high, b.low, b.close,
               COALESCE(b.volume, 0),
               'derived:bars+forward', ?
        FROM bars b
        WHERE b.figi = ? AND b.ts >= ?
          AND NOT EXISTS (
              SELECT 1 FROM bars_adjusted ba
              WHERE ba.figi = b.figi AND ba.ts = b.ts
          )
        """,
        (now_iso, figi, ex_date_iso),
    )

    cur = conn.execute(
        """
        UPDATE bars_adjusted
        SET adj_open   = adj_open   / ?,
            adj_high   = adj_high   / ?,
            adj_low    = adj_low    / ?,
            adj_close  = adj_close  / ?,
            source     = 'derived:bars+forward',
            computed_at = ?
        WHERE figi = ? AND ts >= ?
        """,
        (factor, factor, factor, factor, now_iso, figi, ex_date_iso),
    )
    return cur.rowcount


def apply_all_pending(conn: sqlite3.Connection) -> int:
    """Walk every row in `corporate_actions` with action_type='split'
    in chronological order and apply_forward_split for each.
    Idempotent."""
    rows = conn.execute(
        "SELECT figi, ex_date, factor FROM corporate_actions "
        "WHERE action_type = 'split' "
        "ORDER BY ex_date"
    ).fetchall()
    total = 0
    for figi, ex_date_iso, factor in rows:
        ex_date = date.fromisoformat(ex_date_iso) if isinstance(ex_date_iso, str) else ex_date_iso
        total += apply_forward_split(conn, figi, ex_date, float(factor))
    return total
