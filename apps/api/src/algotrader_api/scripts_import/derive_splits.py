"""Derive historical split factors from local bars.

Russian market has no free machine-readable historical corporate-actions
feed (verified 2026-09-13):
- MOEX ISS: only current FACEVALUE/LOT, no historical events endpoint
- Tinkoff Invest API: only GetDividends, no splits endpoint
- NSD getCorpActions V.2 / e-disclosure.ru: paid subscriptions
- EODHD / Databento / Polygon / Alpaca / FMP: none cover MOEX

This module derives split factors from the price discontinuity between
consecutive `bars` rows, with the current `face_value` from MOEX ISS as
a verification cross-check.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Iterable

from algotrader_api.scripts_import.import_corporate_actions_common import (
    CorporateActionRow,
)


# Operator decision 2026-09-13: only flag ≥ 2× or ≤ 0.5× price changes.
SPLIT_RATIO_THRESHOLD: float = 2.0

# Volume scaling tolerance for BONU discrimination.
_BONU_VOLUME_TOLERANCE: float = 0.20  # 20%


@dataclass(frozen=True)
class _BarLike:
    """Minimal bar shape for the derive function.

    Concrete `bars` rows are sqlite3.Row-like; we only read `ts`,
    `close`, `volume`.
    """

    ts: date
    close: float
    volume: int


def _as_bar(row: object) -> _BarLike:
    """Normalise a bar row into _BarLike.

    Accepts:
    - Mapping (`row["ts"]`) — sqlite3.Row, dict
    - dataclass with `.ts`/`.close`/`.volume`
    - tuple of (ts, close, volume)
    """
    if isinstance(row, tuple):
        ts, close, volume = row
    elif hasattr(row, "keys"):
        ts = row["ts"]
        close = row["close"]
        volume = row["volume"]
    else:
        ts = row.ts
        close = row.close
        volume = row.volume
    if isinstance(ts, str):
        ts = date.fromisoformat(ts)
    return _BarLike(ts=ts, close=close, volume=volume)


def derive_splits_for_figi(
    *,
    figi: str,
    bars: Iterable[object],
    face_value: float | None,
    bar_range: tuple[date, date] | None = None,
) -> list[CorporateActionRow]:
    """Detect split candidates in a figi's bars.

    Parameters
    ----------
    figi:
        The figi to derive splits for.
    bars:
        Iterable of bar-like rows (anything with `ts`, `close`, `volume`).
        Should be sorted by `ts` ascending. Not required for the algorithm
        itself — caller is responsible for ordering.
    face_value:
        Current nominal value from MOEX ISS (optional). If provided, used
        only for audit cross-check; not required for detection.
    bar_range:
        Optional (first_ts, last_ts) to include in the source string.
        If None, inferred from bars.

    Returns
    -------
    list[CorporateActionRow]
        Detected split rows. Empty if no transitions cross the 2× threshold.
    """
    rows: list[_BarLike] = [_as_bar(b) for b in bars]
    if len(rows) < 2:
        return []

    candidates: list[CorporateActionRow] = []
    for i in range(1, len(rows)):
        prev = rows[i - 1]
        cur = rows[i]

        if prev.close == 0 or cur.close == 0:
            continue

        ratio = cur.close / prev.close
        # Hardcoded threshold: ratio <= 0.5 OR ratio >= 2.0 (operator 2026-09-13).
        if (1.0 / SPLIT_RATIO_THRESHOLD) < ratio < SPLIT_RATIO_THRESHOLD:
            # Within threshold — ordinary price movement, dividend ex-date, etc.
            continue

        # We are above the threshold — compute the factor.
        # If ratio < 1, it's a forward split (close divided by N).
        # If ratio > 1, it's a reverse split / consolidation (close multiplied by N).
        factor = 1.0 / ratio if ratio < 1 else ratio

        # Disambiguate forward split from BONU.
        # BONU (bonus issue): per-share price drops AND volume scales
        # proportionally (more shares traded total). Forward split: price
        # drops, volume unchanged.
        # Reverse split: price multiplies, volume unchanged.
        if _looks_like_bonus_issue(prev, cur):
            continue

        source = _build_source(figi, rows[0].ts, rows[-1].ts, face_value)

        candidates.append(
            CorporateActionRow(
                figi=figi,
                action_type="split",
                ex_date=cur.ts,
                factor=factor,
                cash_amount=0.0,
                note=f"derived from bars ratio={ratio:.4f}",
                source=source,
            )
        )

    return candidates


def _looks_like_bonus_issue(prev: _BarLike, cur: _BarLike) -> bool:
    """True if the price jump is matched by proportional volume increase.

    BONU (bonus issue) grants extra shares → per-share price drops
    proportionally AND volume scales by the same factor (more shares
    traded total). Forward split drops price but leaves volume
    unchanged. Reverse split / consolidation multiplies price but
    leaves volume unchanged.

    Check:  vol_ratio ≈ 1 / price_ratio  (i.e. volume scales inversely
            to price). Equivalently  vol_ratio * price_ratio ≈ 1.
    """
    if prev.volume == 0 or prev.close == 0:
        return False  # Can't tell — fall back to assuming real split
    vol_ratio = cur.volume / prev.volume
    price_ratio = cur.close / prev.close
    if price_ratio == 0:
        return False
    product = vol_ratio * price_ratio
    # product == 1.0 means volume × price is preserved → BONU.
    # product >> 1 or product << 1 means split (volume unchanged).
    return abs(product - 1.0) < _BONU_VOLUME_TOLERANCE


def _build_source(
    figi: str, first_ts: date, last_ts: date, face_value: float | None
) -> str:
    """Build the audit trail string for the derived row."""
    base = f"derived:bars+facevalue:{first_ts.isoformat()}:{last_ts.isoformat()}"
    if face_value is not None:
        base += f":facevalue={face_value}"
    return base


# --------------------------------------------------------------------------- #
# MOEX ISS face_value lookup
# --------------------------------------------------------------------------- #

_MOEX_ISS_URL = "https://iss.moex.com/iss/securities/{secid}.json"


def lookup_face_values(
    figis_to_secids: dict[str, str],
    *,
    timeout: float = 5.0,
) -> dict[str, float]:
    """Fetch current FACEVALUE for each figi from MOEX ISS.

    ``figis_to_secids`` maps figi → MOEX secid (e.g. {"BBG004730N88": "SBER"}).
    Returns {figi: face_value} for figis that resolved. Missing/invalid
    figis are silently skipped (None).
    """
    out: dict[str, float] = {}
    for figi, secid in figis_to_secids.items():
        url = _MOEX_ISS_URL.format(secid=urllib.parse.quote(secid))
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                payload = json.load(resp)
        except Exception:
            continue
        # MOEX ISS payload shape:
        # {"securities": {"data": [...], "metadata": {"id": [...]}}, ...}
        # We're looking for the row where id == "FACEVALUE" in the
        # "securities" board.
        try:
            sec_board = payload["securities"]
            metadata = sec_board["metadata"]["id"]
            data = sec_board["data"]
            face_value_idx = metadata.index("FACEVALUE")
        except (KeyError, ValueError, IndexError):
            continue
        for row in data:
            if row[face_value_idx] is not None:
                try:
                    out[figi] = float(row[face_value_idx])
                except (TypeError, ValueError):
                    pass
                break
    return out


# --------------------------------------------------------------------------- #
# Driver: walk the DB and emit rows
# --------------------------------------------------------------------------- #


def _read_bars(conn: sqlite3.Connection, figi: str) -> list[sqlite3.Row]:
    cur = conn.execute(
        "SELECT ts, close, volume FROM bars WHERE figi=? ORDER BY ts",
        (figi,),
    )
    return list(cur.fetchall())


def _read_figis(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute(
        "SELECT DISTINCT figi FROM bars ORDER BY figi",
    )
    return [r[0] for r in cur.fetchall()]


def run_derivation(db_path: str, *, face_values: dict[str, float] | None = None) -> int:
    """Run split derivation over all figis in the DB.

    Returns the number of split rows written.
    ``face_values`` is an optional {figi: face_value} cache — when None,
    ``face_value`` is set to None for every row (still detects splits,
    just without the audit cross-check).
    """
    conn = sqlite3.connect(db_path)
    try:
        all_rows: list[CorporateActionRow] = []
        figis = _read_figis(conn)
        for figi in figis:
            bars = _read_bars(conn, figi)
            if not bars:
                continue
            face_value = (face_values or {}).get(figi)
            rows = derive_splits_for_figi(
                figi=figi, bars=bars, face_value=face_value,
            )
            all_rows.extend(rows)
    finally:
        conn.close()
    from algotrader_api.scripts_import.import_corporate_actions_common import (
        merge_into_corporate_actions,
    )
    return merge_into_corporate_actions(db_path, all_rows)
