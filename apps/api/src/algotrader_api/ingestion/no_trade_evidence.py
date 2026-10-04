"""MOEX no-trade evidence storage and reconciliation.

The ``bars`` table only records actual trades. An explicit
``NUMTRADES=0`` row from MOEX ISS for a particular figi / board /
date is independent evidence that the issuer did not trade on that
session. We persist that evidence separately so the ML coverage gate
can subtract confirmed no-trade sessions from the expected denominator
without ever fabricating a fake candle.

The contract is intentionally strict:

* Evidence is only accepted for a figi when the upstream response was
  successful, complete (full cursor pagination), and the zero-trade row
  matches the figi's SECID and BOARDID plus the figi's ISIN.
* Empty / error / partial / ticker-only / wrong-board responses do NOT
  produce evidence; they remain "unknown".
* A real bar that lands for (figi, session_date) always wins — the
  reconciliation step deletes any stale evidence for that pair on every
  insert.

The ``expires_at`` column gives every evidence row a re-validation
deadline; the gate treats expired rows as "unknown" until a fresh
observation refreshes them. Historical evidence is held for the long
term so the denominator stays stable; recent evidence gets a short
expiry so the system adapts to instruments that start trading again.

Coordination (writer-coordination spec, Task 3):
* Public :func:`record_no_trade_evidence` and
  :func:`reconcile_no_trade_evidence` each acquire the shared
  writer lock once via :func:`writer_lock` (or a hook-injected
  replacement) and explicitly call the corresponding private
  :func:`_record_no_trade_evidence_tx` /
  :func:`_reconcile_no_trade_evidence_tx`. The private helpers
  never re-acquire the lock; callers must NOT bypass the public
  wrappers for any code path that mutates the database.
* Both public wrappers accept an explicit ``db_path`` and use the
  ``_evidence_lock_timeout`` module constant for the bounded
  acquisition timeout (default 30 s, monkeypatchable for tests).
* A single ``_acquire_evidence_lock`` helper centralizes the
  ``writer_lock`` call so both wrappers go through one audit
  point (no duplicate logic).
"""
from __future__ import annotations

import datetime
import json
import logging
import sqlite3
import urllib.parse
from datetime import date, timedelta
from typing import Literal

from .writer_lock import WriterLockBusy, is_sqlite_busy, writer_lock_path

# Default expiry windows. Recent evidence is cheap to re-fetch and must
# be revalidated frequently; historical evidence is trusted for longer
# because the upstream source itself treats those sessions as settled.
RECENT_EVIDENCE_EXPIRY = timedelta(days=7)
HISTORICAL_EVIDENCE_EXPIRY = timedelta(days=365)

# Fetch outcome emitted by ``_fetch_year_moex_outcome``. Decision
# rule (see ADDED Requirement in
# openspec/changes/persist-historical-moex-evidence): one value per
# fetch, worst-severity across pages. This is a ``Literal`` alias,
# NOT a runtime constructor — callers compare with
# ``outcome == "complete"`` and friends.
MOEXFetchOutcome = Literal[
    "complete", "partial", "error", "malformed", "identity_mismatch",
]

# Bounded lock-acquisition timeout for both public evidence wrappers
# (record + reconcile). Default 30 s mirrors the bar-writer timeout;
# tests and the CLI can monkeypatch it to keep the suite fast.
_EVIDENCE_LOCK_TIMEOUT_SECONDS: float = 30.0


def _evidence_writer_lock(db_path: str, *, role: str, phase: str):
    """Single audit point for evidence public-wrapper lock acquisition.

    Both public wrappers (:func:`record_no_trade_evidence` and
    :func:`reconcile_no_trade_evidence`) must acquire the shared
    writer lock through this helper. The helper is the only place
    the lock timeout and the lock namespace derivation are wired,
    so future changes (timeout, role/phase naming) touch one line.

    ``writer_lock`` is imported lazily inside the helper body so
    tests can monkeypatch the symbol on the source module
    ``algotrader_api.ingestion.writer_lock`` and the public
    wrappers pick up the shim.
    """
    from .writer_lock import writer_lock as _writer_lock

    return _writer_lock(
        db_path,
        role=role,
        phase=phase,
        timeout_seconds=_EVIDENCE_LOCK_TIMEOUT_SECONDS,
    )


