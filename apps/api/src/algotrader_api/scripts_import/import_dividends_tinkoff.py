"""Tinkoff investAPI dividends fetcher.

Calls client.get_dividends(figi, from_, to) for every tradeable figi and
persists via merge_into_dividends. Idempotent on PK.

Throttled figis (RESOURCE_EXHAUSTED) are recorded in
`dividends_throttle_pending`; the next cycle drains that queue
before any non-queued figi. Per-figi fetches are gated through the
global `RateLimiter` so we honour the Tinkoff per-method cap.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from datetime import date, datetime, timezone, timedelta
from typing import Any, Protocol

from algotrader_api.ingestion.retry import _is_rate_limit_error
from algotrader_api.observability.logging import get_logger
from algotrader_api.scripts_import.import_corporate_actions_common import (
    DividendRow,
    merge_into_dividends,
)

MSK = timezone(timedelta(hours=3))
_LOG = get_logger("algotrader_api.scripts_import.import_dividends_tinkoff")


# Process-wide RateLimiter singleton. The plan § Task 2.4 assumes the
# limiter lives at `algotrader_api.ingestion.rate_limit._GLOBAL`; that
# module currently does not expose a singleton, so we ensure one here
# at import time. Test code that does
#     monkeypatch.setattr(rl, "_GLOBAL", rl.RateLimiter(...))
# will overwrite this attribute for the duration of the test.
from algotrader_api.ingestion import rate_limit as _rl  # noqa: E402
if not hasattr(_rl, "_GLOBAL"):
    _rl._GLOBAL = _rl.RateLimiter()


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


# ─── throttle-queue helpers (PR-2) ──────────────────────────────────────
#
# These three helpers read / write the persistent
# `dividends_throttle_pending` table. The table is created by
# migration 022_dividends_throttle_pending.sql; on a fresh DB where
# migration 022 hasn't run, the read returns [] so we degrade to
# the no-queue path silently. The write helpers also swallow
# `OperationalError` from the same situation so a partial
# deployment can't crash the cycle.


def _read_pending_figis(db_path: str) -> list[str]:
    """Read figis currently queued for retry, oldest first."""
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        try:
            cur = conn.execute(
                "SELECT figi FROM dividends_throttle_pending "
                "ORDER BY last_failed_at ASC"
            )
            return [r[0] for r in cur.fetchall()]
        except sqlite3.OperationalError:
            # Fresh DB without migration 022 yet — no queue yet.
            return []
    finally:
        conn.close()


def _queue_throttled_figi(db_path: str, figi: str) -> None:
    """Mark a figi as throttled (idempotent upsert).

    `first_failed_at` is only set on the first INSERT — the
    `ON CONFLICT` clause does NOT touch it, so we keep the
    original timestamp. `last_failed_at` and `retry_count`
    advance on every retry.
    """
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        now = datetime.now(MSK).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO dividends_throttle_pending "
            "(figi, first_failed_at, last_failed_at, retry_count) "
            "VALUES (?, ?, ?, 1) "
            "ON CONFLICT(figi) DO UPDATE SET "
            "  last_failed_at = excluded.last_failed_at, "
            "  retry_count = retry_count + 1",
            (figi, now, now),
        )
        conn.commit()
    except sqlite3.OperationalError:
        # Fresh DB without migration 022 yet — nothing to queue.
        pass
    finally:
        conn.close()


def _dequeue_figi(db_path: str, figi: str) -> None:
    """Remove a figi from the throttle queue on success."""
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.execute(
            "DELETE FROM dividends_throttle_pending WHERE figi = ?",
            (figi,),
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()


# Per-method key used by the RateLimiter. Dividends share a bucket
# so an unrelated methods (e.g. universe discovery) don't starve
# this slow phase.
_DIVIDENDS_METHOD = "get_dividends"


def fetch_and_persist(
    db_path: str,
    *,
    client: _Client | None = None,
    figis: list[str] | None = None,
    from_year: int | None = None,
) -> tuple[int, int]:
    """Fetch and persist dividends for tradeable figis.

    Returns ``(written, queued)``:
      - ``written``: number of dividend rows merged into the
        ``dividends`` table this call.
      - ``queued``: number of figis that hit a Tinkoff
        ``RESOURCE_EXHAUSTED`` rate-limit error during this call
        and were queued in ``dividends_throttle_pending`` for the
        next cycle.

    Ordering: any figis already in `dividends_throttle_pending`
    are attempted first (oldest `last_failed_at` first), then the
    caller's `figis` list. Per-figi fetches are gated through the
    global `RateLimiter` so the cycle honours the per-minute cap.
    On `RESOURCE_EXHAUSTED` the figi is queued for the next cycle
    and the loop continues.
    """
    requested = figis or _list_tradeable_figis(db_path)

    # Drain order: pending first (oldest first), then the requested
    # set, with duplicates removed. Preserves requested-list order
    # among the non-pending tail.
    pending = _read_pending_figis(db_path)
    seen: set[str] = set()
    final_figis: list[str] = []
    for f in pending:
        if f not in seen:
            final_figis.append(f)
            seen.add(f)
    for f in requested:
        if f not in seen:
            final_figis.append(f)
            seen.add(f)

    if not final_figis:
        return 0, 0

    from_year = from_year or date.today().year - 2
    from_ = date(from_year, 1, 1)
    to_ = date.today() + timedelta(days=30)
    retrieved_at = _fetched_at()

    # The rate-limit module's `_GLOBAL` attribute is the canonical
    # accessor (PR #129 pattern). We ensure it exists at import time
    # (see top-of-file); tests monkeypatch it to inject a fresh limiter.
    from algotrader_api.ingestion import rate_limit as rl
    limiter = rl._GLOBAL

    async def _run() -> tuple[int, int]:
        assert client is not None
        written_total = 0
        queued_total = 0
        async with client:
            for figi in final_figis:
                await limiter.acquire(_DIVIDENDS_METHOD)
                try:
                    items = await client.get_dividends(figi, from_, to_)
                except Exception as e:
                    if _is_rate_limit_error(e):
                        # Soft-fail: signal the global limiter, queue
                        # the figi for next cycle, keep going.
                        try:
                            limiter.signal_throttle(_DIVIDENDS_METHOD)
                        except Exception:
                            pass
                        _queue_throttled_figi(db_path, figi)
                        queued_total += 1
                        _LOG.warning(
                            "dividends.tinkoff.throttled",
                            figi=figi, error=str(e),
                        )
                        continue
                    # Non-rate-limit error: log + skip (preserves
                    # pre-PR-2 behaviour).
                    _LOG.warning(
                        "dividends.tinkoff.figi_failed",
                        figi=figi, error=str(e),
                    )
                    continue
                rows = [_to_row(figi, d, retrieved_at=retrieved_at) for d in items]
                if rows:
                    written_total += merge_into_dividends(db_path, rows)
                # On success (rows OR empty), the figi is no longer
                # throttled — clear any stale queue row.
                _dequeue_figi(db_path, figi)
        return written_total, queued_total

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
    written, _queued = fetch_and_persist(args.db_path, client=client)
    print(f"Wrote {written} dividend rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
