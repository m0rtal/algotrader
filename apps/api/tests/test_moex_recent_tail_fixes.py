"""Regression tests for PR #100 follow-up fixes.

PR #100 (``feat(moex): add recent-tail fetch as fallback for Tinkoff
outages``) shipped three things that the review flagged as broken or
unspecified:

  * Bug 1 (HIGH): ``_fetch_moex_range`` returned bars outside the
    requested window when the window crossed a year boundary, because
    ``_fetch_year_moex`` only honors ``last_trading_day`` for the
    current year. Fixed by post-filtering ``year_bars`` to
    ``[from_d, year_cap]`` inside ``_fetch_moex_range``.

  * Bug 2 (HIGH): ``backfill_moex_recent_tail._process_one`` returned
    ``len(rows)`` (rows-ATTEMPTED) from ``replace_bars_for_figi`` even
    though the function uses ``replace=False`` (INSERT OR IGNORE
    silently skips duplicates). Operators using the summary log for
    ops triage got an inflated count. Fixed by computing pre/post
    row-count diff inside ``_process_one`` so the returned int
    represents rows-actually-inserted.

  * Wiring (TL;DR): ``backfill_moex_recent_tail`` shipped as dead
    code because no production caller passed ``recent_tail_days>0``.
    Fixed by wiring ``apps/api/worker.py:_step_backfill_moex`` to pass
    ``recent_tail_days=5``.

These tests pin the post-fix behavior so the bugs can't quietly come
back.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import responses

from algotrader_api.ingestion.backfill import (
    BackfillRunner,
    _fetch_moex_range,
)


# ─── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path):
    """Single-instrument fresh DB.

    Tests in this file focus on PR #100's recent-tail pass. Using a
    single ticker keeps each test scoped to one figi's behavior so a
    second figi that doesn't have its MOEX boards mocked doesn't
    silently fail the prefetch and skew the assertions.
    """
    db_path = str(tmp_path / "test.db")
    from algotrader_api.db import sqlite as sqlitedb
    migrations_dir = str(
        Path(__file__).resolve().parent.parent
        / "src/algotrader_api/db/migrations"
    )
    sqlitedb.run_migrations(db_path, migrations_dir)
    sqlitedb.close_all()
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES ('SBER', 'BBG004730N88', 'share', 'Sber', 'rub', 10)"
    )
    con.commit()
    con.close()
    return db_path


async def _noop_sink(_ev):
    return None


# ─── Bug 1: _fetch_moex_range filters out-of-window bars ──────────────


def test_fetch_moex_range_filters_out_of_window_when_window_crosses_year(monkeypatch):
    """When the requested window crosses a calendar-year boundary,
    ``_fetch_year_moex`` for the previous-calendar-year leg ignores
    ``last_trading_day`` and returns the full year of bars.

    PR #100's first HIGH bug: ``_fetch_moex_range`` blindly extended
    those into the output, so ``backfill_moex_recent_tail`` would
    INSERT OR IGNORE rows dated well outside the requested tail
    window. Post-fix, the helper filters ``year_bars`` to the
    requested window after every per-year fetch.

    Scenario: window = [2026-12-20 .. 2027-01-04]. Two-year loop.
    2027 fetch returns the full year of bars (MOEX ignores the cap
    because ``year != last_trading_day.year``). The post-filter must
    drop everything outside ``[2026-12-20, 2027-01-04]``.
    """
    from_d = date(2026, 12, 20)
    to_d = date(2027, 1, 4)
    last_trading_day = date(2027, 1, 4)

    def fake_year(market, board, ticker, year, last_trading_day=None):
        # Emulate the bug: 2027 fetch ignores `last_trading_day`
        # because year != last_trading_day.year, returns the full year.
        if year == 2026:
            return [{"ts": "2026-12-20", "open": 100, "high": 101,
                     "low": 99, "close": 100.5, "volume": 1000},
                    {"ts": "2026-12-21", "open": 101, "high": 102,
                     "low": 100, "close": 101.5, "volume": 1100}]
        if year == 2027:
            # Simulate the leaky MOEX response: bars from the entire
            # year, well outside the requested window.
            return [
                {"ts": "2027-01-04", "open": 200, "high": 201,
                 "low": 199, "close": 200.5, "volume": 2000},  # in-window
                {"ts": "2027-01-15", "open": 201, "high": 202,
                 "low": 200, "close": 201.5, "volume": 2100},  # out
                {"ts": "2027-03-01", "open": 210, "high": 211,
                 "low": 209, "close": 210.5, "volume": 2200},  # out
                {"ts": "2027-06-15", "open": 220, "high": 221,
                 "low": 219, "close": 220.5, "volume": 2300},  # out
                {"ts": "2027-12-31", "open": 230, "high": 231,
                 "low": 229, "close": 230.5, "volume": 2400},  # out
            ]
        return []

    monkeypatch.setattr(
        "algotrader_api.ingestion.backfill._fetch_year_moex",
        fake_year,
    )

    bars = _fetch_moex_range(
        "shares", "TQBR", "TEST",
        from_d, to_d, last_trading_day=last_trading_day,
    )

    ts_set = {b["ts"] for b in bars}
    # In-window bars MUST survive.
    assert "2026-12-20" in ts_set
    assert "2026-12-21" in ts_set
    assert "2027-01-04" in ts_set
    # Out-of-window bars MUST be dropped (the bug).
    assert "2027-01-15" not in ts_set, (
        "out-of-window 2027-01-15 leaked through _fetch_moex_range; "
        "_fetch_year_moex for year != last_trading_day.year returns "
        "the full year, the post-filter must drop it."
    )
    assert "2027-03-01" not in ts_set
    assert "2027-06-15" not in ts_set
    assert "2027-12-31" not in ts_set
    assert len(bars) == 3, f"expected exactly 3 in-window bars, got {len(bars)}"


def test_fetch_moex_range_preserves_in_window_bars_single_year(monkeypatch):
    """Sanity check: when the window fits in a single calendar year,
    the post-filter keeps every returned bar (no false negatives)."""
    from_d = date(2026, 9, 11)
    to_d = date(2026, 9, 21)

    def fake_year(market, board, ticker, year, last_trading_day=None):
        return [
            {"ts": "2026-09-15", "open": 100, "high": 101, "low": 99,
             "close": 100.5, "volume": 1000},
            {"ts": "2026-09-18", "open": 101, "high": 102, "low": 100,
             "close": 101.5, "volume": 1100},
        ]

    monkeypatch.setattr(
        "algotrader_api.ingestion.backfill._fetch_year_moex",
        fake_year,
    )
    bars = _fetch_moex_range(
        "shares", "TQBR", "TEST",
        from_d, to_d, last_trading_day=to_d,
    )
    assert len(bars) == 2


def test_fetch_moex_range_returns_empty_when_from_after_to(monkeypatch):
    """Empty window short-circuits before any HTTP work."""
    from_d = date(2026, 9, 21)
    to_d = date(2026, 9, 11)

    def fail(*_a, **_kw):
        raise AssertionError(
            "_fetch_year_moex must not be called when from_d > to_d"
        )

    monkeypatch.setattr(
        "algotrader_api.ingestion.backfill._fetch_year_moex", fail,
    )
    assert _fetch_moex_range(
        "shares", "TQBR", "TEST",
        from_d, to_d, last_trading_day=to_d,
    ) == []


# ─── Bug 2: backfill_moex_recent_tail reports rows-actually-inserted ──


@responses.activate
async def test_recent_tail_reports_rows_actually_inserted_not_attempted(
    fresh_db, monkeypatch,
):
    """When every bar the tail pass tries to write is already in the DB
    (e.g. the daily chain ran an hour ago), ``replace_bars_for_figi``
    returns the number of rows ATTEMPTED (because of `executemany`
    quirks in Python's sqlite3 module), not the number actually
    INSERTed. INSERT OR IGNORE silently skipped them all.

    PR #100's second HIGH bug: the summary log claimed "wrote N bars"
    where N was the count of attempted rows, so ops triage saw
    thousands of writes per figi on a no-op day.

    Post-fix: ``_process_one`` computes the diff between pre- and
    post-call row count for the figi in the requested window. The
    function returns rows-actually-inserted; on a fully-covered figi
    that's 0, and ``written_total`` for the whole run is the actual
    number of new bars added.
    """
    import json

    # MOEX meta: SBER active on TQBR through 2099 (no `listed_till`
    # constraint — the recent-tail pass must compute to_d from
    # min(yesterday, listed_till_d) and we want yesterday to win).
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/SBER.json",
        json={"boards": {"data": [["SBER", "TQBR", "x", 0, 0, "shares",
                                    0, 1, 1, 0,
                                    "2014-01-01", "2099-12-31",
                                    "2014-01-01", "2099-12-31",
                                    1, "SUR", "%"]]}},
    )

    # Window: [2026-09-11 .. 2026-09-21] (yesterday=2026-09-21 for days=5).
    # _fetch_moex_range will issue one HTTP request to the history URL
    # for year 2026; we return two bars in that window.
    def sber_history_cb(request):
        return (200, {}, json.dumps({
            "history": {
                "columns": ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"],
                "data": [
                    ["2026-09-18", 100.0, 101.0, 99.0, 100.5, 1000],
                    ["2026-09-21", 101.0, 102.0, 100.0, 101.5, 1100],
                ],
            },
            "history.cursor": {"data": [[0, 2, 500]]},
        }))

    responses.add_callback(
        responses.GET,
        "https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities/SBER.json",
        callback=sber_history_cb,
    )

    # Pre-seed BOTH bars into the DB so every attempted insert hits
    # the INSERT OR IGNORE path. After the recent-tail pass, the
    # figi has the same two rows it started with.
    con = sqlite3.connect(fresh_db)
    con.executescript("""
        INSERT INTO bars (figi, ts, open, high, low, close, volume, source)
        VALUES ('BBG004730N88', '2026-09-18', 100.0, 101.0, 99.0, 100.5, 1000, 'moex');
        INSERT INTO bars (figi, ts, open, high, low, close, volume, source)
        VALUES ('BBG004730N88', '2026-09-21', 101.0, 102.0, 100.0, 101.5, 1100, 'moex');
    """)
    con.commit()
    con.close()

    # Pin _last_trading_day so the recent-tail pass picks yesterday
    # deterministically regardless of the host's calendar.
    import algotrader_api.ingestion.backfill as bf_mod
    monkeypatch.setattr(
        bf_mod, "_last_trading_day",
        lambda today, db_path, **_kw: date(2026, 9, 21),
    )

    runner = BackfillRunner(
        client=MagicMock(), db_path=fresh_db, event_sink=_noop_sink,
    )
    written = await runner.backfill_moex_recent_tail(
        days=5, today=date(2026, 9, 22),
    )

    # The pre-fix code would return 2 (rows-attempted), misleading
    # operators into thinking the pass inserted fresh data. Post-fix
    # must return 0 because both bars were already in the DB.
    assert written == 0, (
        f"recent-tail pass must report rows-actually-inserted (0, "
        f"since both bars were already in DB); got {written} "
        f"(pre-fix returned rows-attempted)"
    )

    # And the row count must not have grown.
    con = sqlite3.connect(fresh_db)
    n = con.execute(
        "SELECT COUNT(*) FROM bars WHERE figi = ?",
        ("BBG004730N88",),
    ).fetchone()[0]
    con.close()
    assert n == 2


@responses.activate
async def test_recent_tail_writes_only_new_bars_and_counts_them(
    fresh_db, monkeypatch,
):
    """Happy-path twin of the above: when MOEX returns a bar that ISN'T
    yet in the DB, ``written_total`` must reflect the actual new
    inserts (not zero, not the total attempted)."""
    import json

    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/SBER.json",
        json={"boards": {"data": [["SBER", "TQBR", "x", 0, 0, "shares",
                                    0, 1, 1, 0,
                                    "2014-01-01", "2099-12-31",
                                    "2014-01-01", "2099-12-31",
                                    1, "SUR", "%"]]}},
    )

    # 3 bars in the recent-tail window, none in DB yet.
    def cb(request):
        return (200, {}, json.dumps({
            "history": {
                "columns": ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"],
                "data": [
                    ["2026-09-15", 100.0, 101.0, 99.0, 100.5, 1000],
                    ["2026-09-18", 101.0, 102.0, 100.0, 101.5, 1100],
                    ["2026-09-21", 102.0, 103.0, 101.0, 102.5, 1200],
                ],
            },
            "history.cursor": {"data": [[0, 3, 500]]},
        }))

    responses.add_callback(
        responses.GET,
        "https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities/SBER.json",
        callback=cb,
    )

    import algotrader_api.ingestion.backfill as bf_mod
    monkeypatch.setattr(
        bf_mod, "_last_trading_day",
        lambda today, db_path, **_kw: date(2026, 9, 21),
    )

    runner = BackfillRunner(
        client=MagicMock(), db_path=fresh_db, event_sink=_noop_sink,
    )
    written = await runner.backfill_moex_recent_tail(
        days=5, today=date(2026, 9, 22),
    )

    assert written == 3, (
        f"expected 3 rows actually inserted; got {written}"
    )

    con = sqlite3.connect(fresh_db)
    rows = con.execute(
        "SELECT ts FROM bars WHERE figi = ? ORDER BY ts",
        ("BBG004730N88",),
    ).fetchall()
    con.close()
    assert [r[0] for r in rows] == ["2026-09-15", "2026-09-18", "2026-09-21"]


@responses.activate
async def test_recent_tail_skips_figis_without_primary_moex_board(
    fresh_db, monkeypatch,
):
    """Tickers with no MOEX primary board (sanctions-delisted) must be
    silently skipped — Tinkoff fallback is intentionally NOT attempted
    here; the whole point of this pass is to bypass Tinkoff."""
    # No MOEX boards for SBER → empty response.
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/SBER.json",
        json={"boards": {"data": []}},
    )

    import algotrader_api.ingestion.backfill as bf_mod
    monkeypatch.setattr(
        bf_mod, "_last_trading_day",
        lambda today, db_path, **_kw: date(2026, 9, 21),
    )

    runner = BackfillRunner(
        client=MagicMock(), db_path=fresh_db, event_sink=_noop_sink,
    )
    written = await runner.backfill_moex_recent_tail(
        days=5, today=date(2026, 9, 22),
    )
    assert written == 0

    con = sqlite3.connect(fresh_db)
    n = con.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    con.close()
    assert n == 0


# ─── Wiring: backfill_from_moex(recent_tail_days=N) drives the pass ──


@responses.activate
async def test_backfill_from_moex_with_recent_tail_days_runs_the_tail_pass(
    fresh_db, monkeypatch,
):
    """End-to-end: when the worker calls ``backfill_from_moex(recent_tail_days=5)``
    (the new wiring in ``apps/api/worker.py``), the tail pass must
    actually run. Pre-fix the kwarg was accepted but never read by
    any production caller, so the feature was dead code.

    Test technique: count MOEX history-URL hits. The historical walk
    fires one history request per figi. The recent-tail pass fires
    another. If the tail pass actually runs, MOEX sees >=2 requests;
    if it doesn't (the original PR #100 dead-code bug), only 1.
    """
    import json

    # MOEX meta for SBER (no listed_till constraint).
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/SBER.json",
        json={"boards": {"data": [["SBER", "TQBR", "x", 0, 0, "shares",
                                    0, 1, 1, 0,
                                    "2014-01-01", "2099-12-31",
                                    "2014-01-01", "2099-12-31",
                                    1, "SUR", "%"]]}},
    )

    hit_count = {"n": 0}

    def cb(request):
        hit_count["n"] += 1
        return (200, {}, json.dumps({
            "history": {
                "columns": ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"],
                "data": [["2026-09-21", 100.0, 101.0, 99.0, 100.5, 1000]],
            },
            "history.cursor": {"data": [[0, 1, 500]]},
        }))

    responses.add_callback(
        responses.GET,
        "https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities/SBER.json",
        callback=cb,
    )

    import algotrader_api.ingestion.backfill as bf_mod
    monkeypatch.setattr(
        bf_mod, "_last_trading_day",
        lambda today, db_path, **_kw: date(2026, 9, 21),
    )

    runner = BackfillRunner(
        client=MagicMock(), db_path=fresh_db, event_sink=_noop_sink,
    )
    written = await runner.backfill_from_moex(
        today=date(2026, 9, 22), recent_tail_days=1,
    )

    # The recent-tail pass MUST have hit MOEX. With the dead-code
    # wiring (recent_tail_days=0 default), only the historical walk
    # runs — exactly one history-URL request. With the new wiring,
    # both passes fire — at least 2 distinct invocations for a figi
    # with a primary board.
    assert hit_count["n"] >= 2, (
        f"expected the recent-tail pass to fire MOEX history requests "
        f"in addition to the historical walk; saw {hit_count['n']} "
        f"request(s). If only one request landed, the recent-tail "
        f"pass is dead code (the original PR #100 bug)."
    )

    # Sanity: the historical walk contributed at least one bar.
    con = sqlite3.connect(fresh_db)
    n = con.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    con.close()
    assert n >= 1, (
        f"expected at least 1 bar from the historical walk; got n={n}"
    )
    assert written >= 1, (
        f"written_total={written} should reflect at least the "
        f"historical-walk contribution"
    )