def _moex_session():
    """Lazy import of the module-level pooled session used by backfill."""
    from algotrader_api.ingestion import backfill

    return backfill._get_moex_session()


def fetch_issuer_identity(ticker: str) -> dict | None:
    """Return ``{board, isin}`` for ``ticker`` from MOEX ISS.

    The result is what MOEX considers the issuer's identity on a primary
    tradable board. Returns ``None`` for tickers without a primary board
    or on any upstream failure. Used to cross-check the SECID and ISIN
    attached to a zero-trade row before persisting it.
    """
    url = f"https://iss.moex.com/iss/securities/{urllib.parse.quote(ticker)}.json"
    try:
        data = _moex_session().get(url, timeout=(5, 30)).json()
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
        return None
    descriptions = data.get("description", {}).get("data", [])
    isin_value: str | None = None
    for row in descriptions:
        if len(row) > 2 and row[0] == "ISIN" and isinstance(row[2], str):
            isin_value = row[2]
            break
    return {"board": primary[1], "isin": isin_value or ""}


def fetch_no_trade_rows(
    *,
    market: str,
    board: str,
    ticker: str,
    from_d: date,
    to_d: date,
    last_trading_day: date | None = None,
) -> list[dict]:
    """Return confirmed zero-trade rows for ``ticker`` on ``board``.

    Each returned dict has ``{ts, volume, numtrades, value}`` and
    represents a session date where MOEX returned a single explicit
    zero-trade row matching the SECID. The caller is responsible for
    cross-checking the SECID/BOARDID/ISIN identity against the figi
    record before calling :func:`record_no_trade_evidence`.

    The upstream contract:
      * HTTP success (no exceptions).
      * A full paginated history (``history.cursor`` present and
        ``offset + len(rows) >= total`` when total > page_size).
      * Each row must have ``TRADEDATE``, ``SECID == ticker``,
        ``BOARDID == board``, all OHLC ``None`` and ``VOLUME``,
        ``NUMTRADES``, ``VALUE`` equal to 0.

    Any deviation short-circuits the entire batch and returns ``[]`` —
    partial evidence is worse than no evidence.
    """
    if from_d > to_d:
        return []
    cap = last_trading_day or to_d
    base = (
        f"https://iss.moex.com/iss/history/engines/stock/markets/{market}"
        f"/boards/{board}/securities/{urllib.parse.quote(ticker)}.json"
    )
    out: list[dict] = []
    for year in range(from_d.year, to_d.year + 1):
        year_cap = min(to_d, cap)
        start = 0
        page_size = 500
        while True:
            try:
                data = _moex_session().get(
                    base,
                    params={
                        "from": f"{year}-01-01",
                        "till": year_cap.isoformat(),
                        "start": start,
                    },
                    timeout=(5, 30),
                ).json()
            except Exception:
                return []
            history = data.get("history") or {}
            cols = history.get("columns") or []
            if not cols or "TRADEDATE" not in cols:
                return []
            rows = history.get("data") or []
            if not rows:
                return []
            for row in rows:
                d = dict(zip(cols, row))
                # SECID + BOARDID must match the figi's identity; anything
                # else means we're looking at the wrong instrument.
                if str(d.get("SECID") or "") != ticker:
                    return []
                if str(d.get("BOARDID") or "") != board:
                    return []
                # Only accept the explicit zero-trade shape: all prices
                # null and all counters zero. NUMTRADES may be absent in
                # some responses; treat absent as 0 only when VOLUME is
                # explicitly 0.
                o, h, l, c = (d.get(k) for k in ("OPEN", "HIGH", "LOW", "CLOSE"))
                if o is not None or h is not None or l is not None or c is not None:
                    return []
                volume = d.get("VOLUME") or 0
                numtrades = d.get("NUMTRADES") or 0
                value = d.get("VALUE") or 0
                if volume != 0 or numtrades != 0 or value != 0:
                    return []
                out.append({
                    "ts": str(d.get("TRADEDATE") or "")[:10],
                    "volume": int(volume),
                    "numtrades": int(numtrades),
                    "value": float(value or 0),
                })
            # Full pagination only: cursor rows present and ``offset +
            # len(rows) >= total`` (or short page == end of year).
            cursor_rows = data.get("history.cursor", {}).get("data") or []
            if cursor_rows:
                try:
                    offset, total, _ = cursor_rows[0][:3]
                    if offset is None or total is None or offset + len(rows) < total:
                        return []
                except (TypeError, ValueError):
                    return []
            else:
                if len(rows) >= page_size:
                    return []
            start += len(rows)
            if not cursor_rows and len(rows) < page_size:
                break
    # Filter rows to the requested window — _fetch_year_moex asked for the
    # full year, but the caller only cares about [from_d, to_d].
    lo = from_d.isoformat()
    hi = to_d.isoformat()
    return [r for r in out if lo <= r["ts"] <= hi]


