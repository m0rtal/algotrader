"""Tinkoff investAPI dividends fetcher.

Calls client.get_dividends(figi, from_, to) for every tradeable figi and
persists via merge_into_dividends. Idempotent on PK.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from datetime import date, datetime, timezone, timedelta
from typing import Any, Protocol

from algotrader_api.scripts_import.import_corporate_actions_common import (
    DividendRow,
    merge_into_dividends,
)

MSK = timezone(timedelta(hours=3))


class _Client(Protocol):
    async def get_dividends(self, figi: str, from_: date, to: date) -> Any: ...


def _fetched_at() -> str:
    return datetime.now(MSK).strftime("%Y-%m-%dT%H:%M:%S")


def _to_row(figi: str, d: dict, *, retrieved_at: str) -> DividendRow:
    """Map the dict shape from Task 2's wrapper to a DividendRow."""
    ex_date = d["ex_date"]
    # declared_at may be ISO timestamp; period_year comes from
    # declared_at.year (declared year = dividend year for the
    # Russian fiscal calendar).
    declared = d.get("declared_at") or ex_date
    if "T" in declared:
        period_year = int(declared[:4])
    else:
        period_year = int(declared[:4])
    yield_value = d.get("yield_value")
    yield_pct = (yield_value / d["close_price"]) if (
        yield_value is not None and d.get("close_price")
    ) else None
    return DividendRow(
        figi=figi,
        ex_date=ex_date,
        pay_date=d.get("pay_date"),
        record_date=d.get("record_date"),
        declared_at=d.get("declared_at"),
        period_year=period_year,
        period_no=1,
        currency=d.get("currency", "rub"),
        amount_per_share=float(d.get("amount_per_share", 0.0)),
        dividend_type=(d.get("dividend_type") or "regular").lower(),
        regularity=d.get("regularity"),
        close_price=d.get("close_price"),
        yield_value=yield_value,
        yield_pct=yield_pct,
        source="tinkoff",
        source_revision_ts=d.get("created_at"),
        retrieved_at=retrieved_at,
        revision_n=1,
    )


def _list_tradeable_figis(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT DISTINCT figi FROM instruments "
            "WHERE class IN ('share', 'etf', 'bond') "
            "ORDER BY figi"
        )
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def fetch_and_persist(
    db_path: str,
    *,
    client: _Client | None = None,
    figis: list[str] | None = None,
    from_year: int | None = None,
) -> int:
    """Fetch and persist dividends for tradeable figis.
    Returns the number of rows written."""
    target_figis = figis or _list_tradeable_figis(db_path)
    if not target_figis:
        return 0
    from_year = from_year or date.today().year - 2
    from_ = date(from_year, 1, 1)
    to_ = date.today() + timedelta(days=30)
    retrieved_at = _fetched_at()

    async def _run() -> int:
        assert client is not None
        written_total = 0
        async with client:
            for figi in target_figis:
                try:
                    items = await client.get_dividends(figi, from_, to_)
                except Exception as e:
                    # Per-figi failure is non-fatal (rate-limit / delisted).
                    import structlog
                    structlog.get_logger().warning(
                        "dividends.tinkoff.figi_failed",
                        figi=figi, error=str(e),
                    )
                    continue
                rows = [_to_row(figi, d, retrieved_at=retrieved_at) for d in items]
                if rows:
                    written_total += merge_into_dividends(db_path, rows)
        return written_total

    return asyncio.run(_run())


def main() -> int:  # pragma: no cover
    import argparse
    from algotrader_api.ingestion import real_client
    p = argparse.ArgumentParser(description="Fetch dividends from Tinkoff")
    p.add_argument("db_path", type=str)
    args = p.parse_args()
    conn = sqlite3.connect(args.db_path)
    try:
        row = conn.execute(
            "SELECT value FROM secrets WHERE key='broker_token'"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        print("ERROR: no broker_token in secrets", file=sys.stderr)
        return 2
    client = real_client.RealTinkoffClient(token=row[0])
    n = fetch_and_persist(args.db_path, client=client)
    print(f"Wrote {n} dividend rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
