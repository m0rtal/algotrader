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
import time
import urllib.parse
from collections.abc import Iterator
import requests
import requests.adapters  # HTTPAdapter lives here, not on requests namespace
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from ..observability.logging import get_logger
from .closed_candles import is_closed_candle
from .no_trade_evidence import MOEXFetchOutcome  # noqa: F401  (re-exported)
# Imported lazily inside the call sites that need it; this keeps the
# module-level import surface minimal — `_backfill_one_moex` is the
# only path that calls ``fetch_issuer_identity`` directly (the other
# paths use ``_get_meta_moex`` which already extracts ISIN from the
# same HTTP response).

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


# Module-level pooled HTTP session for MOEX ISS probes.
#
# Why this exists (PR #122, 2026-09-23):
#   The previous code did ``import requests; requests.get(url, timeout=(5, 30))``
#   inside ``_get_meta_moex``. Each call opened a fresh TCP+TLS connection,
#   incurring DNS+handshake latency for every one of the 3837 figis the
#   prefetch walks. Combined with urllib3's default-retry chain (3 retries
#   on connection errors, ~3.3 s each), an intermittent DNS hiccup on
#   ``iss.moex.com`` cost ~10 s per failed call — turning the prefetch from
#   a 5-minute warm-up into a 14-minute (often timeout-killed) stall.
#
#   A single ``requests.Session`` with a bounded ``HTTPAdapter`` reuses
#   connections across calls so DNS is paid once per host (and the
#   underlying socket is reused). ``Retry(total=0)`` short-circuits the
#   default 3-retry chain so a DNS failure surfaces in one 5 s attempt
#   instead of 10 s, and the bounded pool size (``pool_maxsize=16``)
#   matches the prefetch Semaphore below — the worker can't outrun the
#   pool it has to drain into.
_MOEX_SESSION: requests.Session | None = None


def _get_moex_session() -> requests.Session:
    """Return the module-level pooled MOEX ISS session, creating on first use."""
    global _MOEX_SESSION
    if _MOEX_SESSION is None:
        s = requests.Session()
        # Retry(total=0) — disable urllib3's default 3-retry chain so a
        # transient DNS hiccup fails fast (5 s) rather than 3 × ~3.3 s.
        # Connection pooling (pool_connections / pool_maxsize=16) lets
        # the prefetch reuse TCP+TLS sessions across figis instead of
        # paying DNS+handshake per call.
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=16,
            pool_maxsize=16,
            max_retries=0,
        )
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _MOEX_SESSION = s
    return _MOEX_SESSION