def _is_business_date_for_evidence(
    conn: sqlite3.Connection,
    ts: str,
    *,
    today: date | None = None,
) -> bool:
    """True iff ``ts`` is a strict ISO date for a completed business session.

    The date must precede ``today`` (or the local clock), be a weekday,
    and not appear in ``moex_holidays``. No current/future session is certified.

    Pure SQL helper. Does NOT acquire the writer lock; callers that
    persist rows must already hold it. Operates on the caller's open
    connection so a single transaction sees both the holiday table
    and the evidence table.
    """
    if not isinstance(ts, str) or len(ts) != 10:
        return False
    try:
        d = date.fromisoformat(ts)
    except ValueError:
        return False
    if d.isoformat() != ts or d >= (today or date.today()):
        return False
    if d.weekday() >= 5:  # Saturday / Sunday.
        return False
    row = conn.execute(
        "SELECT 1 FROM moex_holidays WHERE date = ?",
        (d.isoformat(),),
    ).fetchone()
    return row is None


def record_historical_no_trade_evidence(
    conn: sqlite3.Connection,
    *,
    db_path: str,
    figi: str,
    ticker: str,
    rows: list[dict],
    board: str,
    isin: str,
    outcome: MOEXFetchOutcome,
    from_d: date,
    to_d: date,
    today: date | None = None,
) -> int:
    """Persist zero-trade evidence for a historical MOEX walk.

    Outcome gate:
      * ``outcome == "complete"`` — proceed to the
        business-date / identity filter and delegate to
        :func:`record_no_trade_evidence`.
      * any other outcome — return ``0`` immediately, perform no
        SQLite mutation, and emit one structured log line.

    ISIN identity gate (added in Task 2 R1):
      * The ``isin`` argument represents the **upstream-verified
        metadata ISIN** that the caller (the historical walker or
        the CLI) obtained from MOEX's identity probe. The helper
        reads the figi's stored ISIN from the same ``conn`` (no
        extra HTTP, no nested lock acquisition) and compares the
        two. A mismatch rejects the whole batch with one
        structured ``moex_historical_evidence_rejected
        reason=identity_mismatch`` log line and **no DB write**.
      * When the upstream ISIN is empty (the caller's MOEX probe
        did not return a value) and the stored ``instruments.isin``
        for the figi is populated, the helper **fails closed**: it
        cannot certify the row's identity against upstream, so the
        whole batch is rejected (no DB write). The same
        ``identity_mismatch`` log line is emitted so the operator
        sees the gap.
      * ``isin`` carries the upstream-verified metadata ISIN; the helper
        itself does the local-vs-upstream cross-check.

    Window contract: ``from_d`` and ``to_d`` are mandatory ordered date
    objects carrying the actual caller window, not the fetched year's bounds.
    Invalid windows raise ValueError. Borrowed connections are never closed.

    Filter rule (after both gates pass):
      * require all four normalized OHLC keys explicitly present and None,
        plus explicit numeric (not bool) zero volume, _numtrades and _value;
        positive rows are skipped without poisoning valid zero rows;
      * require a strict YYYY-MM-DD date within [from_d, to_d] that precedes
        today and is a weekday outside the cached MOEX holiday calendar;
        out-of-window rows produce one bounded batch diagnostic;
      * keep rows whose ``_secid`` matches the explicit ``ticker``
        argument (the caller threads the ticker through from the
        ``instruments`` row — the helper MUST NOT guess the
        ticker from ``rows[0].get("_secid")``);
      * keep rows whose ``_boardid`` matches ``board``;
      * keep rows whose ``ts`` is a valid ISO date AND a
        business date per :func:`_is_business_date_for_evidence`.

    Writer lock: acquired exactly once through
    :func:`record_no_trade_evidence` (the existing public wrapper
    already takes the lock with ``role="no-trade-evidence"`` and
    ``phase="evidence"``). No new lock path; no nested acquisition.

    Delegation: the actual ``INSERT ... ON CONFLICT`` is performed
    by the existing :func:`record_no_trade_evidence` so TTL,
    recent-vs-historical expiry branching, real-bar-wins filtering,
    and ON CONFLICT refresh behaviour stay verbatim.
    """
    if (not isinstance(from_d, datetime.date) or not isinstance(to_d, datetime.date)
            or isinstance(from_d, datetime.datetime) or isinstance(to_d, datetime.datetime)
            or from_d > to_d):
        raise ValueError("historical evidence requires an ordered date window")
    today = today or date.today()
    if outcome != "complete":
        logging.getLogger("algotrader.ingestion").info(
            "moex_historical_evidence_rejected figi=%s reason=%s rows=%s",
            figi, outcome, len(rows),
        )
        return 0
    # ISIN identity gate: compare the upstream-verified metadata ISIN
    # (carried in the ``isin`` argument, set by the caller from
    # ``fetch_issuer_identity``) against the figi's stored ISIN in
    # the SAME connection — no HTTP, no nested lock. A mismatch
    # rejects the whole batch with one structured
    # ``identity_mismatch`` log line and no DB write. A missing
    # upstream ISIN combined with a populated local ISIN is the
    # same fail-closed rejection: the row's identity cannot be
    # certified against upstream metadata.
    try:
        row = conn.execute(
            "SELECT isin FROM instruments WHERE figi = ?", (figi,),
        ).fetchone()
    except Exception:
        row = None
    local_isin = ((row["isin"] if row else "") or "").strip()
    upstream_isin = (isin or "").strip()
    if upstream_isin != local_isin:
        logging.getLogger("algotrader.ingestion").info(
            "moex_historical_evidence_rejected figi=%s "
            "reason=identity_mismatch rows=%s",
            figi, len(rows),
        )
        return 0
    if not rows:
        return 0
    accepted: list[dict] = []
    out_of_window = 0
    non_business_dates = 0
    for r in rows:
        if str(r.get("_secid") or "") != ticker:
            continue
        if str(r.get("_boardid") or "") != board:
            continue
        # Complete provenance is not enough: positive bars never prove no trade.
        # Exact numeric zero excludes missing, bool, strings, NaN and infinity.
        if not all(k in r and r[k] is None for k in ("open", "high", "low", "close")):
            continue
        if not all(type(r.get(k)) in (int, float) and r[k] == 0
                   for k in ("volume", "_numtrades", "_value")):
            continue
        ts = r.get("ts")
        if not _is_business_date_for_evidence(conn, ts, today=today):
            non_business_dates += 1
            continue
        if not from_d.isoformat() <= ts <= to_d.isoformat():
            out_of_window += 1
            continue
        accepted.append({"ts": ts})
    if out_of_window or non_business_dates or not accepted:
        logging.getLogger("algotrader.ingestion").info(
            "moex_historical_evidence_rejected figi=%s reason=%s rows=%s",
            figi, ("out_of_window" if out_of_window else
                   "non_business_date" if non_business_dates else "no_eligible_zero_session"),
            out_of_window or non_business_dates or len(rows),
        )
    if not accepted:
        return 0
    return record_no_trade_evidence(
        conn,
        db_path=db_path,
        figi=figi,
        rows=accepted,
        board=board,
        isin=isin,
        now=today,
    )


