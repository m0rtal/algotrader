"""Probe the live Tinkoff investAPI GetDividends response shape.

This script is a Task-1 probe from the data-pipeline-v2-trustworthy
OpenSpec change. It deliberately tries to call `client.get_dividends(...)`
on `RealTinkoffClient` even though that wrapper method does NOT exist
today — the resulting AttributeError confirms the wrapper is missing
the method and motivates Task 2 (which adds the impl).

If the wrapper is extended (Task 2 done), the probe will instead hit
the live SDK and print:

* `type(resp)` (expect `GetDividendsResponse` dataclass)
* `type(resp.dividends[0])` (expect `Dividend` dataclass) when non-empty
* `len(resp.dividends)` and the field list of the first row.

Run via the thin `apps/api/scripts/probe_tinkoff_dividends.py`
shim using:

    cd apps/api && PYTHONPATH=src \\
        ./.venv/bin/python -m algotrader_api.scripts_import.probe_tinkoff_dividends

A sandbox token from the local `.env` is fine; the request will simply
return an empty list when the FIGI is unknown to Tinkoff — that is
still enough to print the response shape.
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _load_token_from_env() -> str:
    """Best-effort load of TINKOFF_TOKEN / TINKOFF_SANDBOX_TOKEN from
    `apps/api/.env` if present. Falls back to empty string; the SDK
    will still construct but any real call needs a token."""
    env_path = Path(__file__).resolve().parents[3] / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() in ("TINKOFF_TOKEN", "TINKOFF_SANDBOX_TOKEN"):
                return v.strip().strip('"').strip("'")
    return os.environ.get("TINKOFF_SANDBOX_TOKEN") or os.environ.get("TINKOFF_TOKEN", "")


def _print_response_shape(resp: object) -> None:
    """Print type + dataclass field list + first-row fields for a
    GetDividendsResponse-shaped object. Defensive against empty lists."""
    print(f"type(resp) = {type(resp).__name__}")
    if is_dataclass(resp):
        for f in fields(resp):
            print(f"  resp.{f.name}: {f.type}")
    dividends = getattr(resp, "dividends", None)
    if dividends is None:
        print("resp has no .dividends attribute")
        return
    print(f"len(resp.dividends) = {len(dividends)}")
    if dividends:
        first = dividends[0]
        print(f"type(resp.dividends[0]) = {type(first).__name__}")
        if is_dataclass(first):
            for f in fields(first):
                print(f"  dividends[0].{f.name}: {f.type}")


async def _run() -> int:
    from algotrader_api.ingestion.real_client import RealTinkoffClient

    token = _load_token_from_env()
    client = RealTinkoffClient(token=token, target="sandbox")
    # Sandbox FIGI from Tinkoff docs: BBG004730N88 (Sber). Window is
    # wide enough to catch any past dividends without paging.
    figi = "BBG004730N88"
    from_ = datetime(2015, 1, 1, tzinfo=timezone.utc)
    to = datetime.now(tz=timezone.utc) + timedelta(days=1)
    print(
        f"Calling client.get_dividends(figi={figi!r}, from_={from_.date()}, "
        f"to={to.date()}) — expecting AttributeError if wrapper not yet implemented."
    )
    try:
        resp = await client.get_dividends(figi=figi, from_=from_, to=to)
    except AttributeError as e:
        print(f"AttributeError: {e}")
        print(
            "Confirmed: RealTinkoffClient has no get_dividends() — "
            "this is the expected Task-1 outcome. Task 2 will add it."
        )
        return 1
    except Exception as e:
        print(f"{type(e).__name__}: {e}")
        return 2
    _print_response_shape(resp)
    try:
        await client.aclose()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))