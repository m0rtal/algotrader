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
    tickers_done: int = 0
    tickers_total: int = 0
    total_bars: int = 0

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
        limit_to: list[str] | None = None,
    ) -> None:
        """Run the full lifecycle: discover → backfill → done.

        Errors per ticker are logged but don't abort the run.

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
                        figi=figi, ticker=ticker, from_=from_, to=to
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
        delta_only: bool = True,
    ) -> int:
        """Walk every tradeable figi from MOEX listed_from to min(yesterday, listed_till).

        Replaces the Tinkoff-only daily_backfill + full_history walk.
        Insertion is INSERT OR IGNORE on PRIMARY KEY (figi, ts), so existing
        Tinkoff bars (2021+) are never overwritten.

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
        yesterday = today - timedelta(days=1)
        instruments = self._list_instruments()
        self._moex_meta: dict[str, dict | None] = {}
        self._moex_meta_lock = threading.Lock()

        def get_meta(ticker: str) -> dict | None:
            """Probe MOEX for ticker. Return {market, board, listed_from, listed_till} or None."""
            import requests
            with self._moex_meta_lock:
                if ticker in self._moex_meta:
                    return self._moex_meta[ticker]
            url = f"https://iss.moex.com/iss/securities/{urllib.parse.quote(ticker)}.json"
            try:
                data = requests.get(url, timeout=10).json()
            except Exception:
                return None
            boards = data.get("boards", {}).get("data", [])
            primary = next(
                (b for b in boards
                 if b[8] == 1  # is_traded
                 and b[1] in ("TQBR", "TQTF", "TQOB", "TQCB", "SMAL", "TQIF", "TQPI")),
                None,
            )
            if not primary:
                with self._moex_meta_lock:
                    self._moex_meta[ticker] = None
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
            with self._moex_meta_lock:
                self._moex_meta[ticker] = meta
            return meta

        def fetch_year(market: str, board: str, ticker: str, year: int) -> list[dict]:
            """Walk MOEX ISS /iss/history/.../securities/{ticker}.json for ``year``.

            MOEX caps a single response at 500 bars; for a year with >500
            trading days (rare, but possible for ETFs) we would miss data.
            We use the server-reported ``history.cursor`` (offset, total,
            page-size) to decide when to stop. Page-size itself comes from
            the cursor field — pre-2024-Q3 MOEX returned 100 even when we
            asked for 500; asking for 500 simply lets the server pick its
            current maximum and tell us via the cursor.
            """
            import requests
            base = (
                f"https://iss.moex.com/iss/history/engines/stock/markets/{market}/boards/{board}"
                f"/securities/{urllib.parse.quote(ticker)}.json"
            )
            out: list[dict] = []
            start = 0
            page_size = 500
            while True:
                try:
                    data = requests.get(
                        base,
                        params={
                            "from": f"{year}-01-01",
                            "till": f"{year}-12-31",
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

        async def fetch_tinkoff_fallback(
            figi: str, ticker: str, from_d: date, to_d: date
        ) -> list[dict]:
            """Tinkoff fallback for sanctions-delisted tickers where MOEX has no boards.
            Walks in 7-day chunks via the existing Tinkoff client."""
            chunk_retry = retry_mod.AdaptiveRetry(
                max_attempts=2, initial_delay=0.5, backoff_factor=2.0, max_delay=5.0,
            )
            out = []
            cur = from_d
            while cur <= to_d:
                chunk_end = min(cur + timedelta(days=6), to_d)
                try:
                    chunk = await chunk_retry.run(
                        lambda cur=cur, chunk_end=chunk_end: self.client.get_candles(
                            figi=figi, date_from=cur, date_to=chunk_end,
                            interval="CANDLE_INTERVAL_DAY",
                        )
                    )
                    out.extend(chunk)
                except Exception:
                    pass
                cur = chunk_end + timedelta(days=1)
            return out

        written_total = 0

        for inst in instruments:
            if self._stop_flag.is_set():
                break
            figi = inst["figi"]
            ticker = inst.get("ticker")
            if not ticker:
                continue

            meta = get_meta(ticker)
            if meta is None:
                # Sanctions-delisted / no MOEX data: Tinkoff fallback
                await self._log("info", figi=figi, message="no MOEX board; falling back to Tinkoff")
                listed_from_iso = (inst.get("listed_from") or "2014-01-01")[:10]
                try:
                    from_d = date.fromisoformat(listed_from_iso)
                except (TypeError, ValueError):
                    from_d = date(2014, 1, 1)
                candles = await fetch_tinkoff_fallback(figi, ticker, from_d, yesterday)
                if not candles:
                    await self._log("warn", figi=figi, message="Tinkoff fallback: no data")
                    continue
                written = replace_bars_for_figi(self.db_path, figi, candles, replace=False)
                written_total += written
                continue

            listed_from_iso = meta["listed_from"]
            listed_till_iso = meta["listed_till"]
            try:
                listed_from_d = date.fromisoformat(listed_from_iso[:10])
                listed_till_d = date.fromisoformat(listed_till_iso[:10])
            except (TypeError, ValueError):
                continue

            if delta_only:
                con = __import__("sqlite3").connect(self.db_path)
                try:
                    row = con.execute(
                        "SELECT MIN(ts) FROM bars WHERE figi = ?", (figi,)
                    ).fetchone()
                    first_ts = row[0] if row and row[0] else None
                finally:
                    con.close()
                if first_ts and date.fromisoformat(first_ts[:10]) <= listed_from_d:
                    # figi already has bars covering MOEX window — skip
                    continue

            to_d = min(yesterday, listed_till_d)
            from_d = listed_from_d
            if from_d > to_d:
                continue

            year = from_d.year
            all_bars: list[dict] = []
            while year <= to_d.year:
                year_bars = fetch_year(meta["market"], meta["board"], ticker, year)
                for b in year_bars:
                    b["figi"] = figi
                    all_bars.append(b)
                year += 1
            if not all_bars:
                continue
            written = replace_bars_for_figi(self.db_path, figi, all_bars, replace=False)
            written_total += written

        return written_total

    # ─── universe discovery ──────────────────────────────────────────

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
        """
        if ticker:
            self._ticker_by_figi[figi] = ticker
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
                chunks_failed += 1
                last_chunk_error = str(e)
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