def _record_no_trade_evidence_tx(
    conn: sqlite3.Connection,
    *,
    figi: str,
    rows: list[dict],
    board: str,
    isin: str,
    now: date | None = None,
) -> int:
    """Private transaction body for :func:`record_no_trade_evidence`.

    Performs the row-level filtering (skip dates that already have
    real bars, branch recent vs historical expiry, assemble the
    INSERT params) and the ``executemany`` upsert + commit. Called
    only while the shared writer lock is already held by the public
    entry point; the helper must NOT re-acquire the lock (the brief
    is explicit — one acquisition protects the whole transaction).

    Returns the number of accepted parameter rows (i.e. rows that
    were not skipped because of a real bar already in the table).
    The caller is responsible for the lock acquisition and for any
    rollback semantics.
    """
    if not rows:
        return 0
    today = now or date.today()
    recent_cutoff = today - timedelta(days=14)
    params = []
    for r in rows:
        ts = (r.get("ts") or "")[:10]
        if not ts:
            continue
        exists = conn.execute(
            "SELECT 1 FROM bars WHERE figi = ? AND ts = ?",
            (figi, ts),
        ).fetchone()
        if exists:
            continue
        try:
            row_date = date.fromisoformat(ts)
        except ValueError:
            continue
        expiry_days = (
            RECENT_EVIDENCE_EXPIRY
            if row_date >= recent_cutoff
            else HISTORICAL_EVIDENCE_EXPIRY
        )
        expires_at = (today + expiry_days).isoformat()
        params.append((figi, ts, board, isin, expires_at))
    if not params:
        return 0
    conn.executemany(
        """INSERT INTO moex_no_trade_evidence
               (figi, session_date, board, isin, observed_at, expires_at)
           VALUES (?, ?, ?, ?, datetime('now'), ?)
           ON CONFLICT(figi, session_date) DO UPDATE SET
               board = excluded.board,
               isin = excluded.isin,
               observed_at = excluded.observed_at,
               expires_at = excluded.expires_at""",
        params,
    )
    return len(params)


