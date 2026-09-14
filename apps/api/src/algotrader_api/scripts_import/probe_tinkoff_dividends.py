"""One-off probe of the Tinkoff investAPI GetDividends gRPC method.

Prints type / repr / dir for every value the dividend fetcher will
read. Committed so the next implementer does not re-probe.

Run from apps/api/ as:
    PYTHONPATH=src ./.venv/bin/python -m algotrader_api.scripts_import.probe_tinkoff_dividends
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import date


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
    from algotrader_api.ingestion import real_client

    token = _broker_token()
    client = real_client.RealTinkoffClient(token=token)
    try:
        from_ = date(2024, 1, 1)
        to_ = date(2026, 1, 1)
        try:
            resp = await client.get_dividends(figi="BBG004730N88", from_=from_, to=to_)
        except Exception as e:
            print(f"ERROR calling get_dividends: {type(e).__name__}: {e}")
            return
        print(f"type(resp) = {type(resp)}")
        print(f"dir(resp) = {dir(resp)}")
        for attr in ("dividends", "items", "events", "payload", "value"):
            if hasattr(resp, attr):
                items = getattr(resp, attr)
                print(
                    f"\nresp.{attr}: type={type(items)}, "
                    f"len={len(items) if hasattr(items, '__len__') else '?'}"
                )
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
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(_probe())
