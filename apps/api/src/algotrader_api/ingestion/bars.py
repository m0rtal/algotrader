"""Fetch OHLCV bars for instruments and write atomically to parquet.

Atomic write pattern:
1. CREATE TABLE temp_bars AS SELECT * FROM existing UNION ALL new
2. COPY temp_bars TO '<temp>.parquet' (zstd)
3. os.replace(temp, final) — atomic on POSIX

If the parquet file doesn't exist yet, CREATE FROM new only.
"""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb

from ..observability.logging import get_logger

logger = get_logger("algotrader_api.ingestion.bars")


def _max_date_in_parquet(bar_file: Path) -> date | None:
    """Return the max trading date in an existing parquet file, or None if missing/empty."""
    if not bar_file.exists():
        return None
    try:
        conn = duckdb.connect(":memory:")
        result = conn.execute(f"SELECT MAX(ts) FROM read_parquet('{bar_file}')").fetchone()
        conn.close()
        if result and result[0] is not None:
            return result[0] if isinstance(result[0], date) else date.fromisoformat(str(result[0]))
    except Exception as e:
        logger.warning("bars.read_max_date_failed", path=str(bar_file), error=str(e))
    return None


def _atomic_write_parquet(bar_file: Path, new_rows: list[dict], *, ticker: str) -> None:
    """Append new_rows to bar_file atomically. New_rows may be empty.

    If the file doesn't exist yet, creates a new one with new_rows only.
    Otherwise, reads existing rows, appends new_rows (DuckDB UNION ALL),
    and writes atomically via temp file + rename.
    """
    bar_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{ticker}.",
        suffix=".parquet.tmp",
        dir=str(bar_file.parent),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        conn = duckdb.connect(":memory:")
        conn.execute(
            "CREATE TABLE bars (ts DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT)"
        )
        if bar_file.exists():
            try:
                conn.execute(
                    f"INSERT INTO bars SELECT * FROM read_parquet('{bar_file}')"
                )
            except Exception:
                # Existing file is malformed or unreadable — proceed with
                # new_rows only (caller will need to repair later).
                pass
        if new_rows:
            values_sql = ",".join(
                f"('{r['ts']}', {r['open']}, {r['high']}, {r['low']}, {r['close']}, {r['volume']})"
                for r in new_rows
            )
            conn.execute(f"INSERT INTO bars VALUES {values_sql}")
        # De-duplicate by ts — keep the last row for any duplicate date
        conn.execute(
            "CREATE TABLE bars_dedup AS "
            "SELECT ts, open, high, low, close, volume FROM ("
            "  SELECT *, ROW_NUMBER() OVER (PARTITION BY ts ORDER BY ts DESC) AS rn "
            "  FROM bars"
            ") WHERE rn = 1"
        )
        conn.execute(
            "CREATE TABLE bars_sorted AS "
            "SELECT ts, open, high, low, close, volume FROM bars_dedup ORDER BY ts"
        )
        conn.execute(f"COPY bars_sorted TO '{tmp_path}' (FORMAT PARQUET, COMPRESSION 'zstd')")
        conn.close()
        os.replace(tmp_path, bar_file)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


async def fetch_bars_for_instrument(
    client: Any,
    *,
    figi: str,
    ticker: str,
    bars_dir: str,
    history_years: int,
) -> int:
    """Fetch bars for one instrument. Returns rows written.

    Incremental: if <ticker>.parquet exists, only fetch bars after max date.
    Otherwise, fetch the full history_years.
    """
    bar_file = Path(bars_dir) / f"{ticker}.parquet"
    last = _max_date_in_parquet(bar_file)
    today = date.today()
    if last:
        date_from = (last + timedelta(days=1)).isoformat()
    else:
        date_from = (today - timedelta(days=365 * history_years)).isoformat()
    date_to = today.isoformat()
    if date_from > date_to:
        return 0  # up to date (last date is today or in the future)

    candles = await client.get_candles(
        figi=figi, date_from=date_from, date_to=date_to, interval="CANDLE_INTERVAL_DAY"
    )
    if not candles:
        # No data — leave existing file as-is, write empty only if new
        if not bar_file.exists():
            _atomic_write_parquet(bar_file, [], ticker=ticker)
        return 0

    _atomic_write_parquet(bar_file, candles, ticker=ticker)
    logger.info(
        "bars.instrument.done",
        ticker=ticker,
        from_=date_from,
        to=date_to,
        rows_written=len(candles),
    )
    return len(candles)