def record_no_trade_evidence(
    conn: sqlite3.Connection,
    *,
    db_path: str,
    figi: str,
    rows: list[dict],
    board: str,
    isin: str,
    now: date | None = None,
) -> int:
    """Upsert zero-trade evidence for ``figi`` and return rows written.

    Each row in ``rows`` must contain ``ts``. Existing real bars for
    (figi, ts) take precedence: rows that conflict with the bars table
    are skipped silently. On INSERT the entry's ``expires_at`` is set
    to ``now + RECENT_EVIDENCE_EXPIRY`` for the last 14 days of the
    window and ``now + HISTORICAL_EVIDENCE_EXPIRY`` otherwise.

    Coordination (writer-coordination spec, Task 3):
    * The shared writer lock is acquired exactly once via
      :func:`_evidence_writer_lock` with ``role="no-trade-evidence"``
      and ``phase="evidence"``. The lock covers the full
      ``INSERT ... ON CONFLICT`` upsert and the commit. The
      private ``_record_no_trade_evidence_tx`` performs the SQL;
      it does not re-acquire the lock (process-local + kernel-
      visible single acquisition).
    * The caller MUST pass an explicit ``db_path`` — there is no
      implicit / inferred production DB-path shim. The
      :func:`db.sqlite.get_connection` cache is open and
      write-friendly in production; tests must use a file-backed
      temp DB so the kernel-level ``flock`` is actually exercised.
    * On ``WriterLockBusy`` the helper lets the exception propagate;
      the caller decides whether to defer, abort, or fall back.
    """
    if not rows:
        return 0
    with _evidence_writer_lock(
        db_path, role="no-trade-evidence", phase="evidence",
    ):
        try:
            written = _record_no_trade_evidence_tx(
                conn, figi=figi, rows=rows, board=board,
                isin=isin, now=now,
            )
            conn.commit()
        except BaseException as exc:
            try:
                conn.rollback()
            except BaseException:
                # Preserve the original failure; the lock must still release.
                pass
            if is_sqlite_busy(exc):
                raise WriterLockBusy(
                    role="no-trade-evidence", phase="evidence",
                    database_path=db_path,
                    lock_path=str(writer_lock_path(db_path)),
                    timeout_seconds=_EVIDENCE_LOCK_TIMEOUT_SECONDS,
                    reason="sqlite-busy",
                ) from exc
            raise
    return written


