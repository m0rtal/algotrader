"""One-off probe of the Tinkoff investAPI GetDividends gRPC method.

Prints type / repr / dir for every value the dividend fetcher will
read. Committed so the next implementer does not re-probe.

Run from apps/api/src/ as:
    ../../apps/api/.venv/bin/python -m scripts_import.probe_tinkoff_dividends
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC.parent) not in sys.path:
    sys.path.insert(0, str(_SRC.parent))

from algotrader_api.ingestion import real_client  # noqa: E402


def _broker_token() -> str:
    conn = sqlite3.connect("/home/hermes/algotrader/apps/api/data/state.db")
    try:
        row = conn.execute(
            "SELECT value FROM secrets WHERE key='broker_token'"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise SystemExit("no broker_token in secrets")
    return row[0]


async def _probe() -> None:
    token = _broker_token()
    client = real_client.RealTinkoffClient(token=token)
    async with client:
        # Pick a known-dividend-paying figi. GAZP pays dividends yearly.
        from_ = date(2024, 1, 1)
        to_ = date(2026, 1, 1)
        try:
            resp = await client.get_dividends(figi="BBG004730N88", from_=from_, to=to_)
        except Exception as e:
            print(f"ERROR calling get_dividends: {type(e).__name__}: {e}")
            return
        print(f"type(resp) = {type(resp)}")
        print(f"dir(resp) = {dir(resp)}")
        # Iterate likely-candidate fields:
        for attr in ("dividends", "items", "events", "payload", "value"):
            if hasattr(resp, attr):
                items = getattr(resp, attr)
                print(f"\nresp.{attr}: type={type(items)}, len={len(items) if hasattr(items, '__len__') else '?'}")
                if hasattr(items, "__iter__") and len(list(items) if hasattr(items, '__len__') else items) > 0:
                    first = next(iter(items))
                    print(f"  first item type={type(first)}")
                    print(f"  first item dir()={dir(first)}")
                    for f in dir(first):
                        if f.startswith("_"):
                            continue
                        try:
                            v = getattr(first, f)
                            if callable(v):
                                continue
                            print(f"    {f} = {type(v).__name__}: {v!r}")
                        except Exception as e:
                            print(f"    {f} = ERROR: {e}")


if __name__ == "__main__":
    asyncio.run(_probe())