async def run_bars_phase(
    client: Any,
    *,
    instruments: list[dict],
    bars_dir: str,
    history_years: int,
    rate_limiter: Any,
    retry_policy: Any,
    progress_every: int = 10,
) -> tuple[int, int]:
    """Orchestrate fetch over all instruments. Returns (total_rows, rate_limit_hits)."""
    from .rate_limit import RateLimiter

    total_rows = 0
    rate_limit_hits = 0
    bar_classes = {"share", "etf"}
    targets = [i for i in instruments if i.get("class") in bar_classes]
    for idx, inst in enumerate(targets, start=1):
        ticker = inst["ticker"]
        figi = inst["figi"]
        try:
            await rate_limiter.acquire("get_candles")
            try:
                rows = await retry_policy.run(
                    lambda: fetch_bars_for_instrument(
                        client, figi=figi, ticker=ticker, bars_dir=bars_dir, history_years=history_years
                    )
                )
                total_rows += rows
                rate_limiter.record_success("get_candles")
            except Exception as e:
                if "RESOURCE_EXHAUSTED" in str(e).upper() or "rate limit" in str(e).lower():
                    rate_limit_hits += 1
                rate_limiter.record_error("get_candles")
                logger.warning("bars.instrument.failed", ticker=ticker, error=str(e))
        except Exception as e:
            logger.warning("bars.instrument.acquire_failed", ticker=ticker, error=str(e))

        if idx % progress_every == 0:
            logger.info(
                "bars.progress",
                done=idx,
                total=len(targets),
                rows_written=total_rows,
            )

    logger.info("bars.phase.done", total_rows=total_rows, rate_limit_hits=rate_limit_hits)
    return total_rows, rate_limit_hits


# ─── backfill runner support ────────────────────────────────────────


def _candle_to_row(c: Any) -> dict:
    """Convert a single gRPC candle (with is_complete flag) to a parquet row.

    Drops candles whose date is today or later — those are the
    in-progress live bar that Tinkoff occasionally returns with
    is_complete=True. The runner's defensive guard plus this helper's
    filter keeps parquet strictly historical.
    """
    from datetime import date as _date

    o = c.open
    h = c.high
    l = c.low
    cl = c.close
    t = c.time
    a_date = _date(t.year, t.month, t.day)
    if a_date >= _date.today():
        return None  # type: ignore[return-value]
    return {
        "ts": _date(t.year, t.month, t.day).isoformat(),
        "open": o.units + o.nano / 1_000_000_000,
        "high": h.units + h.nano / 1_000_000_000,
        "low": l.units + l.nano / 1_000_000_000,
        "close": cl.units + cl.nano / 1_000_000_000,
        "volume": getattr(c, "volume", 0),
    }


def append_bars(*, path: str, candles: list) -> int:
    """Append candles to a per-ticker parquet file. Returns rows written.

    The runner calls this once per ticker after filtering by
    `is_closed_candle`. Deduplicates by `ts` via the underlying
    `_atomic_write_parquet` (CREATE TABLE ... UNION ALL).
    """
    rows: list[dict] = []
    for c in candles:
        row = _candle_to_row(c)
        if row is not None:
            rows.append(row)
    if not rows:
        return 0
    bar_file = Path(path)
    # Use the FIGI-style filename; the existing bars.py helpers key on
    # ticker though, so the runner passes paths already named after FIGI.
    _atomic_write_parquet(bar_file=bar_file, new_rows=rows, ticker=bar_file.stem)
    return len(rows)