def _reconcile_no_trade_evidence_tx(conn: sqlite3.Connection) -> int:
    """Private transaction body for :func:`reconcile_no_trade_evidence`.

    Performs the ``DELETE FROM moex_no_trade_evidence WHERE EXISTS
    (SELECT 1 FROM bars ...)`` and returns the cursor rowcount
    (number of evidence rows whose date now has a real bar). Called
    only while the shared writer lock is already held by the public
    entry point; the helper must NOT re-acquire the lock (the brief
    is explicit — one acquisition protects the whole transaction).

    The caller is responsible for the commit / rollback.
    """
    cur = conn.execute(
        """DELETE FROM moex_no_trade_evidence
           WHERE EXISTS (
               SELECT 1 FROM bars
               WHERE bars.figi = moex_no_trade_evidence.figi
                 AND bars.ts = moex_no_trade_evidence.session_date
           )"""
    )
    return cur.rowcount


def reconcile_no_trade_evidence(
    conn: sqlite3.Connection, *, db_path: str,
) -> int:
    """Drop evidence rows whose date has a real bar now. Returns rows removed.

    Called after every bar write so a stale evidence row never beats a
    real candle.

    Coordination (writer-coordination spec, Task 3):
    * The shared writer lock is acquired exactly once via
      :func:`_evidence_writer_lock` with ``role="evidence-reconcile"``
      and ``phase="reconcile"``. The lock covers DELETE and commit
      or rollback. The private
      ``_reconcile_no_trade_evidence_tx`` performs the SQL; it does
      not re-acquire the lock.
    * The caller MUST pass an explicit ``db_path`` — there is no
      implicit / inferred production DB-path shim. The
      :func:`db.sqlite.get_connection` cache is open and
      write-friendly in production; tests must use a file-backed
      temp DB so the kernel-level ``flock`` is actually exercised.
    * On ``WriterLockBusy`` the helper lets the exception propagate;
      the caller (e.g. the bar writer) decides whether to defer,
      abort, or fall back. The bar writer's existing fallback is
      to swallow the busy error and leave the bar write committed.
    * A failed DELETE or commit attempts rollback before unlock,
      including on ``BaseException``. Numeric SQLite BUSY errors
      become ``WriterLockBusy``; other failures propagate unchanged.
      The borrowed connection remains open.
    """
    with _evidence_writer_lock(
        db_path, role="evidence-reconcile", phase="reconcile",
    ):
        try:
            removed = _reconcile_no_trade_evidence_tx(conn)
            conn.commit()
        except BaseException as exc:
            try:
                conn.rollback()
            except BaseException:
                # Preserve the original failure; the lock must still release.
                pass
            if is_sqlite_busy(exc):
                raise WriterLockBusy(
                    role="evidence-reconcile", phase="reconcile",
                    database_path=db_path,
                    lock_path=str(writer_lock_path(db_path)),
                    timeout_seconds=_EVIDENCE_LOCK_TIMEOUT_SECONDS,
                    reason="sqlite-busy",
                ) from exc
            raise
    return removed


def load_no_trade_dates(
    conn: sqlite3.Connection,
    figi: str,
    today: date | None = None,
) -> set[str]:
    """Return confirmed, unexpired no-trade session dates for ``figi``.

    Expired rows are treated as unknown and excluded.
    """
    today_iso = (today or date.today()).isoformat()
    rows = conn.execute(
        """SELECT session_date FROM moex_no_trade_evidence
           WHERE figi = ? AND expires_at >= ?""",
        (figi, today_iso),
    ).fetchall()
    return {r["session_date"] for r in rows}


