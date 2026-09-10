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
    bars_dir: str
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
    ) -> None:
        """Run the full lifecycle: discover → backfill → done.

        Errors per ticker are logged but don't abort the run.
        """
        self._stop_flag.clear()
        with self._lock:
            self.state = BackfillState.DISCOVERING
            self.tickers_done = 0
            self.tickers_total = 0
            self.total_bars = 0

        await self._emit("status", {"state": self.state.value, "phase": "start"})

        # Step 1: discover the universe. Always runs; idempotent.
        try:
            total = await self._discover_universe()
            self.tickers_total = total
            await self._emit(
                "status",
                {"state": self.state.value, "tickers_total": total},
            )
        except Exception as e:
            await self._log("error", figi=None, message=f"universe discovery failed: {e}")
            await self._emit("done", {"tickers_done": 0, "tickers_total": 0, "status": "error"})
            with self._lock:
                self.state = BackfillState.IDLE
            return

        # Step 2: backfill each instrument.
        with self._lock:
            self.state = BackfillState.BACKFILLING

        instruments = self._list_instruments()
        for inst in instruments:
            if self._stop_flag.is_set():
                break
            figi = inst["figi"]
            metadata = self._get_metadata(figi)
            strategy, from_, to = decide_strategy(
                metadata_row=metadata,
                today=date.today(),
                history_years=history_years,
                incremental_threshold_days=incremental_threshold_days,
            )
            if strategy == "skip":
                self.tickers_done += 1
                await self._emit(
                    "ticker_progress",
                    {
                        "figi": figi,
                        "ticker": inst.get("ticker"),
                        "status": "skipped",
                        "bars_written": 0,
                    },
                )
                continue
            try:
                bars_written = await self._backfill_one(figi=figi, from_=from_, to=to)
                self.tickers_done += 1
                self.total_bars += bars_written
            except Exception as e:  # noqa: BLE001 — defensive, never abort
                # Per-ticker errors are already logged in _backfill_one.
                await self._log("error", figi=figi, message=f"unhandled: {e}")

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

    # ─── universe discovery ──────────────────────────────────────────

    async def _discover_universe(self) -> int:
        """Fetch all 5 asset classes and upsert into the `instruments` table.

        Returns total instruments inserted across all classes.

        The TinkoffClient Protocol defines `get_shares/get_bonds/...`
        methods (see `ingestion/client.py`); we call those rather than
        the raw SDK's `shares/bonds/...` properties so the wrapper can
        translate gRPC responses to dicts.
        """
        total = 0
        # Fetch the universe of all asset classes for `instruments` table
        # completeness, but only `share` and `etf` actually emit
        # `get_candles` on the live API — bonds/futures/options have
        # candle-less endpoints (coupons, margin, etc) we don't yet
        # model. For those classes we upsert the metadata with
        # status='no_candles_method' so the operator sees them in
        # `instruments` and `pending` but the runner doesn't burn
        # rate-limit slots on per-ticker 404s.
        fetcher_specs = [
            ("get_shares", "share"),
            ("get_bonds", "bond"),
            ("get_etfs", "etf"),
            ("get_futures", "future"),
            ("get_options", "option"),
        ]
        classes_with_candles = {"share", "etf"}
        for method_name, _cls in fetcher_specs:
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
                # Classes without `get_candles` are marked
                # no_candles_method here so the runner doesn't
                # queue them in the backfill loop where they'd just
                # burn rate-limit and return NOT_FOUND.
                if asset_class and figi and asset_class not in classes_with_candles:
                    self._upsert_metadata(
                        figi=figi,
                        last_bar_ts=None,
                        total_bars=0,
                        status="no_candles_method",
                        error_msg=None,
                    )
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
    ) -> int:
        """Fetch candles for one ticker, filter closed, write parquet, update metadata.

        Returns the number of bars written. Errors are caught and logged
        as `ticker_progress` events with `status='error'`; the runner
        continues to the next ticker.
        """
        # Tinkoff's live API rejects (INVALID_ARGUMENT 30014) requests longer
        # than ~7 days for the day interval. Walk the [from_, to] window in
        # 7-day chunks; a single-chunk call behaves like the old flow.
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
                chunk = await self.client.get_candles(
                    figi=figi,
                    date_from=cur,
                    date_to=chunk_end,
                    interval="CANDLE_INTERVAL_DAY",
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
            await self._log(
                "warn",
                figi=figi,
                message=f"no candles in {from_}..{to}",
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
            except (TypeError, ValueError):
                return None

        closed = [c for c in closed if (dt := _candle_date(c)) and dt < today_]

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

    def _list_instruments(self) -> list[dict]:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT ticker, figi, class FROM instruments").fetchall()
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

    # ─── parquet write ──────────────────────────────────────────────

    def _write_bars(self, *, figi: str, candles: list) -> int:
        """Append candles to data/bars/<figi>.parquet.

        Delegates to the existing `bars.write_bars` helper if available,
        else creates a minimal one inline. The runner doesn't recompute
        parquet schema — the existing ingest path handles that on first
        write.
        """
        if not candles:
            return 0
        # ponytail: import lazily so the test doesn't need a full
        # parquet/duckdb stack when the runner is exercised without the
        # write helper.
        from .bars import append_bars
        path = Path(self.bars_dir) / f"{figi}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        return append_bars(path=str(path), candles=candles)

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
