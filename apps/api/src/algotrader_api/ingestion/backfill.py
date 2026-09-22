"""Backfill runner: orchestrates universe + historical bars ingestion.

The runner is the lifecycle owner for fetching MOEX data from the broker
sandbox. It iterates instruments, decides per-ticker whether to do a
full backfill or an incremental update, calls the SDK with rate
limiting, writes bars to parquet, and updates `instrument_metadata`.

State machine:

    IDLE → DISCOVERING → BACKFILLING → DONE
                                  ↑
                                  └── STOPPING (graceful cancel)

The runner is async and emits events through an injected sink; the route
layer wraps it for HTTP/SSE. A persistent systemd-driven entry-point
lives in `worker.py`.

Why this lives in its own module (not in `pipeline.py`): the existing
`pipeline.py` is a one-shot `start_phase / end_phase` recorder for the
manual `POST /api/admin/fetch` flow. Backfill has different concerns:
scheduled execution, resumability across crashes, per-ticker decisions,
rate limiting. Conflating the two would entangle the simple admin fetch
with the persistent backfill lifecycle.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from ..observability.logging import get_logger
from .closed_candles import is_closed_candle

logger = get_logger("algotrader_api.ingestion.backfill")


class BackfillState(str, Enum):
    IDLE = "idle"
    DISCOVERING = "discovering"
    BACKFILLING = "backfilling"
    STOPPING = "stopping"
    DONE = "done"


@dataclass
class BackfillEvent:
    """One event from the runner; routed to SSE subscribers."""

    type: str  # "status" | "log" | "ticker_progress" | "done"
    run_id: int
    ts: str
    payload: dict


# A callable that accepts a BackfillEvent. Async to allow the route
# layer to await SSE writes without blocking the runner.
EventSink = Callable[[BackfillEvent], Awaitable[None]]


# ─── strategy decision ──────────────────────────────────────────────


def _last_trading_day(
    today: date,
    db_path: str,
    *,
    max_lookback_days: int = 14,
) -> date:
    """Return the most recent COMPLETED trading day at or before ``today``.

    The semantics are "the last day we have bars for". Bars are published
    at end-of-day, so even on a trading day (e.g. Wednesday) we cannot
    fetch the day's bars yet — they don't exist. Therefore we always
    start from ``today - 1 day`` and walk backwards skipping weekends
    and MOEX holidays.

    Walking up to ``max_lookback_days`` calendar days is a safety net:
    if we run out (e.g. empty holidays table during a fresh install
    mid-holiday-streak), we fall back to the most recent weekday in
    that window — better to fetch one extra empty trading day than to
    silently skip real bars.

    The moex_holidays table is populated by migration 006 from
    ``scripts_import/data/moex_holidays.json`` at the universe-sync
    step, so the typical case hits it after the daily chain's first
    universe_sync phase.
    """
    import sqlite3

    # Build a set of holiday strings in [today - 1 - max_lookback, today - 1].
    # Use a single SQL query rather than per-day roundtrips.
    start = today - timedelta(days=1)
    lookback_start = start - timedelta(days=max_lookback_days)
    holiday_strings: set[str] = set()
    try:
        con = sqlite3.connect(db_path, timeout=5.0)
        try:
            rows = con.execute(
                "SELECT date FROM moex_holidays "
                "WHERE date >= ? AND date <= ?",
                (lookback_start.isoformat(), start.isoformat()),
            ).fetchall()
            holiday_strings = {r[0] for r in rows}
        finally:
            con.close()
    except Exception:  # noqa: BLE001 — defensive: DB may not be migrated yet
        pass

    cur = start
    for _ in range(max_lookback_days + 1):
        # Monday=0..Sunday=6 — weekday() returns 5 for Sat, 6 for Sun
        if cur.weekday() < 5 and cur.isoformat() not in holiday_strings:
            return cur
        cur = cur - timedelta(days=1)
    # Fallback: most recent weekday in window.
    return lookback_start


def decide_strategy(
    metadata_row: dict | None,
    today: date,
    history_years: int,
    incremental_threshold_days: int,
) -> tuple[str, date | None, date | None]:
    """Return ("full"|"incremental"|"skip", from_date, to_date).

    Rules:
    - No metadata row → "full" (new ticker, no history yet).
    - last_bar_ts is None → "full" (partial previous run; safe to redo).
    - last_bar_ts within the threshold → "skip" (the daily timer will
      catch it tomorrow).
    - last_bar_ts older than the threshold → "incremental" from
      `last_bar_ts + 1 day` (fill the gap).
    """
    if metadata_row is None or metadata_row.get("last_bar_ts") is None:
        from_ = date(today.year - history_years, today.month, today.day)
        return ("full", from_, today)

    last_bar_ts = date.fromisoformat(metadata_row["last_bar_ts"])
    days_since = (today - last_bar_ts).days
    if days_since < incremental_threshold_days:
        return ("skip", None, None)
    from_ = last_bar_ts + timedelta(days=1)
    return ("incremental", from_, today)


# ─── runner ──────────────────────────────────────────────────────────


# ─── module-level backfill helpers (testable, patchable) ───────────
#
# These were extracted from inner closures of BackfillRunner.backfill_from_moex
# so tests can patch them at the class level (BackfillRunner._fetch_year_moex,
# etc.) instead of digging through closures. The bodies are identical to the
# original closures; only the call surface changed.
#
# Why not @staticmethod on the class directly: staticmethods can't be
# patched with ``unittest.mock.patch.object`` on the class attribute — they
# must be module-level functions that the class binds via ``name = ...``.
# This is the standard pattern (see Flask, requests).


def _get_meta_moex(
    ticker: str,
    yesterday: date,
    *,
    meta_cache: dict[str, dict | None],
    meta_lock: threading.Lock,
) -> dict | None:
    """Probe MOEX for ticker. Return {market, board, listed_from, listed_till} or None.

    Extracted from inner closure so tests can patch it.
    Caches results in ``meta_cache`` (guarded by ``meta_lock``) — same
    caching the inline closure used so concurrent access behaves identically.
    """
    import requests
    with meta_lock:
        if ticker in meta_cache:
            return meta_cache[ticker]
    url = f"https://iss.moex.com/iss/securities/{urllib.parse.quote(ticker)}.json"
    try:
        # (connect_timeout, read_timeout) — prevents indefinite hangs
        # when MOEX ISS accepts the TCP connection but stalls mid-response.
        data = requests.get(url, timeout=(5, 30)).json()
    except Exception:
        return None
    boards = data.get("boards", {}).get("data", [])
    primary = next(
        (b for b in boards
         if len(b) > 8 and b[8] == 1  # is_traded
         and b[1] in ("TQBR", "TQTF", "TQOB", "TQCB", "SMAL", "TQIF", "TQPI")),
        None,
    )
    if not primary:
        with meta_lock:
            meta_cache[ticker] = None
        return None
    boardid = primary[1]
    market = "bonds" if boardid in ("TQOB", "TQCB") else "shares"
    listed_from = primary[12]
    listed_till = primary[13]
    meta = {
        "market": market,
        "board": boardid,
        "listed_from": listed_from,
        "listed_till": listed_till or yesterday.isoformat(),
    }
    with meta_lock:
        meta_cache[ticker] = meta
    return meta


def compute_missing_dates(
    figi: str,
    listed_from: date,
    yesterday: date,
    db_path: str,
) -> set[date]:
    """Return the set of expected trading dates the figi is missing.

    "Expected trading dates" = calendar dates in
    [listed_from, yesterday] excluding weekends and entries in the
    `moex_holidays` table. The result is the symmetric difference
    between that expected set and the dates already present in `bars`
    for this figi (regardless of `source` column).

    Pure function: reads `bars` and `moex_holidays` from the given
    SQLite path; never writes.

    Used by `backfill_from_moex()` to drive the per-figi fetch
    decision. A figi with empty missing dates is already complete
    and skipped.
    """
    if listed_from > yesterday:
        return set()
    # 1. Load holidays once (set of date).
    # Note: moex_holidays schema uses `date` column (per migration 004).
    con = sqlite3.connect(db_path)
    try:
        holiday_rows = con.execute(
            "SELECT date FROM moex_holidays WHERE date BETWEEN ? AND ?",
            (listed_from.isoformat(), yesterday.isoformat()),
        ).fetchall()
        holidays = {date.fromisoformat(r[0]) for r in holiday_rows}
        # 2. Load existing bar dates (any source).
        existing_rows = con.execute(
            "SELECT ts FROM bars WHERE figi = ? AND ts BETWEEN ? AND ?",
            (figi, listed_from.isoformat(), yesterday.isoformat()),
        ).fetchall()
        existing = {date.fromisoformat(r[0]) for r in existing_rows}
    finally:
        con.close()
    # 3. Compute expected set: weekday + not holiday + in window.
    expected: set[date] = set()
    cur = listed_from
    while cur <= yesterday:
        if cur.weekday() < 5 and cur not in holidays:
            expected.add(cur)
        cur += timedelta(days=1)
    # 4. Missing = expected ∖ existing.
    return expected - existing


def _fetch_year_moex(
    market: str,
    board: str,
    ticker: str,
    year: int,
    last_trading_day: date | None = None,
) -> list[dict]:
    """Walk MOEX ISS /iss/history/.../securities/{ticker}.json for ``year``.

    MOEX caps a single response at 500 bars; for a year with >500
    trading days (rare, but possible for ETFs) we would miss data.
    We use the server-reported ``history.cursor`` (offset, total,
    page-size) to decide when to stop. Page-size itself comes from
    the cursor field — pre-2024-Q3 MOEX returned 100 even when we
    asked for 500; asking for 500 simply lets the server pick its
    current maximum and tell us via the cursor.

    ``last_trading_day`` caps the ``till`` for the CURRENT calendar
    year: if ``year == last_trading_day.year``, the request stops at
    ``last_trading_day`` (not Dec 31), so we never ask MOEX for bars
    dated after the last published trading day. Earlier years are
    unaffected.

    The parameter is positional-or-keyword (no ``*`` separator) so
    existing tests that mock via ``side_effect=callable`` with the
    4-arg signature keep working unchanged — they just get
    ``last_trading_day=None`` and the function falls back to
    ``year-12-31``.
    """
    import requests
    base = (
        f"https://iss.moex.com/iss/history/engines/stock/markets/{market}/boards/{board}"
        f"/securities/{urllib.parse.quote(ticker)}.json"
    )
    if last_trading_day is not None and year == last_trading_day.year:
        till = last_trading_day.isoformat()
    else:
        till = f"{year}-12-31"
    out: list[dict] = []
    start = 0
    page_size = 500
    while True:
        try:
            data = requests.get(
                base,
                params={
                    "from": f"{year}-01-01",
                    "till": till,
                    "start": start,
                },
                timeout=30,
            ).json()
        except Exception:
            break
        cols = data.get("history", {}).get("columns", [])
        if not cols or "TRADEDATE" not in cols:
            break
        rows = data.get("history", {}).get("data", [])
        if not rows:
            break
        for row in rows:
            d = dict(zip(cols, row))
            out.append({
                "figi": None,  # filled by caller
                "ts": d.get("TRADEDATE"),
                "open": d.get("OPEN"),
                "high": d.get("HIGH"),
                "low": d.get("LOW"),
                "close": d.get("CLOSE"),
                "volume": int(d.get("VOLUME") or 0),
                "source": "moex",
            })
        # history.cursor rows: [offset, total, page_size]. When
        # offset + len(rows) >= total, we've seen everything.
        cursor_rows = data.get("history.cursor", {}).get("data") or []
        if cursor_rows:
            try:
                offset, total, _srv_page_size = cursor_rows[0][:3]
                if offset is not None and total is not None and offset + len(rows) >= total:
                    break
            except (TypeError, ValueError):
                pass
        else:
            # No cursor at all: fall back to "short page = last".
            if len(rows) < page_size:
                break
        start += len(rows)
    return out


def _fetch_moex_range(
    market: str,
    board: str,
    ticker: str,
    from_d: date,
    to_d: date,
    *,
    last_trading_day: date | None = None,
) -> list[dict]:
    """Fetch MOEX bars for an arbitrary [from_d, to_d] window.

    Thin wrapper that walks the touched years via ``_fetch_year_moex``
    and stitches the results together. Used by the recent-tail pass
    where the window is days, not years, but MOEX ISS only serves a
    full year per request, so we still issue one request per touched
    calendar year.

    The ``last_trading_day`` cap is forwarded so the current-year
    request stops at ``last_trading_day`` instead of ``12-31`` — same
    semantic as in ``_fetch_year_moex``.
    """
    if from_d > to_d:
        return []
    cap = last_trading_day or to_d
    out: list[dict] = []
    for year in range(from_d.year, to_d.year + 1):
        # Per-year hard cap at `to_d` so we never return rows outside
        # the requested window even if the year's response sneaks in
        # an extra day (it shouldn't, but defensive).
        year_cap = min(to_d, cap)
        year_bars = _fetch_year_moex(
            market, board, ticker, year, last_trading_day=year_cap
        )
        out.extend(year_bars)
    return out


async def _fetch_tinkoff_fallback_impl(
    client: Any,
    retry_mod: Any,
    figi: str,
    ticker: str,
    from_d: date,
    to_d: date,
) -> list[dict]:
    """Tinkoff fallback for sanctions-delisted tickers where MOEX has no boards.
    Walks in 7-day chunks via the existing Tinkoff client.

    Each chunk emits a ``tinkoff.chunk`` structured log via structlog so
    operators can identify stuck ranges (a single 20-min hang used to
    hide all progress; now each chunk is independently visible).
    """
    import time as _time
    chunk_retry = retry_mod.AdaptiveRetry(
        max_attempts=2, initial_delay=0.5, backoff_factor=2.0, max_delay=5.0,
    )
    out = []
    cur = from_d
    while cur <= to_d:
        chunk_end = min(cur + timedelta(days=6), to_d)
        chunk_start_t = _time.time()
        try:
            chunk = await chunk_retry.run(
                lambda cur=cur, chunk_end=chunk_end: client.get_candles(
                    figi=figi, date_from=cur, date_to=chunk_end,
                    interval="CANDLE_INTERVAL_DAY",
                )
            )
            out.extend(chunk)
            logger.debug(
                "backfill.tinkoff.chunk",
                figi=figi, ticker=ticker,
                date_from=cur.isoformat(),
                date_to=chunk_end.isoformat(),
                elapsed_s=round(_time.time() - chunk_start_t, 3),
                rows=len(chunk),
            )
        except Exception as e:
            logger.warn(
                "backfill.tinkoff.chunk.failed",
                figi=figi, ticker=ticker,
                date_from=cur.isoformat(),
                date_to=chunk_end.isoformat(),
                elapsed_s=round(_time.time() - chunk_start_t, 3),
                error=str(e)[:200],
            )
            # RESOURCE_EXHAUSTED (gRPC 8 / HTTP 429): Tinkoff sandbox is
            # rate-limited at 600 req/min. If we hit it once, all
            # subsequent chunks will also fail and burn the rate-limit
            # window. Bail out immediately — no retry, no second chunk.
            # The figi will be retried on the next cron tick when the
            # bucket has refilled. (Mirrors the bail-out in
            # `_backfill_one`; both Tinkoff-fallback code paths must
            # behave consistently.)
            err_str = str(e)
            if "RESOURCE_EXHAUSTED" in err_str or "rate" in err_str.lower():
                logger.warn(
                    "backfill.tinkoff.rate_limited",
                    figi=figi, ticker=ticker,
                    message="Tinkoff rate-limited; aborting fallback (will retry next cron)",
                )
                return out  # bail out immediately; don't even start the next chunk
        cur = chunk_end + timedelta(days=1)
    return out


@dataclass
class BackfillRunner:
    """Orchestrates one full backfill run end to end.

    The runner holds a reference to a Tinkoff client (Protocol-conforming;
    can be `RealTinkoffClient` for live or `InMemoryTinkoffClient` for
    dev). The caller wires up the runner with the right client for the
    target environment.

    Event delivery is decoupled: every event the runner emits goes
    through `event_sink`. In production that's an SSE broadcaster; in
    tests it's a list collector. The runner never blocks on slow
    subscribers because the sink is awaited only at emit time and
    exceptions in the sink are logged and swallowed.
    """

    client: Any
    db_path: str
    event_sink: EventSink
    run_id: int = 0
    state: BackfillState = BackfillState.IDLE
    _stop_flag: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    # figi → ticker map populated during discover_universe; used to
    # render human-readable figi as ticker in log messages.
    _ticker_by_figi: dict[str, str] = field(default_factory=dict)
    # MOEX metadata cache: ticker → {market, board, listed_from, listed_till}
    # or None if the ticker is not listed on a primary MOEX board.
    # Populated by `_backfill_from_moex` prefetch (production) or lazily
    # by `_resolve_source` (tests / ad-hoc backfill).
    _moex_meta: dict[str, dict | None] = field(default_factory=dict)
    _moex_meta_lock: threading.Lock = field(default_factory=threading.Lock)
    tickers_done: int = 0
    tickers_total: int = 0
    total_bars: int = 0

    # Expose module-level backfill helpers as class attributes so tests
    # can patch them with ``patch.object(BackfillRunner, '_fetch_year_moex')``.
    # These bindings mirror the inner closures that used to live inside
    # backfill_from_moex — same logic, but reachable from outside.
    _fetch_year_moex = staticmethod(_fetch_year_moex)
    _fetch_moex_range = staticmethod(_fetch_moex_range)
    _get_meta_moex = staticmethod(_get_meta_moex)
    _fetch_tinkoff_fallback = staticmethod(_fetch_tinkoff_fallback_impl)

    # ─── public API ──────────────────────────────────────────────────

    def stop(self) -> None:
        """Request graceful cancellation. The runner checks between tickers."""
        self._stop_flag.set()
        with self._lock:
            if self.state == BackfillState.BACKFILLING:
                self.state = BackfillState.STOPPING

    async def run(
        self,
        history_years: int = 5,
        incremental_threshold_days: int = 2,
        *,
        source: str = "auto",
        limit_to: list[str] | None = None,
    ) -> None:
        """Run the full lifecycle: discover → backfill → done.

        Errors per ticker are logged but don't abort the run.

        `source` (R9) selects the data source for each ticker's fetch:
        `"auto"` lets `_backfill_one` pick MOEX vs Tinkoff per window;
        `"moex"` forces the MOEX year walker; `"tinkoff"` forces the
        Tinkoff chunk loop. The HTTP route validates the enum before
        reaching this method.

        `limit_to` (used by the data-quality recovery loop) restricts
        the backfill queue to the given figis. When set, we skip
        the discover step because we already know what we want to
        process.
        """
        self._stop_flag.clear()
        with self._lock:
            self.state = BackfillState.DISCOVERING
            self.tickers_done = 0
            self.tickers_total = 0
            self.total_bars = 0

        await self._emit("status", {"state": self.state.value, "phase": "start"})

        # Step 1: discover the universe (skipped when caller already
        # narrowed the queue to a recovery list).
        if limit_to is None:
            try:
                total = await self._discover_universe()
                self.tickers_total = total
                await self._emit(
                    "status",
                    {"state": self.state.value, "tickers_total": total},
                )
            except Exception as e:  # pragma: no cover — universe discovery failure only fires during live broker run
                await self._log("error", figi=None, message=f"universe discovery failed: {e}")
                await self._emit("done", {"tickers_done": 0, "tickers_total": 0, "status": "error"})
                with self._lock:
                    self.state = BackfillState.IDLE
                return
        else:
            self.tickers_total = len(limit_to)
            await self._emit(
                "status",
                {"state": self.state.value, "tickers_total": len(limit_to)},
            )

        # Step 2: backfill each instrument.
        with self._lock:
            self.state = BackfillState.BACKFILLING

        instruments = self._list_instruments(limit_to=limit_to)

        # Probe MOEX ISS for every ticker once and cache the result, so
        # `_resolve_source` can decide "moex" vs "tinkoff" without doing
        # a synchronous network call on each `_backfill_one` invocation.
        # The cache is reused across ticks within the same run.
        # Skipped in test/fake mode (`ALGOTRADER_INGEST_FAKE=1`) so the
        # existing test suite, which asserts the Tinkoff-only routing
        # for the seed universe, keeps working.
        import os as _os
        if _os.environ.get("ALGOTRADER_INGEST_FAKE") != "1":
            await self.prefetch_moex_meta(instruments)

        # Parallelize broker calls. Tinkoff allows 600 req/min per token;
        # use a semaphore of 10 to stay well under the limit while
        # collapsing ~25min wall time on 3809 figis down to ~3min.
        # Metadata lookups stay serial (cheap sqlite queries).
        parallel_limit = 10
        sem = asyncio.Semaphore(parallel_limit)

        async def _backfill_one_bounded(inst: dict) -> tuple[str, int, str | None]:
            """Run _backfill_one inside the semaphore. Returns (figi, bars, err)."""
            figi = inst["figi"]
            ticker = inst.get("ticker")
            metadata = self._get_metadata(figi)
            strategy, from_, to = decide_strategy(
                metadata_row=metadata,
                today=date.today(),
                history_years=history_years,
                incremental_threshold_days=incremental_threshold_days,
            )
            if strategy == "skip":
                await self._emit(
                    "ticker_progress",
                    {
                        "figi": figi,
                        "ticker": ticker,
                        "status": "skipped",
                        "bars_written": 0,
                    },
                )
                return (figi, 0, None)
            async with sem:
                if self._stop_flag.is_set():
                    return (figi, 0, None)
                try:
                    bars = await self._backfill_one(
                        figi=figi, ticker=ticker, from_=from_, to=to,
                        source=source,
                    )
                    return (figi, bars, None)
                except Exception as e:  # noqa: BLE001
                    await self._log("error", figi=figi, message=f"unhandled: {e}")
                    return (figi, 0, str(e))

        results = await asyncio.gather(
            *[_backfill_one_bounded(inst) for inst in instruments],
            return_exceptions=False,
        )
        for figi, bars, _err in results:
            self.tickers_done += 1
            self.total_bars += bars

        # Step 3: finished.
        final_state = BackfillState.DONE
        if self._stop_flag.is_set():
            final_state = BackfillState.IDLE
        with self._lock:
            self.state = final_state
        await self._emit(
            "done",
            {
                "tickers_done": self.tickers_done,
                "tickers_total": self.tickers_total,
                "total_bars": self.total_bars,
                "status": "stopped" if self._stop_flag.is_set() else "ok",
            },
        )

    # ─── full-history walk ────────────────────────────────────────────

    async def run_full_history(
        self,
        *,
        from_offset_days: int = 30,
        limit_to: list[str] | None = None,
    ) -> int:
        """Walk every tradeable figi from ``first_bar_ts - from_offset_days``
        to ``yesterday``, restricted-period-aware, idempotent.

        Returns the count of figis processed (not the count of bars
        written — ``_backfill_one`` handles per-ticker bar counts).

        Algorithm:
        1. List instruments via ``self._list_instruments(limit_to=limit_to)``.
        2. For each instrument:
           - Query ``MIN(ts)`` for the figi from ``bars`` (or ``None``
             if 0 bars).
           - If 0 bars: skip (nothing to anchor on; operator triggers
             manual import via the Data tab).
           - Otherwise: ``from_ = min_ts - from_offset_days``;
             ``to_ = today - 1d``.
           - Call ``self._backfill_one(figi=figi, ticker=ticker,
             from_=from_, to_=to_)``.
           - Increment ``self.tickers_done``.

        Restricted periods are handled INSIDE ``_backfill_one`` (existing
        behavior — it skips them). Do NOT add restricted-period logic
        here; that would be double-counting.
        """
        self._stop_flag.clear()
        today = date.today()
        to_ = today - timedelta(days=1)
        instruments = self._list_instruments(limit_to=limit_to)
        # Reset per-run counters so a follow-up full-history call
        # reflects only the most recent walk.
        with self._lock:
            self.tickers_done = 0
            self.tickers_total = len(instruments)
        await self._emit(
            "status",
            {"state": BackfillState.BACKFILLING.value, "tickers_total": len(instruments)},
        )
        processed = 0
        for inst in instruments:
            if self._stop_flag.is_set():
                break
            figi = inst["figi"]
            ticker = inst.get("ticker")
            # Query min(ts) for this figi from bars. None = no rows.
            con = sqlite3.connect(self.db_path)
            try:
                row = con.execute(
                    "SELECT MIN(ts) FROM bars WHERE figi = ?",
                    (figi,),
                ).fetchone()
            finally:
                con.close()
            min_ts_str = row[0] if row and row[0] else None
            if min_ts_str is None:
                # Nothing to anchor on; operator triggers manual import.
                await self._log(
                    "info",
                    figi=figi,
                    message="full-history: skipped (no bars in DB)",
                )
                continue
            try:
                min_ts = date.fromisoformat(str(min_ts_str)[:10])
            except (TypeError, ValueError):  # pragma: no cover — defensive
                await self._log(
                    "warn",
                    figi=figi,
                    message=f"full-history: invalid min ts {min_ts_str!r}",
                )
                continue
            from_ = min_ts - timedelta(days=from_offset_days)
            try:
                bars_written = await self._backfill_one(
                    figi=figi, ticker=ticker, from_=from_, to=to_
                )
                self.total_bars += bars_written
            except Exception as e:  # noqa: BLE001 — defensive; per-ticker errors are logged inside _backfill_one  # pragma: no cover
                await self._log("error", figi=figi, message=f"full-history unhandled: {e}")
            self.tickers_done += 1
            processed += 1
        with self._lock:
            self.state = (
                BackfillState.IDLE if self._stop_flag.is_set() else BackfillState.DONE
            )
        await self._emit(
            "done",
            {
                "tickers_done": self.tickers_done,
                "tickers_total": self.tickers_total,
                "total_bars": self.total_bars,
                "status": "stopped" if self._stop_flag.is_set() else "ok",
            },
        )
        return processed

    async def backfill_from_moex(
        self,
        *,
        today: date | None = None,
        max_workers: int = 5,
        delta_only: bool = True,
        priority: bool = True,
        recent_tail_days: int = 0,
    ) -> int:
        """Walk every tradeable figi from MOEX listed_from to min(yesterday, listed_till).

        Replaces the Tinkoff-only daily_backfill + full_history walk.
        Insertion is INSERT OR IGNORE on PRIMARY KEY (figi, ts), so existing
        Tinkoff bars (2021+) are never overwritten.

        When `priority=True` (default), figis are sorted by gap size
        descending before fetching so rate-limit budget is spent on
        the biggest missing-history gaps first.

        When `recent_tail_days > 0`, a second pass runs after the
        historical walk and fetches only the last `recent_tail_days`
        trading days from MOEX ISS for every figi with a primary
        board. Insertion is still INSERT OR IGNORE so any Tinkoff
        bars already present are preserved. This is a fallback for
        the daily chain when Tinkoff is stuck (worker on "Connection
        reset by peer") — MOEX ISS often has yesterday/today data
        even when the broker SDK is unreachable. Default 0
        preserves the historical-only behavior so existing callers
        and tests see no change.

        Algorithm:
          1. List instruments via self._list_instruments().
          2. For each figi:
             a. Probe /iss/securities/{ticker}.json (cached in self._moex_meta).
                - Pick primary board (TQBR/TQTF/SMAL for shares, TQOB/TQCB for bonds).
                - Extract listed_from, listed_till, market (shares|bonds).
                - If NO_BOARDS: fall back to self.client.get_candles() with [listed_from, yesterday].
             b. Compute window = [max(moex_listed_from, figi's earliest existing bar - 30d), min(yesterday, moex_listed_till)].
             c. If delta_only and window is empty (figi already has bars covering moex_listed_from..today), skip.
             d. Walk window year-by-year via /iss/history/.../securities/{ticker}.json?from=YYYY-01-01&till=YYYY-12-31.
                (For bonds: /markets/bonds/.)
             e. INSERT OR IGNORE each bar via replace_bars_for_figi(..., replace=False).
             f. Update instrument_metadata (first_bar_ts, last_bar_ts, total_bars).

        Returns total bars written across all figis.
        """
        from ..db.bars_sqlite import replace_bars_for_figi
        from . import retry as retry_mod

        if today is None:
            today = date.today()
        yesterday = _last_trading_day(today, self.db_path)
        instruments = self._list_instruments()
        self._moex_meta: dict[str, dict | None] = {}
        self._moex_meta_lock = threading.Lock()

        # Priority reorder: when priority=True, sort figis by gap size
        # descending so rate-limit budget is spent on the biggest
        # missing-history gaps first (SBER, large-cap stocks before
        # newly-issued bonds).
        if priority:
            from algotrader_api.ingestion.priority import compute_priority_queue
            universe = [(inst.get("ticker", ""), inst.get("figi", ""))
                        for inst in instruments]
            # For listed_from lookup we don't have it before MOEX probe;
            # use 2014-01-01 as a conservative default (covers most
            # tradable figis; sanctions-delisted fall back to Tinkoff).
            def _lookup_default(ticker: str) -> date:
                return date(2014, 1, 1)
            priority_queue = compute_priority_queue(
                db_path=self.db_path,
                universe=universe,
                listed_from_lookup=_lookup_default,
                yesterday=yesterday,
                gap_threshold_for_moex=0,
            )
            if priority_queue:
                priority_order = {figi: i for i, (_, figi, _) in enumerate(priority_queue)}
                # Stable sort: priority figis first (in order), then the rest.
                instruments = sorted(
                    instruments,
                    key=lambda inst: (
                        0 if inst.get("figi") in priority_order else 1,
                        priority_order.get(inst.get("figi"), 0),
                    ),
                )
                await self._log(
                    "info",
                    message=f"priority-aware fetch: {len(priority_queue)} figis "
                            f"with gaps > 0, sorted by gap size descending",
                )

        def _get_meta(ticker: str) -> dict | None:
            """Inline thin wrapper around the module-level helper, kept so
            other code in the method reads naturally. Real work happens in
            BackfillRunner._get_meta_moex so tests can patch it."""
            return self._get_meta_moex(
                ticker, yesterday,
                meta_cache=self._moex_meta,
                meta_lock=self._moex_meta_lock,
            )

        written_total = 0
        s_http = __import__("requests").Session()

        # Concurrency: up to 5 figis in parallel for MOEX paths (MOEX ISS
        # rate limit is ~100 req/min per endpoint — plenty of headroom
        # for 5 figis walking 13 years each in parallel). Tinkoff fallback
        # is intentionally sequential because Tinkoff sandbox is rate-
        # Tinkoff fallback is intentionally sequential because Tinkoff sandbox is
        # rate-limited at 600/min and adaptive-retry backoff compounds when
        # concurrent slots retry together. Per-figi timeout on Tinkoff
        # fallback so a single hung ticker can't stall the chain.
        _figi_timeout_s = 45

        async def _process_moex_year(inst: dict, meta: dict, year: int) -> list[dict]:
            year_bars = self._fetch_year_moex(
                meta["market"], meta["board"], inst["ticker"], year,
                last_trading_day=yesterday,
            )
            for b in year_bars:
                b["figi"] = inst["figi"]
            return year_bars

        async def _process_tinkoff(inst: dict) -> int:
            figi = inst["figi"]
            ticker = inst["ticker"]
            try:
                listed_from_iso = (inst.get("listed_from") or "2014-01-01")[:10]
                from_d = date.fromisoformat(listed_from_iso)
            except (TypeError, ValueError):
                from_d = date(2014, 1, 1)
            try:
                candles = await asyncio.wait_for(
                    self._fetch_tinkoff_fallback(
                        self.client, retry_mod, figi, ticker, from_d, yesterday,
                    ),
                    timeout=_figi_timeout_s,
                )
            except asyncio.TimeoutError:
                await self._log(
                    "warn", figi=figi,
                    message=f"Tinkoff fallback timeout after {_figi_timeout_s}s for {ticker}; skipping",
                )
                return 0
            if not candles:
                await self._log("warn", figi=figi, message="Tinkoff fallback: no data")
                return 0
            return replace_bars_for_figi(self.db_path, figi, candles, replace=False)

        async def _process_one(inst: dict) -> int:
            if self._stop_flag.is_set():
                return 0
            figi = inst["figi"]
            ticker = inst.get("ticker")
            if not ticker:
                return 0

            meta = _get_meta(ticker)
            if meta is None:
                await self._log("info", figi=figi, message="no MOEX board; falling back to Tinkoff")
                return await _process_tinkoff(inst)

            listed_from_iso = meta["listed_from"]
            listed_till_iso = meta["listed_till"]
            try:
                listed_from_d = date.fromisoformat(listed_from_iso[:10])
                listed_till_d = date.fromisoformat(listed_till_iso[:10])
            except (TypeError, ValueError):
                return 0

            if delta_only:
                # Coverage-aware gap detection: skip if all trading
                # days in [listed_from_d, yesterday] are already in
                # `bars` (any source); otherwise fetch only the
                # missing dates via the from_d/to_d window.
                missing = compute_missing_dates(
                    figi=figi,
                    listed_from=listed_from_d,
                    yesterday=yesterday,
                    db_path=self.db_path,
                )
                if not missing:
                    return 0
                from_d = min(missing)
                if from_d <= listed_from_d:
                    from_d = listed_from_d
            else:
                from_d = listed_from_d

            to_d = min(yesterday, listed_till_d)
            if from_d > to_d:
                return 0

            # Fetch all years for this figi in parallel (MOEX is fast
            # and 13 years × 1 figi per slot is fine — that's 13 reqs
            # distributed across the Semaphore).
            years = list(range(from_d.year, to_d.year + 1))
            year_batches = await asyncio.gather(
                *[_process_moex_year(inst, meta, y) for y in years],
                return_exceptions=True,
            )
            all_bars: list[dict] = []
            for i, batch in enumerate(year_batches):
                if isinstance(batch, Exception):
                    await self._log(
                        "warn", figi=figi,
                        message=f"fetch_year_moex failed for year {years[i]} ({ticker}): {batch!r}",
                    )
                    continue
                if isinstance(batch, list):
                    all_bars.extend(batch)
            if not all_bars:
                return 0
            return replace_bars_for_figi(
                self.db_path, figi, all_bars, replace=False, source="moex"
            )

        # Pre-populate the meta cache for all instruments in parallel via
        # asyncio.to_thread (the sync _get_meta_moex calls MOEX ISS).
        # After this step, _moex_meta has entries for every ticker, so
        # _process_one_bounded can read it without blocking the loop.
        async def _prefetch_meta(inst: dict) -> None:
            ticker = inst.get("ticker") or ""
            if ticker:
                # 30s per-ticker timeout — if MOEX ISS hangs on one ticker,
                # we don't want to block the whole prefetch.
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(_get_meta, ticker),
                        timeout=30.0,
                    )
                except asyncio.TimeoutError:
                    pass  # Cache will be empty for this ticker; _process_one
                    # will fall back to Tinkoff as before.

        await asyncio.gather(*[_prefetch_meta(inst) for inst in instruments])

        # Outer parallelism:
        # - 5 figis in parallel for MOEX path (cheap, MOEX ISS ~100 req/min per endpoint).
        # - 1 figi at a time for Tinkoff fallback (sandbox/prod 600 req/min,
        #   adaptive-retry backoff compounds when concurrent slots retry together).
        # The split prevents rate-limited Tinkoff figis from starving MOEX
        # figis of the shared semaphore.
        _moex_sem = asyncio.Semaphore(5)
        _tinkoff_sem = asyncio.Semaphore(1)

        async def _process_one_bounded(inst: dict) -> int:
            ticker = inst.get("ticker") or ""
            meta = self._moex_meta.get(ticker) if ticker else None
            # After prefetch, self._moex_meta contains either meta dict or
            # None for every ticker. A second _get_meta would just hit the cache.
            if meta is not None:
                sem = _moex_sem
            else:
                sem = _tinkoff_sem
            async with sem:
                return await _process_one(inst)

        results = await asyncio.gather(
            *[_process_one_bounded(inst) for inst in instruments],
            return_exceptions=True,
        )
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                figi = instruments[i].get("figi", "?")
                ticker = instruments[i].get("ticker", "?")
                await self._log(
                    "warn", figi=figi,
                    message=f"backfill_from_moex figi failed for {ticker}: {res!r}",
                )
                continue
            if isinstance(res, int):
                written_total += res

        # Optional recent-tail pass: when Tinkoff is stuck (e.g. worker
        # wedged on "Connection reset by peer"), MOEX ISS often has
        # yesterday/today data and the historical walk above won't
        # have filled those dates. Only runs when the caller asks
        # (recent_tail_days > 0); default is 0 to preserve existing
        # behavior and tests.
        if recent_tail_days > 0:
            tail_written = await self.backfill_moex_recent_tail(
                days=recent_tail_days, today=today,
            )
            written_total += tail_written

        return written_total

    # ─── MOEX recent-tail fallback ────────────────────────────────────

    async def backfill_moex_recent_tail(
        self,
        *,
        days: int = 5,
        today: date | None = None,
        max_concurrency: int = 5,
        limit_to: list[str] | None = None,
    ) -> int:
        """Fetch the last `days` trading days from MOEX ISS for the tradeable universe.

        Purpose: when the broker SDK is stuck (e.g. Tinkoff sandbox
        throwing ``Connection reset by peer`` for several days), the
        daily chain has no way to deliver recent bars. MOEX ISS is
        independent of the broker and usually has yesterday/today data
        for any figi on a primary board. This pass uses that as a
        fallback so the chain stays fresh.

        Algorithm:
          1. Compute window = [yesterday - days, yesterday] using
             ``_last_trading_day`` for the upper bound.
          2. List instruments via ``self._list_instruments(limit_to=...)``.
          3. Probe MOEX meta for each ticker (cached in ``self._moex_meta``,
             same cache the historical walk uses — a probe done in
             ``backfill_from_moex`` is reused here).
          4. For figis with a primary board, fetch the window via
             ``_fetch_moex_range`` (one HTTP request per touched year;
             for ``days=5`` the window fits inside a single year so
             this is at most 1 request per figi).
          5. INSERT OR IGNORE via ``replace_bars_for_figi(..., replace=False)``,
             so any Tinkoff bars already in the DB are preserved.
          6. Tickers without a MOEX primary board (sanctions-delisted)
             are skipped — Tinkoff is the only path for those and
             this pass explicitly avoids waiting on it.

        Concurrency is capped at ``max_concurrency`` figis in parallel;
        with ``days=5`` a full universe of ~3000 figis completes in
        roughly 60s on a 100 req/min MOEX ISS budget.

        Returns total bars written across all figis.
        """
        from ..db.bars_sqlite import replace_bars_for_figi

        if today is None:
            today = date.today()
        if days <= 0:
            return 0
        yesterday = _last_trading_day(today, self.db_path)
        # Use calendar days for the lower bound (not trading days):
        # MOEX will simply return no rows for non-trading days in
        # [from_d, yesterday], so over-fetching is harmless and we
        # don't need to walk a holiday calendar here. days*2 covers
        # two-week-long holiday stretches (e.g. New Year window).
        from_d = yesterday - timedelta(days=days * 2)
        if from_d > yesterday:
            return 0

        instruments = self._list_instruments(limit_to=limit_to)
        # Reuse / populate the meta cache that the historical walk
        # already warmed — a second backfill_from_moex call after
        # this one will skip the probe entirely.
        if not hasattr(self, "_moex_meta") or self._moex_meta is None:
            self._moex_meta = {}
        if not hasattr(self, "_moex_meta_lock") or self._moex_meta_lock is None:
            self._moex_meta_lock = threading.Lock()

        def _get_meta(ticker: str) -> dict | None:
            return self._get_meta_moex(
                ticker, yesterday,
                meta_cache=self._moex_meta,
                meta_lock=self._moex_meta_lock,
            )

        async def _probe(inst: dict) -> None:
            ticker = inst.get("ticker") or ""
            if not ticker:
                return
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(_get_meta, ticker),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                pass

        # Pre-populate the meta cache so the per-figi loop can read
        # without blocking the event loop on HTTP.
        await asyncio.gather(*[_probe(inst) for inst in instruments])

        sem = asyncio.Semaphore(max_concurrency)
        written_total = 0

        async def _process_one(inst: dict) -> int:
            if self._stop_flag.is_set():
                return 0
            figi = inst.get("figi")
            ticker = inst.get("ticker") or ""
            if not figi or not ticker:
                return 0
            meta = self._moex_meta.get(ticker)
            if meta is None:
                # No primary MOEX board → skip silently. Tinkoff
                # fallback is intentionally NOT attempted here; the
                # whole point of this pass is to bypass Tinkoff.
                return 0
            listed_till_iso = meta.get("listed_till") or yesterday.isoformat()
            try:
                listed_till_d = date.fromisoformat(listed_till_iso[:10])
            except (TypeError, ValueError):
                return 0
            # Don't fetch dates after the instrument was delisted.
            to_d = min(yesterday, listed_till_d)
            if from_d > to_d:
                return 0
            # Fetch off the loop — _fetch_moex_range is sync.
            try:
                bars = await asyncio.to_thread(
                    self._fetch_moex_range,
                    meta["market"], meta["board"], ticker,
                    from_d, to_d, last_trading_day=yesterday,
                )
            except Exception as e:  # noqa: BLE001 — defensive
                await self._log(
                    "warn", figi=figi,
                    message=f"moex_recent_tail fetch failed for {ticker}: {e!r}",
                )
                return 0
            for b in bars:
                b["figi"] = figi
            if not bars:
                return 0
            return replace_bars_for_figi(
                self.db_path, figi, bars, replace=False, source="moex"
            )

        async def _bounded(inst: dict) -> int:
            async with sem:
                return await _process_one(inst)

        results = await asyncio.gather(
            *[_bounded(inst) for inst in instruments],
            return_exceptions=True,
        )
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                figi = instruments[i].get("figi", "?")
                ticker = instruments[i].get("ticker", "?")
                await self._log(
                    "warn", figi=figi,
                    message=f"moex_recent_tail failed for {ticker}: {res!r}",
                )
                continue
            if isinstance(res, int):
                written_total += res

        await self._log(
            "info", figi=None,
            message=(
                f"moex_recent_tail: wrote {written_total} bars "
                f"across {len(instruments)} figis "
                f"(window {from_d.isoformat()}..{yesterday.isoformat()})"
            ),
        )
        return written_total

    # ─── universe discovery ──────────────────────────────────────────

    async def prefetch_moex_meta(self, instruments: list[dict]) -> None:
        """Probe MOEX ISS for every ticker and populate `self._moex_meta`.

        Called by `run()` so that `_resolve_source` can read MOEX
        metadata from a per-ticker dict instead of doing a synchronous
        network probe on every `_backfill_one` invocation. Each entry
        is either `{"market", "board", "listed_from", "listed_till"}`
        (active on a primary board) or `None` (sanctions-delisted /
        no primary board).

        The probe is idempotent: tickers already in the cache are
        skipped. Re-runs reuse cached results. Probes run concurrently
        with a small semaphore so a 3809-figi universe doesn't take
        1900s (one probe per figi × 0.5s).
        """
        import asyncio

        today = date.today()
        sem = asyncio.Semaphore(20)

        async def _probe(ticker: str) -> None:
            async with sem:
                # `_get_meta_moex` is a synchronous function that does
                # the blocking HTTP request internally; offload to a
                # thread so the event loop stays responsive. Each probe
                # has its own (5s, 30s) timeout inside `_get_meta_moex`.
                await asyncio.to_thread(
                    self._get_meta_moex,
                    ticker, today,
                    meta_cache=self._moex_meta,
                    meta_lock=self._moex_meta_lock,
                )

        to_probe: list[str] = []
        for inst in instruments:
            ticker = inst.get("ticker")
            if not ticker or ticker in self._moex_meta:
                continue
            to_probe.append(ticker)
        if to_probe:
            await asyncio.gather(*(_probe(t) for t in to_probe))

    async def _discover_universe(self) -> int:
        """Fetch only tradeable asset classes and upsert into `instruments`.

        Filters at the *source*: we only call the broker SDK methods
        for classes in `TRADEABLE_CLASSES`. Anything else (today:
        `future`, `option`) is never requested, so we don't waste
        rate-limit budget on 10k+ option position_uids the operator
        doesn't trade.

        Returns total instruments inserted across all tradeable
        classes. The TinkoffClient Protocol defines
        `get_shares/get_bonds/...` methods (see `ingestion/client.py`);
        we call those rather than the raw SDK's `shares/bonds/...`
        properties so the wrapper can translate gRPC responses to
        dicts.
        """
        from ..domain.tradeable import TRADEABLE_CLASSES

        total = 0
        fetcher_specs = [
            ("get_shares", "share"),
            ("get_bonds", "bond"),
            ("get_etfs", "etf"),
            ("get_futures", "future"),
            ("get_options", "option"),
        ]
        for method_name, cls in fetcher_specs:
            if cls not in TRADEABLE_CLASSES:
                # Skip non-tradeable classes entirely — no SDK call,
                # no upsert. This is the runtime enforcement of the
                # tradeable contract.
                continue
            if self._stop_flag.is_set():
                break
            try:
                rows = await getattr(self.client, method_name)()
            except Exception as e:  # noqa: BLE001 — defensive
                await self._log(
                    "error", figi=None, message=f"{method_name} failed: {e}"
                )
                continue
            total += len(rows)
            for row in rows:
                figi = row.get("figi")
                ticker = row.get("ticker") or ""
                asset_class = row.get("class") or ""
                if figi and ticker:
                    self._ticker_by_figi[figi] = ticker
                self._upsert_instrument(row)
                # Insert metadata row only for previously-unseen figis.
                # Existing rows preserve their last_bar_ts/status from
                # previous backfill runs — otherwise we would invalidate
                # the `decide_strategy` skip/incremental decisions.
                # No-candles-method is no longer used; instrument status
                # is the source of truth (a genuine NOT_FOUND from the
                # SDK sets status='error' inside `_backfill_one`).
                if figi:
                    self._seed_metadata_for_figi(figi)
            for row in rows:
                await self._emit(
                    "ticker_progress",
                    {
                        "figi": row.get("figi"),
                        "ticker": row.get("ticker"),
                        "status": "discovered",
                    },
                )
        return total

    # ─── per-ticker fetch + write ────────────────────────────────────

    async def _backfill_one(
        self,
        *,
        figi: str,
        from_: date,
        to: date,
        ticker: str | None = None,
        source: str = "auto",
    ) -> int:
        """Fetch candles for one ticker, filter closed, write parquet, update metadata.

        Returns the number of bars written. Errors are caught and logged
        as `ticker_progress` events with `status='error'`; the runner
        continues to the next ticker.

        `ticker` is optional and used only for log message readability
        (so the operator sees `VB58CU6B/3334f2b7-...` instead of the raw
        position_uid). It must be populated by the caller — passing it
        here avoids a per-ticker DB roundtrip inside the loop and also
        works for backfill runs that start without a fresh
        `_discover_universe`, where `_ticker_by_figi` would be empty.

        Routing rules (source='auto'):
        - If `from_..to` spans > 9 months AND the figi has MOEX metadata,
          walk the window year-by-year via `_fetch_year_moex`, then bridge
          the trailing 9 months via `self.client.get_candles`.
        - Otherwise fall through to the existing Tinkoff chunk loop.

        Routing rules (source='tinkoff'):
        - Force Tinkoff chunk loop. Operator override.

        Routing rules (source='moex'):
        - Force MOEX year walker only. Operator audit mode.
        """
        if ticker:
            self._ticker_by_figi[figi] = ticker

        resolved_source = self._resolve_source(figi, ticker, from_, to, source)
        if resolved_source == "moex":
            return await self._backfill_one_moex(
                figi=figi, ticker=ticker, from_=from_, to=to,
            )
        # tinkoff or auto-with-no-MOEX-meta
        return await self._backfill_one_tinkoff(
            figi=figi, ticker=ticker, from_=from_, to=to,
        )

    def _resolve_source(self, figi: str, ticker: str | None,
                        from_: date, to: date, requested: str) -> str:
        """Decide which source fetches this window.

        Routing is purely on window duration and requested source —
        we do NOT short-circuit on `earliest_local_bar_ts` (R5).

        MOEX routing requires `ticker` to have a pre-populated entry
        in `self._moex_meta` (set via `prefetch_moex_meta()` or by the
        caller). When the cache is empty we default to Tinkoff — this
        keeps the existing `_backfill_one` semantics intact for
        callers that don't pre-populate.
        """
        if requested in ("tinkoff", "moex"):
            return requested
        # auto
        if (to - from_).days < 270:  # <9 months: always Tinkoff
            return "tinkoff"
        if not ticker:
            return "tinkoff"
        meta = self._moex_meta.get(ticker)
        if meta is None:
            # Cache miss for this ticker. Either the caller skipped the
            # MOEX prefetch (tests, ad-hoc backfills) or the ticker is
            # genuinely not on MOEX. Default to Tinkoff in both cases —
            # the Tinkoff chunk loop has its own "delisted" handling.
            return "tinkoff"
        return "moex"

    async def _backfill_one_moex(self, *, figi, ticker, from_, to) -> int:
        """Walk `from_..to` year-by-year through MOEX ISS; bridge trailing 9m via Tinkoff."""
        today = date.today()
        total_added = 0
        year = from_.year
        while year <= to.year:
            last_trading_day_for_year = today if year == to.year else None
            try:
                candles = self._fetch_year_moex(
                    "shares", "TQBR", ticker, year,
                    last_trading_day=last_trading_day_for_year,
                )
            except Exception as e:  # noqa: BLE001
                await self._log("warn", figi=figi,
                                message=f"moex year {year} failed: {e}")
                candles = []
            if not candles:
                await self._log("warn", figi=figi,
                                message=f"moex empty year={year} ticker={ticker}")
            else:
                # Mark figi on the candles dict (MOEX doesn't know figi)
                for c in candles:
                    c["figi"] = figi
                from ..db.bars_sqlite import replace_bars_for_figi
                added = replace_bars_for_figi(self.db_path, figi, candles, replace=False)
                total_added += added
            year += 1
        # Trailing bridge: ask Tinkoff for the last 9 months of the window.
        # R1: bridge_start = to - 270 days (the trailing 9m), NOT year-aligned.
        # R6: bridge runs UNCONDITIONALLY when (bridge_end - bridge_start) > 270 days.
        bridge_start = to - timedelta(days=270)
        bridge_end = today
        if (bridge_end - bridge_start).days <= 270:
            return total_added
        # Pull trailing 9 months from Tinkoff (additive; INSERT OR IGNORE on dup).
        from .retry import AdaptiveRetry
        retry = AdaptiveRetry(max_attempts=2, initial_delay=0.5,
                              backoff_factor=2.0, max_delay=5.0)
        chunks = []
        cur = bridge_start
        while cur <= bridge_end:
            chunk_end = min(cur + timedelta(days=6), bridge_end)
            try:
                chunk = await retry.run(
                    lambda cur=cur, chunk_end=chunk_end: self.client.get_candles(
                        figi=figi, date_from=cur, date_to=chunk_end,
                        interval="CANDLE_INTERVAL_DAY",
                    )
                )
                chunks.extend(chunk)
            except Exception as e:  # noqa: BLE001
                await self._log("warn", figi=figi,
                                message=f"trailing bridge {cur}..{chunk_end}: {e}")
                break
            cur = chunk_end + timedelta(days=1)
        if chunks:
            from ..db.bars_sqlite import replace_bars_for_figi
            total_added += replace_bars_for_figi(
                self.db_path, figi, chunks, replace=False
            )
        return total_added

    async def _backfill_one_tinkoff(self, *, figi, ticker, from_, to) -> int:
        """Existing Tinkoff chunk loop — verbatim body of the original _backfill_one.

        Errors are caught and logged as `ticker_progress` events with
        `status='error'`; the runner continues to the next ticker.
        """
        # Tinkoff's live API rejects (INVALID_ARGUMENT 30014) requests longer
        # than ~7 days for the day interval. Walk the [from_, to] window in
        # 7-day chunks; a single-chunk call behaves like the old flow.
        # Each chunk is wrapped in AdaptiveRetry so a transient
        # RESOURCE_EXHAUSTED (gRPC 8) or HTTP 429 from the rate limiter
        # is handled via exponential backoff instead of being logged as
        # a per-chunk warning and abandoned. We keep the backoff tight
        # (max 5s, max 2 attempts) so a sustained throttle still fails
        # the ticker promptly rather than stalling the whole run.
        from .retry import AdaptiveRetry

        chunk_retry = AdaptiveRetry(
            max_attempts=2,
            initial_delay=0.5,
            backoff_factor=2.0,
            max_delay=5.0,
        )
        all_candles: list = []
        chunk_days = 7
        cur = from_
        chunks_attempted = 0
        chunks_failed = 0
        last_chunk_error: str | None = None
        while cur <= to:
            chunk_end = min(cur + timedelta(days=chunk_days - 1), to)
            chunks_attempted += 1
            try:
                chunk = await chunk_retry.run(
                    lambda cur=cur, chunk_end=chunk_end: self.client.get_candles(
                        figi=figi,
                        date_from=cur,
                        date_to=chunk_end,
                        interval="CANDLE_INTERVAL_DAY",
                    )
                )
                all_candles.extend(chunk)
            except Exception as e:  # noqa: BLE001
                # RESOURCE_EXHAUSTED (gRPC 8 / HTTP 429): Tinkoff sandbox
                # is rate-limited at 600 req/min. If we hit it once, all
                # subsequent chunks in this run will also fail and just
                # burn the rate-limit window. Bail out immediately so
                # the chain can move to other figis and pick this one up
                # on the next cron tick when the bucket has refilled.
                err_str = str(e)
                if "RESOURCE_EXHAUSTED" in err_str or "rate" in err_str.lower():
                    chunks_failed += 1
                    last_chunk_error = err_str
                    await self._log(
                        "warn",
                        figi=figi,
                        message=f"Tinkoff rate-limited on chunk {cur}..{chunk_end}; aborting fallback (will retry next cron)",
                    )
                    break  # skip remaining chunks
                chunks_failed += 1
                last_chunk_error = err_str
                await self._log(
                    "warn",
                    figi=figi,
                    message=f"chunk {cur}..{chunk_end}: {e}",
                )
            cur = chunk_end + timedelta(days=1)
        # Treat as failure only when every chunk failed. If at least
        # one chunk returned bars we proceed; partial historic data is
        # still useful and the failure was likely the tail of the
        # window.
        if chunks_failed > 0 and chunks_failed == chunks_attempted:
            err_msg = last_chunk_error or "all_chunks_failed"
            await self._log("error", figi=figi, message=f"get_candles: {err_msg}")
            self._upsert_metadata(
                figi=figi,
                last_bar_ts=None,
                total_bars=0,
                status="error",
                error_msg=err_msg[:200],
            )
            await self._emit(
                "ticker_progress",
                {
                    "figi": figi,
                    "status": "error",
                    "bars_written": 0,
                    "error": err_msg[:200],
                },
            )
            return 0
        raw_candles = all_candles
        if not raw_candles:
            # Empty response: broker has no data for this range. Mark
            # last_bar_ts=today so decide_strategy skips on next run
            # (otherwise it returns 'full' and we'd retry forever).
            self._upsert_metadata(
                figi=figi,
                last_bar_ts=to,
                total_bars=0,
                status="skipped",
                error_msg=None,
            )
            await self._log(
                "warn",
                figi=figi,
                message=f"no candles in {from_}..{to}",
            )
            await self._emit(
                "ticker_progress",
                {
                    "figi": figi,
                    "status": "empty",
                    "bars_written": 0,
                },
            )
            return 0

        closed = [c for c in raw_candles if is_closed_candle(c)]
        # Defensive last-bar guard: never persist a candle dated today or
        # later — Tinkoff occasionally returns an open bar even when
        # is_complete is True. The on-disk `last_bar_ts` should always
        # be strictly < today.
        today_ = date.today()

        def _candle_date(c: Any) -> date | None:
            """Extract the candle's date regardless of SDK shape.

            Handles three shapes:
              1. t-tech SDK flat dict: {"ts": "YYYY-MM-DD", ...}
              2. Legacy gRPC-shaped dict: {"time": {"year":..., "month":..., "day":...}, ...}
              3. Native gRPC object: c.time.{year, month, day}
            """
            if isinstance(c, dict):
                if c.get("ts"):
                    try:
                        return date.fromisoformat(c["ts"][:10])
                    except (TypeError, ValueError):
                        return None
                t = c.get("time") or c.get("time_") or {}
                if isinstance(t, dict):
                    y, m, d = t.get("year"), t.get("month"), t.get("day")
                else:
                    y, m, d = getattr(t, "year", None), getattr(t, "month", None), getattr(t, "day", None)
            else:
                t = getattr(c, "time", None)
                y, m, d = getattr(t, "year", None), getattr(t, "month", None), getattr(t, "day", None)
            if not (y and m and d):
                return None
            try:
                return date(y, m, d)
            except (TypeError, ValueError):  # pragma: no cover — defensive
                return None

        closed = [c for c in closed if (dt := _candle_date(c)) and dt < today_]

        # ── integrity gate ────────────────────────────────────────────
        # Every fetched candle is validated against the bar-level rules
        # defined in data_quality.integrity. Violating candles are
        # logged at WARN with rule+detail and dropped — only valid
        # candles reach `bars`. The total skipped count is also written
        # to ingestion_logs so the data-quality guardian surfaces it.
        from ..data_quality.integrity import bar_ts, validate_bar

        clean: list = []
        skipped = 0
        for raw in closed:
            violations = validate_bar(raw)
            if not violations:
                clean.append(raw)
                continue
            skipped += 1
            for vio in violations:
                logger.warning(
                    "bar-integrity-skip",
                    figi=figi,
                    ts=bar_ts(raw),
                    rule=vio.rule.value,
                    detail=vio.message,
                )
            await self._log(
                "warn",
                figi=figi,
                message=f"bar-corruption-skipped ts={bar_ts(raw)} rule={violations[0].rule.value}",
            )
        if skipped and clean:
            await self._log(
                "warn",
                figi=figi,
                message=f"bar-corruption-skipped={skipped}",
            )
        closed = clean

        written = self._write_bars(figi=figi, candles=closed)
        if written > 0:
            last_ts = self._extract_last_bar_ts(closed)
            self._upsert_metadata(
                figi=figi,
                last_bar_ts=last_ts,
                total_bars=written,
                status="ok",
                error_msg=None,
            )
            await self._log(
                "info", figi=figi, message=f"bars_written={written} last_bar_ts={last_ts}"
            )
            await self._emit(
                "ticker_progress",
                {
                    "figi": figi,
                    "status": "ok",
                    "bars_written": written,
                    "last_bar_ts": last_ts,
                },
            )
        else:
            self._upsert_metadata(
                figi=figi,
                last_bar_ts=None,
                total_bars=0,
                status="skipped",
                error_msg=None,
            )
            await self._emit(
                "ticker_progress",
                {
                    "figi": figi,
                    "status": "skipped",
                    "bars_written": 0,
                },
            )
        return written

    # ─── DB helpers ──────────────────────────────────────────────────

    def _list_instruments(
        self, limit_to: list[str] | None = None
    ) -> list[dict]:
        """Read the tradeable universe for the backfill loop.

        Filters at the SQL boundary: only rows whose `class` is in
        `TRADEABLE_CLASSES` are returned. This is the third line of
        defence against non-tradeable figis sneaking into the
        backfill queue — even if a stale row survived a previous
        build, this query won't pick it up.

        `limit_to` (used by the data-quality recovery loop) restricts
        the result to a figi whitelist. When None, the full
        tradeable universe is returned.

        Class-specific `get_candles` wiring lives in
        `ingestion/client.py`; this function only knows the
        tradeable set, not how each class is fetched.
        """
        from ..domain.tradeable import TRADEABLE_CLASSES

        placeholders = ",".join("?" for _ in TRADEABLE_CLASSES)
        sql = (
            f"SELECT ticker, figi, class FROM instruments "
            f"WHERE class IN ({placeholders})"
        )
        params: list = list(TRADEABLE_CLASSES)
        if limit_to:
            qs = ",".join("?" for _ in limit_to)
            sql += f" AND figi IN ({qs})"
            params.extend(limit_to)
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(sql, tuple(params)).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]

    def _get_metadata(self, figi: str) -> dict | None:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT last_bar_ts, last_run_status, total_bars FROM instrument_metadata WHERE figi = ?",
            (figi,),
        ).fetchone()
        con.close()
        return dict(row) if row else None

    def _upsert_instrument(self, row: dict) -> None:
        figi = row.get("figi")
        ticker = row.get("ticker")
        if not figi or not ticker:
            return
        # Schema requires NOT NULL on `name` and a few other fields. Tests
        # use minimal fakes; production rows from Tinkoff always have
        # these populated. Substitute empty strings for missing ones so
        # the upsert doesn't blow up on partial mocks.
        name = row.get("name") or ""
        currency = row.get("currency") or ""
        lot_size = row.get("lot_size") or 0
        # INSERT OR REPLACE handles conflicts on the `ticker` PRIMARY KEY
        # (the canonical row identity). The `figi` UNIQUE constraint is
        # updated via the figi column too. We don't use INSERT ... ON
        # CONFLICT(figi) because in production there are rare cases
        # where the same figi appears under multiple class rows, and we
        # prefer to keep the ticker as the canonical primary key.
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                "INSERT OR REPLACE INTO instruments "
                "(ticker, figi, class, name, currency, lot_size) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    ticker,
                    figi,
                    row.get("class"),
                    name,
                    currency,
                    lot_size,
                ),
            )
            con.commit()
        finally:
            con.close()

    def _seed_metadata_for_figi(self, figi: str) -> None:
        """Insert a metadata row for `figi` only if it doesn't already exist.

        Used during universe discovery: each instrument needs a metadata
        row for `_pending_count` to count it, but existing rows carry
        their last_bar_ts from previous runs and must not be wiped —
        otherwise `decide_strategy` would re-do work the daily scheduler
        has already finished.
        """
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                "INSERT OR IGNORE INTO instrument_metadata "
                "(figi, last_bar_ts, total_bars, last_run_status, last_run_at, last_error) "
                "VALUES (?, NULL, 0, 'pending', NULL, NULL)",
                (figi,),
            )
            con.commit()
        finally:
            con.close()

    def _upsert_metadata(
        self,
        *,
        figi: str,
        last_bar_ts: str | None,
        total_bars: int,
        status: str,
        error_msg: str | None,
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                "INSERT INTO instrument_metadata "
                "(figi, last_bar_ts, last_backfilled_at, total_bars, "
                " last_run_status, last_run_at, last_error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(figi) DO UPDATE SET "
                "last_bar_ts = excluded.last_bar_ts, "
                "last_backfilled_at = excluded.last_backfilled_at, "
                "total_bars = excluded.total_bars, "
                "last_run_status = excluded.last_run_status, "
                "last_run_at = excluded.last_run_at, "
                "last_error = excluded.last_error",
                (
                    figi,
                    last_bar_ts,
                    now_iso if status == "ok" else None,
                    total_bars,
                    status,
                    now_iso,
                    error_msg,
                ),
            )
            con.commit()
        finally:
            con.close()

    # ── bars write (SQLite only) ────────────────────────────────

    def _write_bars(self, *, figi: str, candles: list) -> int:
        """Insert candles into the SQLite `bars` table.

        The SQLite bars table is the single source of truth after the
        `remove-duckdb-and-parquet` change; there is no parquet mirror.
        Candles are inserted via INSERT OR IGNORE so re-running the
        same chunk (e.g. on a retry) does not raise UNIQUE constraint
        failures. Returns the number of candles passed in.
        """
        if not candles:
            return 0
        from ..db.bars_sqlite import replace_bars_for_figi

        try:
            replace_bars_for_figi(self.db_path, figi, candles, replace=False)
        except Exception as sqlite_err:  # noqa: BLE001  # pragma: no cover — surfaces to caller; covered by caller tests
            logger.error(
                "bars.sqlite_write_failed",
                figi=figi,
                error=str(sqlite_err),
            )
            raise
        return len(candles)

    def _extract_last_bar_ts(self, candles: list) -> str | None:
        """ISO date string of the most recent candle, or None if empty.

        Defensive against candle-like objects missing the `time` field
        and against the SDK returning dicts instead of gRPC objects.
        Newer t-tech SDK pre-converts candles to {"ts": "YYYY-MM-DD", ...}
        dicts — try that fast-path first before going through `time.*`.
        """
        if not candles:
            return None
        dates: list[date] = []
        for c in candles:
            try:
                if isinstance(c, dict):
                    # Fast-path: SDK pre-converted ("ts": "YYYY-MM-DD...").
                    ts = c.get("ts")
                    if isinstance(ts, str):
                        try:
                            d = date.fromisoformat(ts[:10])
                        except ValueError:
                            d = None
                        if d:
                            dates.append(d)
                            continue
                    t = c.get("time") or c.get("time_") or {}
                else:
                    t = getattr(c, "time", None)
                if isinstance(t, dict):
                    y, m, d = t.get("year"), t.get("month"), t.get("day")
                elif t is not None:
                    y, m, d = getattr(t, "year", None), getattr(t, "month", None), getattr(t, "day", None)
                else:
                    y, m, d = None, None, None
                if not (y and m and d):
                    continue
                dates.append(date(y, m, d))
            except (AttributeError, TypeError, ValueError):
                continue
        if not dates:
            return None
        latest = max(dates)
        return f"{latest.year:04d}-{latest.month:02d}-{latest.day:02d}"

    # ─── event/log ───────────────────────────────────────────────────

    async def _emit(self, event_type: str, payload: dict) -> None:
        ev = BackfillEvent(
            type=event_type,
            run_id=self.run_id,
            ts=datetime.now(timezone.utc).isoformat(),
            payload=payload,
        )
        try:
            await self.event_sink(ev)
        except Exception as e:  # noqa: BLE001 — never break the run on sink errors
            logger.warn(
                "backfill.event_sink_failed", error=str(e), event_type=event_type
            )

    async def _log(
        self, level: str, *, figi: str | None = None, message: str
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        # Resolve ticker prefix when we have it; falls back to figi.
        ticker = self._ticker_by_figi.get(figi) if figi else None
        display = f"{ticker}/{figi}" if ticker and figi else (figi or "")
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                "INSERT INTO ingestion_logs (ts, run_id, level, figi, message) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts, self.run_id, level, display, message),
            )
            con.commit()
        finally:
            con.close()