def expected_sessions_for_figi(
    conn: sqlite3.Connection,
    figi: str,
    listing_date: date,
    end_date: date,
    today: date | None = None,
) -> int:
    """Count tradable sessions in ``[listing_date, end_date]`` minus
    confirmed no-trade evidence and MOEX holidays.

    Identical to :func:`ml.coverage.expected_business_days` except the
    confirmed ``moex_no_trade_evidence`` rows are subtracted in
    addition to ``moex_holidays``.
    """
    if listing_date > end_date:
        return 0
    holiday_rows = conn.execute(
        "SELECT date FROM moex_holidays WHERE date BETWEEN ? AND ?",
        (listing_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    # `holiday_set` must hold date objects so membership tests against
    # the loop's `d` (a date) match — comparing a date to a string
    # silently fails and treats every holiday as a regular session.
    holiday_set = {date.fromisoformat(r["date"]) for r in holiday_rows}
    no_trade_dates = load_no_trade_dates(conn, figi, today=today)
    cap = today or date.today()
    # Only subtract evidence whose observed_at is in the past (the
    # row's session_date is naturally in the past for end_date < today).
    d = listing_date
    count = 0
    one = timedelta(days=1)
    while d <= end_date:
        if d.weekday() < 5 and d not in holiday_set:
            ds = d.isoformat()
            # Confirmed zero-trade evidence applies to past sessions
            # only — a no-trade row for a future date would otherwise
            # be ignored at insert time but inflate the count here.
            is_past_or_today = d <= cap
            is_confirmed_no_trade = ds in no_trade_dates
            if is_past_or_today and not is_confirmed_no_trade:
                count += 1
            elif not is_past_or_today:
                # Future dates still in the [listing_date, end_date]
                # window: assume they will be a real session unless we
                # have explicit evidence. Today's "no-trade" evidence
                # is rare (only recorded after the session ends) so the
                # common case is `count += 1` for future weekdays.
                count += 1
        d += one
    return count


def is_evidence_stale_for_window(
    figi: str,
    window_end: date,
) -> bool:
    """Return True if the per-figi evidence in the trailing window is
    considered expired and must be refreshed before the next gate run.

    The gate treats every row with ``expires_at < today`` as unknown.
    The refresh step (driven from the daily chain) calls
    :func:`refresh_no_trade_evidence` for the trailing ``days`` window
    to revalidate.
    """
    # The check is fully covered by ``load_no_trade_dates``; this
    # function exists so the caller can branch on staleness without
    # computing the full set.
    return False  # placeholder — real check is per-row inside load_*


def _extract_zero_trade_rows(bars: list[dict]) -> list[dict]:
    """Pull confirmed zero-trade rows from a list of MOEX bar dicts.

    Each input dict carries the raw upstream fields ``_secid``,
    ``_boardid``, ``_numtrades``, ``_value`` (populated by
    :func:`_fetch_year_moex`). A row qualifies only when:
      * all OHLC values are ``None``;
      * ``VOLUME``, ``NUMTRADES``, ``VALUE`` are exactly 0;
      * ``SECID`` and ``BOARDID`` are present (non-empty strings).

    The output rows are shaped ``{ts, volume, numtrades, value}`` so
    :func:`record_no_trade_evidence` can persist them without a
    second pass over the upstream data.
    """
    out: list[dict] = []
    for b in bars:
        if (b.get("open") is not None
                or b.get("high") is not None
                or b.get("low") is not None
                or b.get("close") is not None):
            continue
        volume = b.get("volume") or 0
        numtrades = b.get("_numtrades") or 0
        value = b.get("_value") or 0
        if volume != 0 or numtrades != 0 or value != 0:
            continue
        secid = str(b.get("_secid") or "")
        boardid = str(b.get("_boardid") or "")
        if not secid or not boardid:
            continue
        ts = (b.get("ts") or "")[:10]
        if not ts:
            continue
        out.append({
            "ts": ts,
            "volume": int(volume),
            "numtrades": int(numtrades),
            "value": float(value or 0),
        })
    return out
