# apps/api/src/algotrader_api/scripts_import/import_corporate_actions_moex.py
"""Import historical dividends from the MOEX ISS REST API.

Endpoint: GET https://iss.moex.com/iss/securities/{secid}/dividends.json
Free, no auth. Returns dividends registered on the Moscow Exchange.

The fetcher iterates every tradeable figi, resolves `figi → secid` via
the `instruments.ticker` column (assumes ticker == secid for MOEX
paper — true in practice; verify via iss.securities endpoint when in
doubt), and writes one CorporateActionRow per event.

Note: MOEX uses `secid` (ticker), not `figi`. We write rows with
figi=secid so the common writer works; downstream code that needs
true figi resolution should cross-reference with `instruments.figi`.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.request
from datetime import date
from typing import Optional

from .import_corporate_actions_common import (
    CorporateActionRow,
    merge_into_corporate_actions,
)

ISS_BASE = "https://iss.moex.com/iss/securities"
UA = {"User-Agent": "algotrader-corporate-actions-importer/1.0"}


def _secid_from_figi(db_path: str, figi: str) -> Optional[str]:
    """Return the secid (=ticker for MOEX paper) for the given figi.

    MOEX ISS uses secid (=ticker); we don't store secid explicitly so we
    read `instruments.ticker` as the best-available proxy. Returns None
    if the figi isn't in the table.
    """
    con = sqlite3.connect(db_path)
    cur = con.execute("SELECT ticker FROM instruments WHERE figi = ?", (figi,))
    row = cur.fetchone()
    con.close()
    return row[0] if row else None


def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_dividends_for_secid(
    secid: str, from_: date, to: date,
) -> list[CorporateActionRow]:
    """Fetch dividends for one secid from MOEX ISS and convert to rows."""
    url = f"{ISS_BASE}/{secid}/dividends.json?from={from_.isoformat()}&till={to.isoformat()}"
    try:
        payload = _http_get_json(url)
    except Exception:
        # Network errors are not fatal — return empty and move on.
        return []
    block = payload.get("dividends", {})
    cols = block.get("columns", [])
    rows_data = block.get("data", [])
    if not cols or not rows_data:
        return []
    # Build column index map
    idx = {name: i for i, name in enumerate(cols)}
    date_idx = idx.get("registry_close_date")
    value_idx = idx.get("value")
    secid_idx = idx.get("secid")
    if date_idx is None or value_idx is None:
        return []
    out: list[CorporateActionRow] = []
    for row in rows_data:
        try:
            d = date.fromisoformat(row[date_idx])
            v = float(row[value_idx])
        except (ValueError, TypeError, IndexError):
            continue
        if v <= 0:
            continue
        if not (from_ <= d <= to):
            continue
        out.append(CorporateActionRow(
            figi=row[secid_idx] if secid_idx is not None else secid,
            action_type="dividend",
            ex_date=d,
            factor=1.0,
            cash_amount=v,
            note="moex:iss",
            source="moex:iss:dividends",
        ))
    return out


def import_corporate_actions_moex(
    db_path: str, from_: date = date(2015, 1, 1), to: date = date.today(),
) -> int:
    rows: list[CorporateActionRow] = []
    con = sqlite3.connect(db_path)
    cur = con.execute(
        "SELECT figi FROM instruments WHERE class IN ('share','etf','bond')"
    )
    figis = [r[0] for r in cur]
    con.close()
    for figi in figis:
        secid = _secid_from_figi(db_path, figi)
        if not secid:  # pragma: no cover — defensive: every figi in `instruments` has a ticker
            continue
        rows.extend(fetch_dividends_for_secid(secid, from_, to))
    return merge_into_corporate_actions(db_path, rows)


if __name__ == "__main__":
    import sys  # pragma: no cover — operator entry point
    n = import_corporate_actions_moex(sys.argv[1])
    print(f"Imported {n} MOEX ISS dividends")