def _get_meta_moex(
    ticker: str,
    yesterday: date,
    *,
    meta_cache: dict[str, dict | None],
    meta_lock: threading.Lock,
) -> dict | None:
    """Probe MOEX for ticker. Return {market, board, listed_from, listed_till, isin} or None.

    Uses the module-level pooled ``_MOEX_SESSION`` so the prefetch reuses
    TCP+TLS connections across all 3837 figis instead of opening a fresh
    connection per call. Caches results in ``meta_cache`` (guarded by
    ``meta_lock``).

    ``isin`` is pulled from the same response's ``description`` block —
    zero new HTTP round-trips. It lets the MOEX-routed write paths
    cross-check each figi's stored ISIN before stamping bars onto it
    (the T/DIOD/ROST cross-listed-mirror collision in production):
    ``fetch_issuer_identity`` is reserved for the same-day script
    where the meta cache already exists in a different shape.
    """
    with meta_lock:
        if ticker in meta_cache:
            return meta_cache[ticker]
    session = _get_moex_session()
    url = f"https://iss.moex.com/iss/securities/{urllib.parse.quote(ticker)}.json"
    try:
        # (connect_timeout, read_timeout) — prevents indefinite hangs
        # when MOEX ISS accepts the TCP connection but stalls mid-response.
        # Default urllib3 retries are disabled at the Session adapter level
        # so DNS hiccups surface in one 5 s attempt, not 3 × ~3.3 s.
        data = session.get(url, timeout=(5, 30)).json()
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
    # Pull ISIN from the same response's ``description`` block.
    # Match the lookup in ``fetch_issuer_identity`` (no_trade_evidence)
    # so the two paths agree on what MOEX thinks the issuer ISIN is.
    isin = ""
    for row in data.get("description", {}).get("data", []):
        if len(row) > 2 and row[0] == "ISIN" and isinstance(row[2], str):
            isin = row[2]
            break
    meta = {
        "market": market,
        "board": boardid,
        "listed_from": listed_from,
        "listed_till": listed_till or yesterday.isoformat(),
        "isin": isin,
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


# PR #129 (2026-09-24): Tinkoff fallback circuit breaker.
#
# Helpers below store breaker state in ``instrument_metadata`` so the
# decision survives worker restarts. ``_tinkoff_breaker_is_open`` is
# the hot read (called once per figi per cycle); ``record_failure``
# increments the failure counter and opens the breaker at threshold;
# ``record_success`` resets both. All three do a single short SQLite
# transaction.


def _tinkoff_breaker_is_open(db_path: str, figi: str) -> bool:
    """Return True if the breaker is open for this figi right now.

    An "open" breaker means we should skip the Tinkoff fallback call
    entirely (it would time out or return empty anyway). Auto-resets
    if ``tinkoff_breaker_open_until`` is in the past.
    """
    con = sqlite3.connect(db_path, timeout=5.0)
    try:
        try:
            row = con.execute(
                "SELECT tinkoff_breaker_open, tinkoff_breaker_open_until "
                "FROM instrument_metadata WHERE figi = ?",
                (figi,),
            ).fetchone()
        except sqlite3.OperationalError:
            # Schema hasn't been migrated yet (e.g. an in-process test
            # built a stripped-down instrument_metadata table). Treat as
            # closed so the breaker doesn't block real work.
            return False
    finally:
        con.close()
    if row is None:
        # No metadata row yet (fresh figi from universe_sync). Allow
        # the call — record_failure will create the row on first hit.
        return False
    is_open, until = row
    if not is_open:
        return False
    if until is None:
        # Opened but no expiry — defensive default: treat as open
        # until record_failure/record_success clears it.
        return True
    try:
        until_dt = datetime.fromisoformat(until)
    except (TypeError, ValueError):
        return True
    return datetime.now() < until_dt


def _tinkoff_breaker_record_failure(
    db_path: str, figi: str, threshold: int,
) -> None:
    """Increment the consecutive-failure counter for figi; open the
    breaker when it reaches ``threshold``.
    """
    con = sqlite3.connect(db_path, timeout=5.0)
    try:
        # INSERT ... ON CONFLICT DO NOTHING is idempotent under
        # concurrent writers: the first one creates the row, the rest
        # skip the INSERT and proceed to the UPDATE. SQLite ≥ 3.24
        # supports this syntax; the codebase already targets ≥ 3.11.
        now_iso = datetime.now().isoformat()
        con.execute(
            "INSERT OR IGNORE INTO instrument_metadata(figi) VALUES (?)",
            (figi,),
        )
        con.execute(
            "UPDATE instrument_metadata SET "
            "tinkoff_consecutive_failures = COALESCE(tinkoff_consecutive_failures, 0) + 1, "
            "tinkoff_last_failure_ts = ? "
            "WHERE figi = ?",
            (now_iso, figi),
        )
        # Read back the new counter; if it crossed the threshold,
        # open the breaker. Doing it via a separate statement (instead
        # of combining with the UPDATE) keeps the SQL portable and the
        # race window minimal: at worst two threads cross the
        # threshold and we open the breaker twice — both succeed,
        # neither is wrong.
        row = con.execute(
            "SELECT tinkoff_consecutive_failures FROM instrument_metadata "
            "WHERE figi = ?",
            (figi,),
        ).fetchone()
        new_failures = row[0] if row else 0
        if new_failures >= threshold:
            open_until = (
                datetime.now()
                + timedelta(hours=_TINKOFF_BREAKER_OPEN_HOURS_GLOBAL)
            ).isoformat()
            con.execute(
                "UPDATE instrument_metadata SET "
                "tinkoff_breaker_open = 1, "
                "tinkoff_breaker_open_until = ? "
                "WHERE figi = ?",
                (open_until, figi),
            )
        con.commit()
    finally:
        con.close()


def _tinkoff_breaker_record_success(db_path: str, figi: str) -> None:
    """Reset the failure counter and close the breaker.

    Called whenever Tinkoff returns at least one candle. Cheap to
    invoke on every successful fetch — it's a single UPDATE.
    """
    con = sqlite3.connect(db_path, timeout=5.0)
    try:
        con.execute(
            "UPDATE instrument_metadata SET "
            "tinkoff_consecutive_failures = 0, "
            "tinkoff_breaker_open = 0, "
            "tinkoff_breaker_open_until = NULL "
            "WHERE figi = ?",
            (figi,),
        )
        con.commit()
    finally:
        con.close()


# Default expiry window used by ``_tinkoff_breaker_record_failure``.
# Mirrors the per-call value in ``backfill_from_moex`` so the breaker
# auto-resets whether we opened it via the in-process closure above
# or via this module-level helper (when called from tests).
_TINKOFF_BREAKER_OPEN_HOURS_GLOBAL = 24


def _reduce_outcomes(prev: str, new: str) -> str:
    """Return the more-severe of two ``MOEXFetchOutcome`` values.

    Severity order (most-severe first): ``error`` > ``malformed`` >
    ``partial`` > ``identity_mismatch`` > ``complete``.

    Used to collapse per-page outcomes into a single per-fetch value:
    callers only see the worst thing the upstream did during the
    walk, regardless of how many pages preceded the bad one.
    """
    order = {
        "error": 5,
        "malformed": 4,
        "partial": 3,
        "identity_mismatch": 2,
        "complete": 1,
    }
    if order.get(new, 0) > order.get(prev, 0):
        return new
    return prev


def _fetch_year_moex_iter(
    market: str,
    board: str,
    ticker: str,
    year: int,
    *,
    last_trading_day: date | None = None,
) -> Iterator[tuple[list[dict], str]]:
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

    The iterator yields ``(page_rows, page_outcome)`` after every
    page. The legacy ``_fetch_year_moex`` simply concatenates the
    rows; ``_fetch_year_moex_outcome`` folds the per-page outcomes
    into a single worst-severity value via ``_reduce_outcomes``.

    Each emitted dict carries the raw MOEX columns the no-trade
    evidence helper needs (``SECID``, ``BOARDID``, ``NUMTRADES``,
    ``VALUE``) so :mod:`no_trade_evidence` can confirm a zero-trade
    row without a second HTTP round-trip. Empty OHLC values are kept
    as ``None`` — the bars writer already drops such rows, but the
    evidence helper relies on the explicit None shape.

    Strict feed contract (Task 1) — for every page:

    * HTTP ``status_code == 200`` else ``"malformed"`` and stop.
    * ``history.columns`` must contain ``TRADEDATE``, the four OHLC
      columns, and ``VOLUME``; otherwise ``"malformed"`` and stop.
    * Each parsed row's length must equal the column-list length;
      too-short rows are dropped silently.
    * Zero-trade rows (``VOLUME == 0``) require ``NUMTRADES == 0``
      AND ``VALUE == 0``; a missing counter on a zero-trade row is
      ``"malformed"``.
    * Non-zero-volume rows with missing OHLC are dropped, page
      ``"malformed"``.
    * Cursor block carries ``[offset, total, page_size]``. The
      server-reported ``offset`` must equal the loop's requested
      ``start``; mismatch → ``"malformed"`` and stop. Non-positive
      ``total`` → ``"malformed"`` and stop. Repeated cursor offset
      across two pages → ``"malformed"`` and stop.
    * Identity: any row with ``SECID != ticker`` OR
      ``BOARDID != board`` flips the page's outcome to
      ``"identity_mismatch"`` (batch-poisoning; identity is per-batch).
    """
    base = (
        f"https://iss.moex.com/iss/history/engines/stock/markets/{market}/boards/{board}"
        f"/securities/{urllib.parse.quote(ticker)}.json"
    )
    if last_trading_day is not None and year == last_trading_day.year:
        till = last_trading_day.isoformat()
    else:
        till = f"{year}-12-31"
    required_cols = {"TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"}
    start = 0
    page_size = 500
    # Hard cap on the number of HTTP pages the iterator will walk
    # for a single year. MOEX never returns more than 500 rows per
    # page, so a normal year finishes in 1 page and a busy year in
    # 2. The cap exists to bound an upstream that fails to terminate
    # (no cursor + full pages forever) or that breaks the page-size
    # contract; without it a buggy upstream would loop indefinitely.
    # A year of MOEX history is at most a few hundred trading days,
    # so 20 pages is far more than any real year needs and acts as
    # a circuit-breaker. On hit we mark ``partial`` (we have rows;
    # we just couldn't certify) and return — never malformed, so
    # the bar consumer keeps what it got.
    max_pages = 20
    pages_walked = 0
    prev_cursor_offset: int | None = None
    prev_cursor_total: int | None = None
    while True:
        pages_walked += 1
        if pages_walked > max_pages:
            # Cap reached. Yield the rows collected so far as
            # ``partial`` (caller keeps the data; the outcome tells
            # the evidence path to refuse).
            yield ([], "partial")
            return
        # Network failure: the iterator signals ``error`` exactly
        # once and stops — callers that care (the evidence path)
        # see it; bar consumers keep the partial list they already
        # collected from prior pages.
        try:
            response = requests.get(
                base,
                params={
                    "from": f"{year}-01-01",
                    "till": till,
                    "start": start,
                },
                timeout=30,
            )
        except Exception:
            yield ([], "error")
            return
        kept_rows: list[dict] = []
        try:
            data = response.json()
        except Exception:
            yield (kept_rows, "malformed")
            return
        cols = data.get("history", {}).get("columns", []) or []
        if not cols or not required_cols.issubset(set(cols)):
            yield (kept_rows, "malformed")
            return
        rows_raw = data.get("history", {}).get("data", []) or []
        page_outcome = "complete"
        for row in rows_raw:
            # Strict feed contract: every raw row MUST match the
            # column-list length. Any malformed raw row (short, long,
            # non-sequence) POISONS the page's outcome to ``malformed``
            # BEFORE the loop continues; valid clean rows already
            # accepted in earlier iterations are preserved in
            # ``kept_rows`` so the bar consumer keeps its data, and
            # the evidence path refuses the batch via the outcome.
            # Without the poison: cursor-close on a kept-only count
            # would certify a fetch that actually contained a bad row
            # (parent repro, round 2).
            row_ok = False
            try:
                if len(row) == len(cols):
                    row_ok = True
            except TypeError:
                # Non-sequence row (None, str, dict, scalar) — malformed.
                row_ok = False
            if not row_ok:
                page_outcome = _reduce_outcomes(page_outcome, "malformed")
                continue
            d = dict(zip(cols, row))
            # SECID/BOARDID identity check (per-batch poison).
            if str(d.get("SECID") or "") != ticker \
                    or str(d.get("BOARDID") or "") != board:
                page_outcome = _reduce_outcomes(
                    page_outcome, "identity_mismatch",
                )
                # Still keep the row — the bar consumer sees the
                # data they already accepted today; the outcome is
                # the signal the evidence path uses to refuse.
            volume_raw = d.get("VOLUME")
            if volume_raw is None:
                # VOLUME column present but row's value is missing
                # — treat as malformed. The strict contract requires
                # VOLUME to be present (zero is a valid value).
                page_outcome = _reduce_outcomes(page_outcome, "malformed")
                continue
            # VOLUME must be an integer count of shares, not a
            # fractional float. ``int(0.5) == 0`` would silently
            # promote a 0.5 row into the zero-trade shape (false
            # positive no-trade evidence); ``int(1000.5) == 1000``
            # would silently corrupt a real bar. Reject any float
            # that is not a whole number, plus any non-numeric type.
            if isinstance(volume_raw, bool) or not isinstance(
                volume_raw, (int, float),
            ):
                page_outcome = _reduce_outcomes(page_outcome, "malformed")
                continue
            if isinstance(volume_raw, float):
                if not volume_raw.is_integer():
                    page_outcome = _reduce_outcomes(
                        page_outcome, "malformed",
                    )
                    continue
                volume = int(volume_raw)
            else:
                volume = int(volume_raw)
            # Per-shape contract:
            #  * zero-volume rows MUST carry NUMTRADES == 0 AND VALUE == 0;
            #    missing/None on those counters is malformed.
            #  * non-zero rows MUST have all four OHLC columns present;
            #    missing/None on OHLC when VOLUME > 0 is malformed.
            if volume == 0:
                if d.get("NUMTRADES") != 0 or d.get("VALUE") != 0:
                    page_outcome = _reduce_outcomes(
                        page_outcome, "malformed",
                    )
                    continue
            else:
                if (d.get("OPEN") is None
                        or d.get("HIGH") is None
                        or d.get("LOW") is None
                        or d.get("CLOSE") is None):
                    page_outcome = _reduce_outcomes(
                        page_outcome, "malformed",
                    )
                    continue
            kept_rows.append({
                "figi": None,  # filled by caller
                "ts": d.get("TRADEDATE"),
                "open": d.get("OPEN"),
                "high": d.get("HIGH"),
                "low": d.get("LOW"),
                "close": d.get("CLOSE"),
                "volume": volume,
                "source": "moex",
                # No-trade evidence: raw upstream columns, kept verbatim.
                "_secid": d.get("SECID"),
                "_boardid": d.get("BOARDID"),
                "_numtrades": d.get("NUMTRADES"),
                "_value": d.get("VALUE"),
            })
        # Spec line 53: HTTP ``status_code == 200`` is required for
        # the page to be considered ``complete``. A missing
        # ``status_code`` attribute is malformed (we cannot
        # certify an unverified response), not 200-by-default.
        # The check is done AFTER row parsing so the bar-list
        # wrapper still gets the parsed rows (existing partial-
        # bar tolerance is preserved); the outcome tag is the
        # signal the evidence path uses to refuse.
        # A real ``requests.Response`` always carries
        # ``status_code``; a missing one means the test double
        # is broken (or someone returned the wrong object).
        if not hasattr(response, "status_code"):
            yield (kept_rows, "malformed")
            return
        if response.status_code != 200:
            yield (kept_rows, "malformed")
            return
        # Cursor validation. The cursor MUST advance (or close) —
        # a repeated offset is a server-side loop / no-progress
        # condition we cannot trust.
        cursor_rows = data.get("history.cursor", {}).get("data") or []
        if cursor_rows:
            try:
                offset, total, _srv_page_size = cursor_rows[0][:3]
            except (TypeError, ValueError):
                page_outcome = _reduce_outcomes(page_outcome, "malformed")
                yield (kept_rows, page_outcome)
                return
            if offset is None or total is None:
                page_outcome = _reduce_outcomes(page_outcome, "malformed")
                yield (kept_rows, page_outcome)
                return
            try:
                offset_i = int(offset)
                total_i = int(total)
            except (TypeError, ValueError):
                page_outcome = _reduce_outcomes(page_outcome, "malformed")
                yield (kept_rows, page_outcome)
                return
            try:
                srv_page_size_i = int(_srv_page_size)
            except (TypeError, ValueError):
                page_outcome = _reduce_outcomes(page_outcome, "malformed")
                yield (kept_rows, page_outcome)
                return
            # Reject non-strict-integer cursor values: ``bool`` is a
            # subclass of ``int`` (``int(True) == 1``); a ``float``
            # silently truncates (``int(0.5) == 0``). The strict feed
            # contract demands an integer offset/total/page_size; any
            # other type is malformed.
            if (isinstance(offset, bool) or isinstance(total, bool)
                    or isinstance(_srv_page_size, bool)
                    or isinstance(offset, float)
                    or isinstance(total, float)
                    or isinstance(_srv_page_size, float)):
                page_outcome = _reduce_outcomes(page_outcome, "malformed")
                yield (kept_rows, page_outcome)
                return
            if srv_page_size_i <= 0:
                # A non-positive server-reported page_size is
                # malformed — we cannot reason about "short page"
                # without a valid denominator. Same severity as a
                # non-positive ``total`` (spec line 33).
                page_outcome = _reduce_outcomes(page_outcome, "malformed")
                yield (kept_rows, page_outcome)
                return
            if offset_i != start:
                page_outcome = _reduce_outcomes(page_outcome, "malformed")
                yield (kept_rows, page_outcome)
                return
            if total_i <= 0:
                # A non-positive server-reported ``total`` is
                # malformed (spec line 33): we cannot reason
                # about pagination completeness without a valid
                # upper bound.
                page_outcome = _reduce_outcomes(page_outcome, "malformed")
                yield (kept_rows, page_outcome)
                return
            if (prev_cursor_total is not None
                    and total_i != prev_cursor_total):
                # The cursor's ``total`` must be stable across
                # pages (a real MOEX session reports a single
                # value). A change mid-walk means the upstream
                # is shifting its count — a contract-less state
                # we cannot certify. Spec line 30-31 (consistency
                # check).
                page_outcome = _reduce_outcomes(page_outcome, "malformed")
                yield (kept_rows, page_outcome)
                return
            prev_cursor_total = total_i
            if len(kept_rows) > srv_page_size_i:
                # Spec line 30-31: page_size consistency check
                # on the high side. The server reported a
                # page_size, but the response carried MORE rows
                # than that — the loop cannot trust a server
                # that doesn't even keep its own page-size
                # contract (the low side is the short-page-no-
                # cursor / partial case handled below).
                page_outcome = _reduce_outcomes(page_outcome, "malformed")
                yield (kept_rows, page_outcome)
                return
            if prev_cursor_offset is not None and offset_i == prev_cursor_offset:
                page_outcome = _reduce_outcomes(page_outcome, "malformed")
                yield (kept_rows, page_outcome)
                return
            prev_cursor_offset = offset_i
            # Decide between final-page-yield and intermediate-yield:
            # the per-page outcome stays whatever this page's data
            # determined; the ``partial`` tag is reserved for the
            # final-page case (cursor closed but offset+len<total).
            if offset_i + len(kept_rows) >= total_i:
                yield (kept_rows, page_outcome)
                return
            # A short page (len(kept_rows) < server-reported
            # page_size) with a consistent cursor that still
            # promises more rows is the spec's "final page" signal
            # — the server is wrapping up. Mark this per-fetch
            # ``partial``: the data we have is fine, the loop
            # just couldn't reach ``total``. The next request
            # would either return the same offset (repeated →
            # malformed) or advance (this branch would be
            # skipped, we'd walk the next page).
            # We use the server-reported page_size (from the
            # cursor) rather than the loop's requested 500,
            # because MOEX may serve fewer rows per response
            # than we asked for; a server-full page (e.g. 100
            # rows when the cursor reports page_size=100) is
            # still a full page from MOEX's perspective.
            if len(kept_rows) < srv_page_size_i:
                page_outcome = _reduce_outcomes(page_outcome, "partial")
                yield (kept_rows, page_outcome)
                return
            # Intermediate page — data parsed cleanly and the
            # loop has more to walk. Yield with the per-page
            # outcome (which is ``complete`` if this page's data
            # was clean).
            yield (kept_rows, page_outcome)
            start += len(kept_rows)
            continue
        # No cursor at all.
        if len(kept_rows) == 0 and len(rows_raw) == 0:
            # Empty response — nothing more to do; this is not
            # necessarily an error (the year genuinely may have no
            # data). Yield once with whatever outcome we have so
            # callers can record it, then stop.
            yield (kept_rows, page_outcome)
            return
        if len(rows_raw) < page_size:
            # Short page, no cursor → cannot certify completeness;
            # the strict contract marks this ``malformed``.
            page_outcome = _reduce_outcomes(page_outcome, "malformed")
            yield (kept_rows, page_outcome)
            return
        # Full page, no cursor → not malformed per se, but we
        # cannot certify pagination completeness without a cursor.
        # Mark ``partial`` so the outcome reflects "we don't know
        # if this is everything".
        page_outcome = _reduce_outcomes(page_outcome, "partial")
        yield (kept_rows, page_outcome)
        start += len(kept_rows)


def _fetch_year_moex(
    market: str,
    board: str,
    ticker: str,
    year: int,
    last_trading_day: date | None = None,
) -> list[dict]:
    """Bar-list wrapper around :func:`_fetch_year_moex_iter`.

    Signature preserved (positional-or-keyword ``last_trading_day``,
    no ``*`` separator) so existing callers keep working unchanged —
    they just get ``last_trading_day=None`` and the function falls
    back to ``year-12-31``.
    """
    out: list[dict] = []
    for page_rows, _outcome in _fetch_year_moex_iter(
        market, board, ticker, year,
        last_trading_day=last_trading_day,
    ):
        out.extend(page_rows)
    return out


def _fetch_year_moex_outcome(
    market: str,
    board: str,
    ticker: str,
    year: int,
    last_trading_day: date | None = None,
) -> tuple[list[dict], str]:
    """Same walk as :func:`_fetch_year_moex` but returns an outcome.

    Returns ``(rows, outcome)``. ``outcome`` is one of the values in
    the ``MOEXFetchOutcome`` literal declared in
    :mod:`no_trade_evidence` (re-exported here for callers that
    already imported it from this module). The outcome is the
    worst-severity per-page outcome across the whole walk.
    """
    out: list[dict] = []
    overall = "complete"
    for page_rows, page_outcome in _fetch_year_moex_iter(
        market, board, ticker, year,
        last_trading_day=last_trading_day,
    ):
        out.extend(page_rows)
        overall = _reduce_outcomes(overall, page_outcome)
    return out, overall


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
        # Per-year hard cap at `to_d` so we never request rows outside
        # the window.
        # NB: ``_fetch_year_moex`` only honors ``last_trading_day`` when
        # ``year == last_trading_day.year`` (it asks MOEX for the full
        # year otherwise). For any other year, the response can contain
        # bars outside [from_d, to_d]; we filter those out below.
        year_cap = min(to_d, cap)
        year_bars = _fetch_year_moex(
            market, board, ticker, year, last_trading_day=year_cap
        )
        # Defensive post-filter: keep only bars whose TRADEDATE falls
        # inside the requested window. This closes the gap when
        # `_fetch_year_moex` returned the full year for a year other
        # than ``last_trading_day.year`` (e.g. a January request for a
        # recent-tail window that crosses Dec → Jan). Without this,
        # ``backfill_moex_recent_tail`` would silently INSERT OR IGNORE
        # bars dated outside the requested tail window, polluting the
        # bars table with out-of-window rows.
        window_lo = from_d.isoformat()
        window_hi = year_cap.isoformat()
        for b in year_bars:
            ts = b.get("ts") or ""
            if window_lo <= str(ts)[:10] <= window_hi:
                out.append(b)
    return out


def _filter_moex_bars_by_identity(
    bars: list[dict], *, ticker: str, board: str,
) -> list[dict]:
    """Drop bars whose raw MOEX SECID/BOARDID disagrees with what we asked for.

    Every MOEX source emits ``_secid`` and ``_boardid`` alongside the
    candle columns (set in :func:`_fetch_year_moex`). When the upstream
    board lookup is stale or the ticker is shared by a cross-listed
    mirror, MOEX can serve rows from a different instrument on the same
    board (or even a different board under the same history endpoint).
    Without this filter those rows would be silently attached to the
    figi the caller asked about — the root cause of the T/DIOD/ROST
    cross-pollution seen in production.

    The filter is data-driven (no extra HTTP round-trip per figi);
    callers that already know the expected ticker/board do the
    comparison in-process. Empty lists pass through unchanged; rows
    missing either identity field are dropped fail-closed.
    """
    if not bars:
        return bars
    return [
        b for b in bars
        if str(b.get("_secid") or "") == ticker
        and str(b.get("_boardid") or "") == board
    ]


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
    consecutive_failures = 0
    while cur <= to_d:
        chunk_end = min(cur + timedelta(days=6), to_d)
        chunk_start_t = _time.time()
        # The broker's date_to is EXCLUSIVE: a [09-25..09-30] request
        # returns rows through 09-29 only. Ask for one extra day or the
        # final day of every window is never fetched (observed
        # 2026-10-01: RGEN max_ts stuck at the session before the last
        # one). Slightly over-fetching into an unfinished session is
        # harmless — the broker returns only closed candles.
        fetch_end = chunk_end + timedelta(days=1)
        try:
            chunk = await chunk_retry.run(
                lambda cur=cur, fetch_end=fetch_end: client.get_candles(
                    figi=figi, date_from=cur, date_to=fetch_end,
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
            # "Channel is closed" (gRPC INTERNAL_STREAM_CLOSED): the
            # SDK's cached AsyncClient handle is dead but the retry
            # decorator's non_rate_error path raises immediately, so
            # the chunk loop was hammering the dead channel at ~1kHz
            # before this check. Bail out so the supervisor can
            # SIGKILL and relaunch the worker — that triggers
            # ``RealTinkoffClient.__init__`` to build a fresh channel.
            # Observed 2026-09-27 18:15 MSK: 59 chunk.failed in 5 min
            # vs. 0 bars added.
            if "Channel is closed" in err_str or "channel" in err_str.lower():
                logger.warn(
                    "backfill.tinkoff.channel_dead",
                    figi=figi, ticker=ticker,
                    message="Tinkoff channel closed mid-run; aborting figi (will rebuild next cycle)",
                )
                return out
            # De-listed / frozen instruments fail EVERY chunk (the
            # broker has no data and each call burns its retry budget).
            # Continuing through 13 chunks per figi wasted ~45 s each
            # and tripped the per-figi timeout, which counted as a
            # circuit-breaker failure — ~1649 instruments accumulated
            # fake breakers this way on 2026-10-01. Two consecutive
            # chunk failures is enough evidence: bail out and let the
            # breaker accounting decide the figi's fate cheaply.
            consecutive_failures += 1
            if consecutive_failures >= 2:
                logger.warn(
                    "backfill.tinkoff.chunk_fail_bail",
                    figi=figi, ticker=ticker,
                    message="2 consecutive chunk failures; aborting figi early",
                )
                return out
        else:
            consecutive_failures = 0
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

        When `recent_tail_days > 0`, a first pass fetches only the
        last `recent_tail_days` trading days from MOEX ISS for every figi
        with a primary board. The historical walk runs afterward and
        reuses the MOEX metadata cache. This keeps fresh bars flowing
        even when a long historical walk or broker fallback stalls.

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
        # Run the recent pass before the slow multi-year walk. It populates
        # self._moex_meta, which the historical prefetch reuses below.
        if recent_tail_days > 0:
            written_total += await self.backfill_moex_recent_tail(
                days=recent_tail_days, today=today,
            )
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

        # PR #129 (2026-09-24): Tinkoff fallback circuit breaker.
        # Empty figis (no MOEX board, no bars ever written) trigger a
        # 45s Tinkoff timeout on every cycle. With ~700 such figis the
        # backfill_moex phase ran for ~9 hours per cycle waiting on
        # refused Tinkoff responses, so downstream phases
        # (corporate_actions, dividends) never ran. After
        # ``_TINKOFF_BREAKER_THRESHOLD`` consecutive empty responses,
        # we mark the figi with ``tinkoff_breaker_open=1`` and skip it
        # for ``_TINKOFF_BREAKER_OPEN_HOURS`` hours. The breaker is
        # stored in ``instrument_metadata`` so it survives restarts.
        _TINKOFF_BREAKER_THRESHOLD = 3
        _TINKOFF_BREAKER_OPEN_HOURS = 24

        async def _process_moex_year(inst: dict, meta: dict, year: int) -> list[dict]:
            year_bars = self._fetch_year_moex(
                meta["market"], meta["board"], inst["ticker"], year,
                last_trading_day=yesterday,
            )
            # Identity filter: drop rows whose raw SECID/BOARDID disagrees
            # with the ticker/board we asked MOEX for. Closes the
            # cross-listed-mirror path (T/DIOD/ROST cases in production)
            # for the historical multi-year walk. Cost is in-process; no
            # extra HTTP round-trips.
            year_bars = _filter_moex_bars_by_identity(
                year_bars, ticker=inst["ticker"], board=meta["board"],
            )
            for b in year_bars:
                b["figi"] = inst["figi"]
            return year_bars

        async def _process_tinkoff(inst: dict) -> int:
            figi = inst["figi"]
            ticker = inst["ticker"]
            # PR #124 (2026-09-23): only fetch missing dates, not the
            # figi's full history. The previous code passed
            # ``inst.listed_from`` (often 2014) as from_d, so for a
            # figi missing one day the Tinkoff fallback walked 12 years
            # of history in 7-day chunks before returning. With 714
            # figis in the no-MOEX-board fallback, the cycle never
            # converged. Use ``compute_missing_dates`` to get the actual
            # gap set and ask Tinkoff for only ``[min(missing), yesterday]``.
            # PR #129 (2026-09-24): circuit breaker. If the breaker is
            # open for this figi, short-circuit with one log line and
            # return. Check before doing any other work — even
            # ``compute_missing_dates`` is wasted work when we know
            # Tinkoff has refused this figi 3+ times in a row.
            if await asyncio.to_thread(
                _tinkoff_breaker_is_open, self.db_path, figi,
            ):
                await self._log(
                    "info", figi=figi,
                    message=f"Tinkoff fallback skipped (circuit breaker open) for {ticker}",
                )
                return 0
            try:
                listed_from_iso = (inst.get("listed_from") or "2014-01-01")[:10]
                listed_from_d = date.fromisoformat(listed_from_iso)
            except (TypeError, ValueError):
                listed_from_d = date(2014, 1, 1)
            # If the listing date is suspiciously old (empty-string
            # default path, or older than 2014 — the project's earliest
            # data), bound it so we don't walk pre-data history. The
            # missing-dates set will still correctly identify recent gaps.
            if listed_from_d < date(2014, 1, 1):
                listed_from_d = date(2014, 1, 1)
            missing = compute_missing_dates(
                figi=figi,
                listed_from=listed_from_d,
                yesterday=yesterday,
                db_path=self.db_path,
            )
            if not missing:
                # Already complete through yesterday — skip silently.
                # Returning here avoids the Tinkoff round-trip that
                # would have walked 12 years of history anyway.
                return 0
            # Bound the fetch. `missing` can contain multi-year holes:
            # an instrument whose bars start in 2021 but whose listing
            # floor defaults to 2014, or a foreign security with no
            # local bars at all. Using min(missing) as from_d chunked
            # 12 years of 7-day windows — hundreds of requests, a 45 s
            # timeout, then the circuit breaker, every cycle (observed
            # 2026-10-01: ~1870 foreign instruments stuck stale).
            # Policy: fetch the full gap when it fits the per-figi
            # budget (~60 chunks); otherwise trail only the recent
            # window. Old holes beyond the trailing window are left to
            # a dedicated historical pass, not the daily cycle.
            _TINKOFF_FULL_FETCH_MAX_DAYS = 420  # ~60 seven-day chunks
            _TINKOFF_TRAILING_DAYS = 90
            first_missing = min(missing)
            if (yesterday - first_missing).days > _TINKOFF_FULL_FETCH_MAX_DAYS:
                trailing_cutoff = yesterday - timedelta(days=_TINKOFF_TRAILING_DAYS)
                recent_missing = [d for d in missing if d >= trailing_cutoff]
                if not recent_missing:
                    return 0
                first_missing = min(recent_missing)
            from_d = first_missing
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
                    message=f"Tinkoff fallback timeout after {_figi_timeout_s}s for {ticker} "
                            f"window={from_d}..{yesterday}; skipping",
                )
                await asyncio.to_thread(
                    _tinkoff_breaker_record_failure,
                    self.db_path, figi, _TINKOFF_BREAKER_THRESHOLD,
                )
                return 0
            if not candles:
                await self._log(
                    "warn", figi=figi,
                    message=f"Tinkoff fallback: no data for {ticker} "
                            f"window={from_d}..{yesterday}",
                )
                await asyncio.to_thread(
                    _tinkoff_breaker_record_failure,
                    self.db_path, figi, _TINKOFF_BREAKER_THRESHOLD,
                )
                return 0
            await asyncio.to_thread(
                _tinkoff_breaker_record_success, self.db_path, figi,
            )
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

            # Identity gate (PR #176 follow-up). For a ticker shared by
            # multiple figis (T / DIOD / ROST in production), MOEX
            # serves only the primary board's instrument. Stamping
            # those bars on every figi carrying the ticker cross-
            # pollutes the table. Refuse unless the meta's ISIN
            # matches the figi's stored ISIN. A NULL/empty ISIN on
            # either side short-circuits the write — verification is
            # impossible, so the safe move is to skip and log.
            inst_isin = (inst.get("isin") or "").strip()
            meta_isin = (meta.get("isin") or "").strip()
            if not inst_isin or not meta_isin or inst_isin != meta_isin:
                await self._log(
                    "info", figi=figi,
                    message=f"moex identity mismatch (ticker={ticker} "
                            f"inst_isin={inst_isin!r} meta_isin={meta_isin!r}); "
                            f"skipping",
                )
                return 0

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

        # Prefetch metadata in parallel via asyncio.to_thread. Successful
        # MOEX lookups and confirmed no-board results enter _moex_meta;
        # HTTP failures can remain uncached and may be retried later.
        #
        # Semaphore(16) keeps both queued and active MOEX metadata probes
        # bounded. Keep each slot until its to_thread call actually returns:
        # wait_for cancels the await but cannot stop its HTTP thread, so a
        # timed-out await would release the slot while the request still runs.
        # The underlying requests.get uses (5s connect, 30s read) timeouts.
        # Per-figi self._log gives the structured log incremental progress.
        _prefetch_sem = asyncio.Semaphore(16)
        prefetch_done = 0

        async def _prefetch_meta(inst: dict) -> None:
            nonlocal prefetch_done
            ticker = inst.get("ticker") or ""
            figi = inst.get("figi")
            if not ticker:
                return
            t0 = time.monotonic()
            result = "ok"
            try:
                async with _prefetch_sem:
                    try:
                        await asyncio.to_thread(_get_meta, ticker)
                    except Exception as e:
                        result = f"err:{type(e).__name__}"
            finally:
                prefetch_done += 1
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                # Log every N=50 + first 5 + last 5 so we don't flood
                # ingestion_logs but a stalled prefetch shows up as
                # missing incremental progress. The first and last are
                # always logged.
                if prefetch_done <= 5 or prefetch_done % 50 == 0 \
                        or prefetch_done == len(instruments):
                    await self._log(
                        "info",
                        figi=figi,
                        message=(
                            f"prefetch_meta {result} "
                            f"progress={prefetch_done}/{len(instruments)} "
                            f"elapsed_ms={elapsed_ms}"
                        ),
                    )

        await asyncio.gather(*[_prefetch_meta(inst) for inst in instruments])

        # Outer parallelism:
        # - 5 figis in parallel for MOEX path (cheap, MOEX ISS ~100 req/min per endpoint).
        # - 5 figis in parallel for Tinkoff fallback (sandbox/prod 600 req/min
        #   = 10 RPS, adaptive-retry backoff compounds when concurrent slots
        #   retry together; 5 in flight keeps burst RPS at ~25 which the
        #   token-bucket absorbs).
        # The split prevents rate-limited Tinkoff figis from starving MOEX
        # figis of the shared semaphore.
        _moex_sem = asyncio.Semaphore(5)
        _tinkoff_sem = asyncio.Semaphore(5)

        async def _process_one_bounded(inst: dict) -> int:
            ticker = inst.get("ticker") or ""
            meta = self._moex_meta.get(ticker) if ticker else None
            # A cached board uses the MOEX lane. Unknown/no-board results
            # use the broker lane; a failed MOEX probe may be retried there.
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
          3. Probe MOEX metadata for each ticker (cached in
             ``self._moex_meta``); start fetching each figi as soon as
             its probe completes. The subsequent historical walk reuses
             this cache.
          4. For figis with a primary board, fetch the window via
             ``_fetch_moex_range`` (one HTTP request per touched year;
             for ``days=5`` the window fits inside a single year so
             this is at most 1 request per figi).
          5. INSERT OR IGNORE via ``replace_bars_for_figi(..., replace=False)``,
             so any Tinkoff bars already in the DB are preserved.
          6. Tickers without a MOEX primary board (sanctions-delisted)
             are skipped — Tinkoff is the only path for those and
             this pass explicitly avoids waiting on it.

        At most 16 metadata probes and ``max_concurrency`` figi fetches
        run concurrently; total duration depends on MOEX latency and
        rate limits.

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
        # Populate metadata for the historical walk, allowing a later
        # backfill_from_moex call to reuse cached results too.
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

        # Keep each slot until its synchronous HTTP call actually exits.
        # wait_for(to_thread(...)) would cancel only the coroutine, leaving
        # the HTTP thread running and silently exceeding this 16-call cap.
        probe_sem = asyncio.Semaphore(16)

        async def _probe(inst: dict) -> dict:
            ticker = inst.get("ticker") or ""
            if not ticker:
                return inst
            async with probe_sem:
                try:
                    await asyncio.to_thread(_get_meta, ticker)
                except Exception as exc:
                    await self._log(
                        "warn", figi=inst.get("figi"),
                        message=f"moex_recent_tail metadata probe failed for {ticker}: "
                                f"{type(exc).__name__}",
                    )
            return inst

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
            # Identity gate (PR #176 follow-up). The recent-tail pass
            # routes by ticker too; without this check every figi
            # carrying the same ticker (T / DIOD / ROST in production)
            # would receive the same MOEX bars. Skip unless the meta
            # ISIN matches the figi's stored ISIN — a NULL/empty ISIN
            # on either side also skips (verification impossible).
            inst_isin = (inst.get("isin") or "").strip()
            meta_isin = (meta.get("isin") or "").strip()
            if not inst_isin or not meta_isin or inst_isin != meta_isin:
                await self._log(
                    "info", figi=figi,
                    message=f"moex_recent_tail identity mismatch "
                            f"(ticker={ticker} inst_isin={inst_isin!r} "
                            f"meta_isin={meta_isin!r}); skipping",
                )
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
            # Identity filter: drop rows whose raw SECID/BOARDID disagrees
            # with the ticker/board we asked MOEX for. Closes the
            # cross-listed-mirror path for the recent-tail pass; same
            # data-driven check the same-day script uses (PR176).
            bars = _filter_moex_bars_by_identity(
                bars, ticker=ticker, board=meta["board"],
            )
            for b in bars:
                b["figi"] = figi
            # Persist confirmed zero-trade evidence BEFORE the bar
            # write so a successful tail write that follows the same
            # HTTP response can rely on the same upstream confirmation.
            # Errors here are logged but never abort the bar write:
            # a missing evidence row is "unknown", not a fail-closed
            # block on the bars path. Task 3: pass the explicit
            # ``db_path`` so the evidence lock uses the same
            # namespace as the bar lock.
            try:
                from .no_trade_evidence import (
                    record_no_trade_evidence,
                    _extract_zero_trade_rows,
                )
                from ..db.bars_sqlite import get_connection
                zero_rows = _extract_zero_trade_rows(bars)
                if zero_rows:
                    inst_row = get_connection(self.db_path).execute(
                        "SELECT isin FROM instruments WHERE figi = ?",
                        (figi,),
                    ).fetchone()
                    inst_isin = inst_row["isin"] if inst_row else ""
                    record_no_trade_evidence(
                        get_connection(self.db_path),
                        db_path=self.db_path,
                        figi=figi,
                        rows=zero_rows,
                        board=meta["board"],
                        isin=str(inst_isin or ""),
                    )
            except Exception as e:  # noqa: BLE001 — defensive
                await self._log(
                    "warn", figi=figi,
                    message=f"moex_recent_tail no-trade evidence failed: {e!r}",
                )
            if not bars:
                return 0
            # `replace_bars_for_figi` returns rows-ATTEMPTED, not
            # rows-actually-inserted (with `replace=False` it does
            # INSERT OR IGNORE — duplicates silently skipped, but the
            # return value counts every attempted row). For ops triage
            # we want the real number of new bars added by this pass,
            # so compute the diff between pre- and post-call row count
            # for the figi in the requested window.
            from ..db.bars_sqlite import get_connection
            try:
                _conn = get_connection(self.db_path)
                pre_rows = _conn.execute(
                    "SELECT COUNT(*) FROM bars WHERE figi = ? AND ts BETWEEN ? AND ?",
                    (figi, from_d.isoformat(), yesterday.isoformat()),
                ).fetchone()[0]
            except Exception:
                pre_rows = None  # fall back to attempted count below
            replace_bars_for_figi(
                self.db_path, figi, bars, replace=False, source="moex"
            )
            if pre_rows is None:
                # Could not read pre-count; report attempted rows so
                # the operator at least sees that the pass tried to
                # write something for this figi.
                return len(bars)
            try:
                _conn = get_connection(self.db_path)
                post_rows = _conn.execute(
                    "SELECT COUNT(*) FROM bars WHERE figi = ? AND ts BETWEEN ? AND ?",
                    (figi, from_d.isoformat(), yesterday.isoformat()),
                ).fetchone()[0]
            except Exception:
                return len(bars)
            inserted = max(0, post_rows - pre_rows)
            return inserted

        async def _bounded(inst: dict) -> int:
            async with sem:
                return await _process_one(inst)

        # Begin fetching each figi as soon as its metadata is ready. Waiting
        # for every probe would let one slow board lookup hold up all bars.
        ready: list[dict] = []
        processing: list[asyncio.Task[int]] = []
        for probe in asyncio.as_completed([_probe(inst) for inst in instruments]):
            inst = await probe
            ready.append(inst)
            processing.append(asyncio.create_task(_bounded(inst)))
        results = await asyncio.gather(*processing, return_exceptions=True)
        for inst, res in zip(ready, results):
            if isinstance(res, Exception):
                figi = inst.get("figi", "?")
                ticker = inst.get("ticker", "?")
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
        # Identity gate (PR #176 follow-up). This entry point takes
        # figi+ticker directly (no ``inst`` dict), so resolve the
        # figi's stored ISIN + MOEX's ISIN for the ticker up front and
        # refuse the run on mismatch. Without it the full-history
        # walker would happily stamp the RU bar onto every US mirror
        # figi carrying the ticker (the production cross-pollution
        # the audit flagged).
        from ..db.bars_sqlite import get_connection
        try:
            row = get_connection(self.db_path).execute(
                "SELECT isin FROM instruments WHERE figi = ?", (figi,),
            ).fetchone()
            inst_isin = ((row["isin"] if row else "") or "").strip()
        except Exception:
            inst_isin = ""
        from .no_trade_evidence import fetch_issuer_identity
        ident = fetch_issuer_identity(ticker)
        meta_isin = ((ident.get("isin") or "") if ident else "").strip()
        if not inst_isin or not meta_isin or inst_isin != meta_isin:
            await self._log(
                "info", figi=figi,
                message=f"moex identity mismatch (ticker={ticker} "
                        f"inst_isin={inst_isin!r} meta_isin={meta_isin!r}); "
                        f"skipping",
            )
            return 0
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
                # Identity filter: drop rows whose raw SECID/BOARDID
                # disagrees with the ticker/board we asked MOEX for.
                # Closes the cross-listed-mirror path for the full-history
                # walker; same data-driven check the same-day script
                # uses (PR176). The trailing 9-month Tinkoff block below
                # is by-figi and intentionally untouched.
                candles = _filter_moex_bars_by_identity(
                    candles, ticker=ticker, board="TQBR",
                )
                if candles:
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
                # "Channel is closed" (gRPC INTERNAL_STREAM_CLOSED): the
                # cached AsyncClient handle is dead but the retry
                # decorator's non_rate_error path raises immediately,
                # so the chunk loop would otherwise hammer the dead
                # channel at ~1kHz (observed 2026-09-27 18:15 MSK).
                # Bail out so the worker cycle can return and the
                # supervisor SIGKILL+relaunch triggers a fresh channel
                # in ``RealTinkoffClient.__init__``.
                if "Channel is closed" in err_str or "channel" in err_str.lower():
                    chunks_failed += 1
                    last_chunk_error = err_str
                    await self._log(
                        "warn",
                        figi=figi,
                        message=f"Tinkoff channel closed on chunk {cur}..{chunk_end}; aborting figi (will rebuild next cycle)",
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
        # ``isin`` is selected defensively — the column exists in the
        # production schema (migration 002) but is optional for any
        # custom / minimal schema that hand-rolls the table for unit
        # tests. Wrap the column lookup in a pragma so a missing column
        # surfaces as ``SELECT '' AS isin`` rather than aborting the
        # runner. Identity-gate callers must handle an empty ISIN
        # (they do — empty ISIN means "skip, can't prove").
        from ..db.bars_sqlite import get_connection as _gc
        try:
            has_isin = bool(_gc(self.db_path).execute(
                "SELECT 1 FROM pragma_table_info('instruments') "
                "WHERE name = 'isin'"
            ).fetchone())
        except Exception:
            has_isin = False
        if has_isin:
            sql = sql.replace(
                "SELECT ticker, figi, class",
                "SELECT ticker, figi, class, isin", 1,
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
        # Update supplied broker fields by figi without resetting locally
        # computed coverage, source timestamps, or absent broker metadata.
        # Tickers are not unique: relisted figis may share one ticker.
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                "INSERT INTO instruments "
                "(ticker, figi, class, name, currency, lot_size, isin, sector) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(figi) DO UPDATE SET "
                "ticker=excluded.ticker, class=excluded.class, "
                "name=excluded.name, currency=excluded.currency, "
                "lot_size=excluded.lot_size, "
                "isin=COALESCE(excluded.isin, instruments.isin), "
                "sector=COALESCE(excluded.sector, instruments.sector)",
                (
                    ticker,
                    figi,
                    row.get("class"),
                    name,
                    currency,
                    lot_size,
                    row.get("isin"),
                    row.get("sector"),
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


# ─── bond depth backfill (coverage-and-quality PR-1) ─────────────────
#
# For every tradable bond figi with fewer than ``target_days`` bars in
# the local ``bars`` table, fetch history from Tinkoff until the target
# is reached (or the broker has nothing more). Idempotent: re-running
# with the same broker response does not duplicate rows — we look up
# (figi, ts) before each INSERT and skip existing pairs.
#
# ADAPT-1: The brief's reference implementation calls
# ``client.get_historical_bonds(figi, from_date, to_date)``. The current
# ``TinkoffClient`` Protocol in ``client.py`` exposes ``get_candles``
# but not ``get_historical_bonds``. We follow the brief verbatim so the
# test contract (which mocks ``mock_client.get_historical_bonds``)
# matches the production call site. The Protocol is
# ``@runtime_checkable`` and missing methods on a real client surface
# only at call time, so this stays forward-compatible — when the real
# Tinkoff wrapper grows a ``get_historical_bonds`` method, the depth
# backfill lights up without further changes here.


async def _async_backfill_impl(
    target_days: int = 30,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Inner async body of backfill_bonds_to_depth.

    Extracted so the public sync entry point can asyncio.run() it.
    Resolves the sync/async rate-limit bypass from PR-1 (ADAPT-4) by
    properly awaiting ``rl.acquire("get_historical_bonds")`` here in
    the async context. Existing sync tests still pass because they
    patch ``acquire`` with a MagicMock that returns non-awaitable.

    Writer-coordination (Task 2, SDD brief):
    * No raw ``INSERT INTO bars`` / ``conn.commit()`` remains on the
      path. Missing candles for each FIGI are filtered out of the
      fetched batch (a single batched ``SELECT figi, ts FROM bars
      WHERE figi = ? AND ts IN (...)`` outside the lock), deduped
      in-batch, and the remaining missing candles are handed to the
      coordinated common writer
      ``replace_bars_for_figi(..., replace=False, source="tinkoff")``
      which owns ``<db>.writer.lock`` for the bar transaction.
      ``bars_added`` and the per-FIGI log ``after`` count ONLY the
      newly-inserted rows (the original raw-INSERT semantic).
    * The file-backed DB path is resolved explicitly:
      - When ``conn`` is None (production worker entry), use
        ``get_settings().sqlite_path`` directly.
      - When a ``conn`` is injected (tests, manual backfill),
        read ``PRAGMA database_list`` to get the on-disk main
        database path. The production writer path
        (``replace_bars_for_figi``) needs a real file so the
        shared writer lock can be opened; reject ``:memory:``
        loudly instead of silently degrading the contract.
    * Per-FIGI ``figis_processed`` / ``skipped`` / ``errors``
      counters are preserved so callers and operators see the
      same accounting as before.
    """
    from algotrader_api.db.bars_sqlite import (
        replace_bars_for_figi_with_rowcount,
    )

    if conn is None:
        # Production path: derive the path from settings and open
        # the shared connection. The writer lock uses this same
        # path so the kernel-level namespace matches.
        from algotrader_api.config import get_settings
        from algotrader_api.db.sqlite import get_connection
        sqlite_path = get_settings().sqlite_path
        conn = get_connection(sqlite_path)
    else:
        # A connection was injected. The bar writer needs a
        # file-backed path (the shared ``<db>.writer.lock`` and
        # the SQLite WAL depend on it). Read ``PRAGMA database_list``
        # to learn the on-disk path; reject ``:memory:`` loudly.
        sqlite_path = _resolve_db_path_from_connection(conn)

    # Find all tradable bond figis
    rows = conn.execute(
        "SELECT figi, ticker FROM instruments WHERE class='bond'"
    ).fetchall()
    figis_processed = 0
    bars_added = 0
    skipped = 0
    errors = 0

    # Lazy imports to avoid circular deps at module load
    from algotrader_api.ingestion.rate_limit import get_global
    rl = get_global()

    for figi, ticker in rows:
        try:
            current = conn.execute(
                "SELECT COUNT(*) FROM bars WHERE figi = ?", (figi,)
            ).fetchone()[0]
            if current >= target_days:
                skipped += 1
                continue

            figis_processed += 1
            from_date = (date.today() - timedelta(days=target_days * 2)).isoformat()
            to_date = date.today().isoformat()

            # Use the existing Tinkoff client (production target)
            from algotrader_api.ingestion.client import make_client
            # Forward the already-resolved file-backed ``sqlite_path``
            # so the token lookup uses the same app-local DB the
            # writer path uses. Without this, ``make_client`` falls
            # back to ``ALGOTRADER_SQLITE_PATH`` which the worker
            # process never exports — production smoke run on
            # 2026-10-03 logged ``tinkoff.token.sqlite_path_unset``
            # and the bonds_depth step returned processed=74,
            # errors=0, bars_added=0.
            client = make_client(sqlite_path=sqlite_path)
            # Per-FIGI client lifecycle (fix/bond-client-lifecycle):
            # the gRPC channel built by ``make_client`` is owned
            # by this loop iteration. The code-owned defect was
            # the missing ``aclose`` — the per-FIGI client
            # lifetime was unbounded. The per-step figi count
            # and any downstream effect on the
            # connection-tracking surface were not measured as
            # part of this fix and are not asserted here. The
            # close is bounded: ``try/finally`` wraps ONLY
            # ``rl.acquire`` and ``get_candles`` so the prefilter
            # + bar write stay outside the close call. A close
            # failure is logged but never masks the fetch error
            # or the per-FIGI ``bars_added`` accounting.
            try:
                # ADAPT-4: was a sync rl.acquire() call that silently bypassed
                # the AsyncLimiter. Now properly awaited in async context so
                # Tinkoff's 600 req/min cap is honored on bonds backfill.
                #
                # autonomous-data-pipeline Task B.1: call get_candles (the
                # method that exists on RealTinkoffClient) — not the
                # non-existent get_historical_bonds. The previous call site
                # was unreachable in production because RealTinkoffClient has
                # only get_candles (see coverage-and-quality ADAPT-1).
                await rl.acquire("get_candles")
                candles = await client.get_candles(
                    figi=figi, date_from=from_date, date_to=to_date
                )
            finally:
                # Best-effort close. ``RealTinkoffClient.aclose`` is
                # already bounded by
                # ``BOND_CLIENT_CLOSE_TIMEOUT_SECONDS``; an older
                # double without ``aclose`` (Protocol-only) is
                # allowed — duck-test via ``getattr`` and skip the
                # call. A close failure is logged with bounded
                # metadata (no token, no password) so a real
                # production outage shows up without leaking
                # secrets: only the exception type name is
                # recorded, never the message (gRPC trailer
                # strings routinely embed the bearer token, the
                # resolved sqlite_path, and HTTP/2 trailer
                # bytes).
                #
                # Observability: log-only. The close-failure
                # path is intentionally NOT surfaced via the
                # ``errors`` counter (which counts fetch and
                # insertion failures) and there is no public
                # ``aclose_errors`` metric. The reasoning: a
                # close that times out or raises after a
                # successful fetch does not represent data loss
                # or a missed obligation — the candles are
                # already written, the gRPC channel is
                # ephemeral, and the next iteration rebuilds
                # the channel via ``make_client``. The
                # structured log is the only signal. Operators
                # alert on the log event name
                # (``bond_depth_client_close_failed``), not on
                # a counter.
                aclose = getattr(client, "aclose", None)
                if aclose is not None:
                    try:
                        await aclose()
                    except Exception as close_exc:  # noqa: BLE001 — best-effort cleanup
                        logger.warning(
                            "bond_depth_client_close_failed",
                            extra={
                                "event": "bond_depth_client_close_failed",
                                "figi": figi,
                                "ticker": ticker,
                                "client_type": type(client).__name__,
                                "error": type(close_exc).__name__,
                            },
                        )
            if not candles:
                # Nothing to write; record the no-op in the
                # ``bars_added`` accounting so operators see the
                # figi was processed (consistent with the prior
                # raw-INSERT path which also added zero here).
                logger.info(
                    "bond_depth_backfill",
                    extra={
                        "event": "bond_depth_backfill",
                        "figi": figi,
                        "ticker": ticker,
                        "before": current,
                        "after": current,
                        "added": 0,
                    },
                )
                continue

            # Pre-filter the fetched batch to ONLY the candles that
            # are actually missing from the DB. The original raw-INSERT
            # path counted only newly-inserted rows in
            # ``bars_added`` / log ``after``; restoring that accounting
            # here is the Task 2 brief's "Restore pre-write
            # missing-candle filtering" requirement. Filtering runs
            # outside the lock (it is a read-only DB query + a Python
            # set diff) so the writer-lock window stays bounded to
            # the bar transaction only.
            #
            # Steps:
            #   1. Normalize every fetched candle into
            #      ``(c_figi, c_ts)`` plus the original candle for
            #      re-use; dedupe in-batch by ``(c_figi, c_ts)`` so
            #      the broker never double-counts a repeated key.
            #   2. Query the DB once for the existing keys (single
            #      batched SELECT) — outside the lock.
            #   3. Hand only the missing candles to the coordinated
            #      common writer. ``replace_bars_for_figi`` returns
            #      the number of rows actually inserted, which is
            #      the canonical "added" count.
            seen_keys: set[tuple[str, str]] = set()
            ordered: list[tuple[tuple[str, str], object]] = []
            for c in candles:
                if isinstance(c, dict):
                    c_figi = c.get("figi") or figi
                    c_ts = c.get("ts")
                else:
                    c_figi = getattr(c, "figi", None) or figi
                    c_ts = getattr(c, "ts", None)
                if c_ts is None:
                    # No timestamp → nothing we can dedupe or
                    # write; drop silently (matches the original
                    # behavior of "no ts → no row written").
                    continue
                key = (c_figi, c_ts)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                ordered.append((key, c))

            if not ordered:
                # No usable keys in the fetched batch (all were
                # missing ``ts``). Mirror the empty-candles log
                # line above so operators see the figi was
                # processed and nothing was added.
                logger.info(
                    "bond_depth_backfill",
                    extra={
                        "event": "bond_depth_backfill",
                        "figi": figi,
                        "ticker": ticker,
                        "before": current,
                        "after": current,
                        "added": 0,
                    },
                )
                continue

            # Single bounded SELECT for all (figi, ts) pairs in
            # this figi's batch — one query, no IN placeholder
            # expansion. SQLite caps the number of bound
            # parameters per statement (default 999 / 32766
            # depending on the build); an unbounded
            # ``WHERE ts IN (?, ?, ...)`` over a huge broker
            # batch trips ``too many SQL variables`` and the
            # whole backfill dies. The bounded path reads the
            # stored (figi, ts) set ONCE for this loop figi
            # and filters in Python. The DB is read-only here;
            # no lock is held.
            existing_rows = conn.execute(
                "SELECT ts FROM bars WHERE figi = ?",
                (figi,),
            ).fetchall()
            existing_keys = {(r[0],) for r in existing_rows}
            missing = [candle for key, candle in ordered if key not in existing_keys]

            if not missing:
                # Every fetched candle is already in the DB. Log
                # ``added=0`` and continue — the per-FIGI accounting
                # reflects the original "we wrote zero new rows"
                # semantic.
                logger.info(
                    "bond_depth_backfill",
                    extra={
                        "event": "bond_depth_backfill",
                        "figi": figi,
                        "ticker": ticker,
                        "before": current,
                        "after": current,
                        "added": 0,
                    },
                )
                continue

            # Delegate the bar write to the coordinated common
            # writer. It owns ``<db>.writer.lock`` for the bar
            # transaction and re-uses the existing
            # ``replace=False, source='tinkoff'`` semantic. The
            # ``_with_rowcount`` variant returns the actual
            # ``executemany`` cursor ``rowcount`` — the public
            # ``replace_bars_for_figi`` preserves the legacy
            # ``len(normalized rows)`` contract, which is wrong
            # for this path: the prefilter is not proof a
            # concurrent writer did not insert the same key
            # between the SELECT and the locked INSERT, and the
            # brief requires ``bars_added`` count only what the
            # locked ``executemany`` actually committed. Counts
            # only ``bars`` insertions — never the
            # ``instrument_metadata`` aggregate or the
            # reconciliation hook.
            added_this_figi = replace_bars_for_figi_with_rowcount(
                sqlite_path,
                figi,
                missing,
                replace=False,
                source="tinkoff",
            )
            bars_added += added_this_figi
            logger.info(
                "bond_depth_backfill",
                extra={
                    "event": "bond_depth_backfill",
                    "figi": figi,
                    "ticker": ticker,
                    "before": current,
                    "after": current + added_this_figi,
                    "added": added_this_figi,
                },
            )
        except Exception as e:
            errors += 1
            logger.warning(
                "bond_depth_backfill_error",
                extra={
                    "event": "bond_depth_backfill_error",
                    "figi": figi,
                    "error": str(e)[:200],
                },
            )
            # do not raise — continue with next figi
            continue

    return {
        "figis_processed": figis_processed,
        "bars_added": bars_added,
        "skipped": skipped,
        "errors": errors,
    }


def _resolve_db_path_from_connection(conn: sqlite3.Connection) -> str:
    """Return the file-backed main database path of ``conn``.

    The production bar writer (``replace_bars_for_figi``) needs a
    real file to open ``<db>.writer.lock`` and to run SQLite WAL.
    ``PRAGMA database_list`` returns one row per attached database
    with the form ``(seq, name, file)``; the first row is the main
    database. If the main database is ``:memory:`` (or empty)
    the function raises :class:`RuntimeError` so the caller can
    surface a fail-closed diagnostic instead of silently
    degrading the bar write contract.

    Tests that need to drive the public function with an
    injected connection must use a file-backed temp DB (the
    existing ``test_backfill_bonds_to_depth`` fixture already
    does this). New code should never pass ``:memory:`` here.
    """
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except Exception as exc:  # noqa: BLE001 — defensive: surface as RuntimeError
        raise RuntimeError(
            f"cannot read PRAGMA database_list from injected connection: {exc!r}"
        ) from exc
    if not rows:
        raise RuntimeError(
            "injected connection has no PRAGMA database_list rows; "
            "cannot resolve file-backed path for the bar writer"
        )
    # First row is the main database. Support both row access
    # patterns (sqlite3.Row tuple and bare tuple) so we work
    # with whatever the caller configured on the connection.
    first = rows[0]
    try:
        # sqlite3.Row supports both index and key access.
        file_path = first["file"]
    except (KeyError, TypeError, IndexError):
        try:
            file_path = first[2]
        except (IndexError, TypeError):
            raise RuntimeError(
                f"unexpected PRAGMA database_list row shape: {first!r}"
            )
    file_path = (file_path or "").strip()
    if not file_path or file_path == ":memory:":
        raise RuntimeError(
            "production bar writer path requires a file-backed SQLite "
            "database; injected connection is :memory: (the shared "
            "<db>.writer.lock cannot be opened on an in-memory DB). "
            "Open a temp file-backed DB or call without ``conn=`` so "
            "settings.sqlite_path is used."
        )
    return file_path


def backfill_bonds_to_depth(
    target_days: int = 30,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Public sync entry point for the bond depth backfill.

    For each tradable bond figi with < target_days bars, fetch historical
    bars from Tinkoff until target reached or broker history exhausted.

    Returns: {figis_processed: int, bars_added: int, skipped: int, errors: int}

    ADAPT-5 Option B: the body runs in an asyncio.run() loop so that
    ``rl.acquire("get_historical_bonds")`` inside the inner async impl
    properly awaits the AsyncLimiter. The sync signature is preserved so
    existing callers (worker.py step, tests) work unchanged; per-call
    loop setup/teardown is ~1 ms, negligible vs the backfill work.
    """
    return asyncio.run(_async_backfill_impl(target_days, conn))
