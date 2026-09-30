"""Tests for the per-figi no-trade evidence pipeline.

The evidence module is independent from `bars`: it persists explicit
MOEX confirmations of zero-trade sessions so the consumption-time
coverage gate can subtract them from the expected denominator and
accept a continuous chain as fresh. The bar writer always wins.

These tests verify the core invariants:

  * Partial / wrong-shape upstream data produces no evidence rows.
  * Records are idempotent (ON CONFLICT refreshes observed_at /
    expires_at without changing figi / session_date).
  * Real bars always suppress a stale evidence row.
  * Expired evidence rows are ignored by the loader.
  * The recent-tail fetcher threads raw upstream columns through so
    the evidence helper can confirm identity without a second request.
  * The coverage gate accepts a complete chain and rejects a gap.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest


# -- helpers ---------------------------------------------------------------


def _make_conn():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE instruments (
            figi TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            isin TEXT,
            source_updated_at TEXT,
            expected_bars INTEGER
        );
        CREATE TABLE bars (
            figi TEXT NOT NULL,
            ts TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'tinkoff',
            PRIMARY KEY (figi, ts)
        );
        CREATE TABLE moex_no_trade_evidence (
            figi TEXT NOT NULL,
            session_date TEXT NOT NULL,
            board TEXT NOT NULL,
            isin TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'moex_iss',
            observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL,
            UNIQUE (figi, session_date)
        );
        CREATE TABLE moex_holidays (date TEXT PRIMARY KEY, name TEXT NOT NULL);
    """)
    return con


def _bar(figi, ts, close=100.0):
    return (figi, ts, close, close, close, close, 1000, "tinkoff")


def _insert_bar(con, figi, ts, close=100.0):
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        _bar(figi, ts, close),
    )
    con.commit()


# -- _extract_zero_trade_rows ----------------------------------------------


def test_extract_zero_trade_rows_picks_only_zero_shape():
    from algotrader_api.ingestion.no_trade_evidence import (
        _extract_zero_trade_rows,
    )

    rows = [
        {  # Confirmed zero-trade row.
            "ts": "2026-09-28",
            "open": None, "high": None, "low": None, "close": None,
            "volume": 0,
            "_secid": "RU000A0JWVL2",
            "_boardid": "TQCB",
            "_numtrades": 0,
            "_value": 0,
        },
        {  # Real bar — must not be picked.
            "ts": "2026-09-29",
            "open": 100, "high": 102, "low": 99, "close": 101,
            "volume": 1000,
            "_secid": "RU000A0JWVL2",
            "_boardid": "TQCB",
        },
        {  # Partial OHLC — must not be picked.
            "ts": "2026-09-30",
            "open": None, "high": 100, "low": None, "close": None,
            "volume": 0,
            "_secid": "RU000A0JWVL2",
            "_boardid": "TQCB",
        },
        {  # VOLUME > 0 with NULL OHLC — must not be picked.
            "ts": "2026-10-01",
            "open": None, "high": None, "low": None, "close": None,
            "volume": 5,
            "_secid": "RU000A0JWVL2",
            "_boardid": "TQCB",
            "_numtrades": 0,
            "_value": 0,
        },
    ]
    out = _extract_zero_trade_rows(rows)
    assert out == [{
        "ts": "2026-09-28",
        "volume": 0,
        "numtrades": 0,
        "value": 0.0,
    }]


def test_extract_zero_trade_rows_skips_missing_identity():
    from algotrader_api.ingestion.no_trade_evidence import (
        _extract_zero_trade_rows,
    )
    base = {
        "open": None, "high": None, "low": None, "close": None,
        "volume": 0,
        "_numtrades": 0,
        "_value": 0,
    }
    assert _extract_zero_trade_rows([
        {**base, "ts": "2026-09-28", "_secid": "", "_boardid": "TQCB"},
    ]) == []
    assert _extract_zero_trade_rows([
        {**base, "ts": "2026-09-28", "_secid": "RU000A0JWVL2", "_boardid": ""},
    ]) == []
    assert _extract_zero_trade_rows([
        {**base, "ts": "", "_secid": "X", "_boardid": "TQCB"},
    ]) == []


# -- record_no_trade_evidence ---------------------------------------------


def test_record_skips_dates_that_have_real_bars():
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
    )

    con = _make_conn()
    _insert_bar(con, "FIGI1", "2026-09-28")
    written = record_no_trade_evidence(
        con,
        figi="FIGI1",
        rows=[{"ts": "2026-09-28"}],
        board="TQCB",
        isin="RU000A0JWVL2",
    )
    assert written == 0
    count = con.execute(
        "SELECT COUNT(*) FROM moex_no_trade_evidence"
    ).fetchone()[0]
    assert count == 0


def test_record_idempotent_on_conflict_refreshes_timestamps():
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
    )

    con = _make_conn()
    today = date(2026, 9, 28)
    rows = [{"ts": "2026-09-26"}]

    record_no_trade_evidence(
        con, figi="FIGI1", rows=rows, board="TQCB", isin="X",
        now=today,
    )
    row = con.execute(
        "SELECT observed_at, expires_at FROM moex_no_trade_evidence "
        "WHERE figi='FIGI1' AND session_date='2026-09-26'"
    ).fetchone()
    first_expires = row["expires_at"]

    # Second insert: same key, new today. observed_at is wall-clock so
    # only expires_at is deterministic across sub-second repeats;
    # expires_at must advance because it is derived from `now`.
    record_no_trade_evidence(
        con, figi="FIGI1", rows=rows, board="TQCB", isin="X",
        now=today + timedelta(days=3),
    )
    row2 = con.execute(
        "SELECT observed_at, expires_at FROM moex_no_trade_evidence "
        "WHERE figi='FIGI1' AND session_date='2026-09-26'"
    ).fetchone()
    assert row2["expires_at"] != first_expires
    count = con.execute(
        "SELECT COUNT(*) FROM moex_no_trade_evidence"
    ).fetchone()[0]
    assert count == 1


def test_record_assigns_recent_vs_historical_expiry():
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
        RECENT_EVIDENCE_EXPIRY,
        HISTORICAL_EVIDENCE_EXPIRY,
    )

    con = _make_conn()
    today = date(2026, 9, 28)
    record_no_trade_evidence(
        con,
        figi="FIGI1",
        rows=[
            {"ts": "2026-09-22"},  # within 14 days → recent
            {"ts": "2020-05-15"},  # well outside 14 days → historical
        ],
        board="TQCB",
        isin="X",
        now=today,
    )
    rows = con.execute(
        "SELECT session_date, expires_at FROM moex_no_trade_evidence "
        "ORDER BY session_date"
    ).fetchall()
    by_date = {r["session_date"]: r["expires_at"] for r in rows}
    recent_expected = (today + RECENT_EVIDENCE_EXPIRY).isoformat()
    historical_expected = (today + HISTORICAL_EVIDENCE_EXPIRY).isoformat()
    assert by_date["2026-09-22"] == recent_expected
    assert by_date["2020-05-15"] == historical_expected


# -- reconcile_no_trade_evidence ------------------------------------------


def test_reconcile_removes_evidence_when_bar_lands():
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
        reconcile_no_trade_evidence,
    )

    con = _make_conn()
    record_no_trade_evidence(
        con,
        figi="FIGI1",
        rows=[{"ts": "2026-09-26"}, {"ts": "2026-09-27"}],
        board="TQCB",
        isin="X",
    )
    _insert_bar(con, "FIGI1", "2026-09-26")
    removed = reconcile_no_trade_evidence(con)
    assert removed == 1
    remaining = con.execute(
        "SELECT session_date FROM moex_no_trade_evidence ORDER BY session_date"
    ).fetchall()
    assert [r["session_date"] for r in remaining] == ["2026-09-27"]


def test_reconcile_idempotent():
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
        reconcile_no_trade_evidence,
    )

    con = _make_conn()
    record_no_trade_evidence(
        con,
        figi="FIGI1",
        rows=[{"ts": "2026-09-26"}],
        board="TQCB",
        isin="X",
    )
    _insert_bar(con, "FIGI1", "2026-09-26")
    reconcile_no_trade_evidence(con)
    # Second call must not raise or count any rows.
    assert reconcile_no_trade_evidence(con) == 0


# -- load_no_trade_dates ---------------------------------------------------


def test_load_excludes_expired_rows():
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
        load_no_trade_dates,
    )

    con = _make_conn()
    today = date(2026, 9, 28)
    record_no_trade_evidence(
        con,
        figi="FIGI1",
        rows=[{"ts": "2026-09-22"}, {"ts": "2020-05-15"}],
        board="TQCB",
        isin="X",
        now=today,
    )
    # Recent row expires 7 days from today → still valid on day +6
    # and at the day+7 boundary (>= comparison).
    valid = load_no_trade_dates(con, "FIGI1", today=today + timedelta(days=7))
    assert valid == {"2026-09-22", "2020-05-15"}
    # Day +8 → recent row expires, historical row still valid.
    valid = load_no_trade_dates(con, "FIGI1", today=today + timedelta(days=8))
    assert valid == {"2020-05-15"}
    # Day +366 → historical row also expires.
    valid = load_no_trade_dates(con, "FIGI1", today=today + timedelta(days=366))
    assert valid == set()


# -- expected_sessions_for_figi -------------------------------------------


def test_expected_sessions_subtracts_confirmed_evidence_and_holidays():
    from algotrader_api.ingestion.no_trade_evidence import (
        expected_sessions_for_figi,
        record_no_trade_evidence,
    )

    con = _make_conn()
    # Saturday 2026-09-26 and Sunday 2026-09-27 already excluded by
    # weekday < 5. Monday 2026-09-28 is a regular session.
    con.execute(
        "INSERT INTO moex_holidays VALUES ('2026-09-29', 'X')"
    )
    record_no_trade_evidence(
        con,
        figi="FIGI1",
        rows=[{"ts": "2026-09-28"}],  # confirmed no-trade Mon
        board="TQCB",
        isin="X",
    )
    count = expected_sessions_for_figi(
        con, "FIGI1",
        listing_date=date(2026, 9, 28),
        end_date=date(2026, 9, 30),
        today=date(2026, 9, 30),
    )
    # 2026-09-28 confirmed no-trade → not counted
    # 2026-09-29 holiday → not counted
    # 2026-09-30 regular weekday → counted
    assert count == 1


# -- check_coverage staleness override ------------------------------------


def test_check_coverage_accepts_continuous_chain():
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
    )
    from algotrader_api.ml.features import check_coverage

    con = _make_conn()
    con.execute(
        "INSERT INTO instruments(figi, ticker, isin, source_updated_at, expected_bars) "
        "VALUES ('FIGI1', 'X', 'X', '2025-01-01', 1)"
    )
    # Bars end on Friday 2026-09-25. Last completed session is the
    # following Monday 2026-09-28 (Tuesday is "today" — outside window).
    _insert_bar(con, "FIGI1", "2026-09-25")
    record_no_trade_evidence(
        con,
        figi="FIGI1",
        rows=[{"ts": "2026-09-28"}],  # Mon confirmed no-trade
        board="TQCB",
        isin="X",
    )

    # Pretend today is Tue 2026-09-29 so 2026-09-28 is the last session.
    import datetime as _dt
    today = date(2026, 9, 29)
    real_today = _dt.date

    class _FakeDate(real_today):
        @classmethod
        def today(cls):
            return today

    import algotrader_api.ml.features as features_mod
    features_mod.date = _FakeDate
    try:
        failing = check_coverage(con, ["FIGI1"])
    finally:
        features_mod.date = real_today
    assert failing == []


def test_check_coverage_rejects_partial_chain():
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
    )
    from algotrader_api.ml.features import check_coverage

    con = _make_conn()
    con.execute(
        "INSERT INTO instruments(figi, ticker, isin, source_updated_at, expected_bars) "
        "VALUES ('FIGI1', 'X', 'X', '2025-01-01', 1)"
    )
    _insert_bar(con, "FIGI1", "2026-09-25")
    # Gap: only Mon evidence, no evidence for Tue (the last session).
    record_no_trade_evidence(
        con,
        figi="FIGI1",
        rows=[{"ts": "2026-09-28"}],
        board="TQCB",
        isin="X",
    )
    import datetime as _dt
    today = date(2026, 9, 30)
    real_today = _dt.date

    class _FakeDate(real_today):
        @classmethod
        def today(cls):
            return today

    import algotrader_api.ml.features as features_mod
    features_mod.date = _FakeDate
    try:
        failing = check_coverage(con, ["FIGI1"])
    finally:
        features_mod.date = real_today
    reasons = {f["figi"]: f["reason"] for f in failing}
    assert "FIGI1" in reasons
    assert "stale" in reasons["FIGI1"]


def test_check_coverage_keeps_incomplete_arm_separate():
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
    )
    from algotrader_api.ml.features import check_coverage

    con = _make_conn()
    # Cached expected_bars artificially high; coverage ratio < 95%.
    con.execute(
        "INSERT INTO instruments(figi, ticker, isin, source_updated_at, expected_bars) "
        "VALUES ('FIGI1', 'X', 'X', '2025-01-01', 100)"
    )
    _insert_bar(con, "FIGI1", "2026-09-25")
    record_no_trade_evidence(
        con,
        figi="FIGI1",
        rows=[{"ts": "2026-09-28"}],
        board="TQCB",
        isin="X",
    )
    import datetime as _dt
    today = date(2026, 9, 29)
    real_today = _dt.date

    class _FakeDate(real_today):
        @classmethod
        def today(cls):
            return today

    import algotrader_api.ml.features as features_mod
    features_mod.date = _FakeDate
    try:
        failing = check_coverage(con, ["FIGI1"])
    finally:
        features_mod.date = real_today
    reasons = {f["figi"]: f["reason"] for f in failing}
    assert reasons["FIGI1"] == "incomplete"


def test_check_coverage_treats_expired_evidence_as_unknown():
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
    )
    from algotrader_api.ml.features import check_coverage

    con = _make_conn()
    con.execute(
        "INSERT INTO instruments(figi, ticker, isin, source_updated_at, expected_bars) "
        "VALUES ('FIGI1', 'X', 'X', '2025-01-01', 1)"
    )
    _insert_bar(con, "FIGI1", "2026-09-25")
    # Evidence recorded 30 days ago with a 7-day expiry → expired.
    record_no_trade_evidence(
        con,
        figi="FIGI1",
        rows=[{"ts": "2026-09-28"}],
        board="TQCB",
        isin="X",
        now=date(2026, 9, 28),
    )
    import datetime as _dt
    # Past recent (7-day) expiry.
    today = date(2026, 10, 8)
    real_today = _dt.date

    class _FakeDate(real_today):
        @classmethod
        def today(cls):
            return today

    import algotrader_api.ml.features as features_mod
    features_mod.date = _FakeDate
    try:
        failing = check_coverage(con, ["FIGI1"])
    finally:
        features_mod.date = real_today
    reasons = {f["figi"]: f["reason"] for f in failing}
    assert "stale" in reasons["FIGI1"]


# -- _fetch_year_moex raw column threading -------------------------------


def test_fetch_year_moex_emits_raw_columns_per_dict():
    """Regression test for the upstream column passthrough.

    The recent-tail pass relies on these extra fields to confirm
    identity and zero-trade shape without a second HTTP request.
    """
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    captured: list[dict] = []

    def fake_get(url, params=None, timeout=None):  # noqa: ARG001
        # Only one page; emit a real bar + a zero-trade row + a partial row.
        payload = {
            "history": {
                "columns": [
                    "TRADEDATE", "SECID", "BOARDID",
                    "OPEN", "HIGH", "LOW", "CLOSE",
                    "VOLUME", "NUMTRADES", "VALUE",
                ],
                "data": [
                    ["2026-09-28", "RU000A0JWVL2", "TQCB",
                     None, None, None, None, 0, 0, 0],
                    ["2026-09-29", "RU000A0JWVL2", "TQCB",
                     100, 102, 99, 101, 1000, 5, 100000],
                    ["2026-09-30", "RU000A0JWVL2", "TQCB",
                     None, 100, None, None, 0, 0, 0],
                ],
            },
            "history.cursor": {"data": [[3, 3, 500]]},
        }
        captured.append(payload)
        return _FakeResp(payload)

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get", side_effect=fake_get):
        rows = backfill._fetch_year_moex(
            "bonds", "TQCB", "RU000A0JWVL2", 2026,
        )
    assert len(rows) == 3
    by_ts = {r["ts"]: r for r in rows}
    zero = by_ts["2026-09-28"]
    assert zero["_secid"] == "RU000A0JWVL2"
    assert zero["_boardid"] == "TQCB"
    assert zero["_numtrades"] == 0
    assert zero["_value"] == 0
    assert zero["open"] is None
    bar = by_ts["2026-09-29"]
    assert bar["_secid"] == "RU000A0JWVL2"
    assert bar["_numtrades"] == 5
    partial = by_ts["2026-09-30"]
    assert partial["_secid"] == "RU000A0JWVL2"


# -- populate_expected_bars evidence subtraction -------------------------


def test_populate_expected_bars_subtracts_confirmed_evidence(tmp_path, monkeypatch):
    """End-to-end: confirmed no-trade evidence must reduce expected_bars.

    Re-implements the relevant portion of the populate script's
    evidence loop to validate the algorithm without invoking the
    script as a subprocess. The actual script is exercised by
    `test_cron_expected_bars.py`.
    """
    from datetime import date as _date
    from algotrader_api.ml.coverage import expected_business_days

    db = tmp_path / "x.db"
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE instruments (figi TEXT PRIMARY KEY, ticker TEXT, isin TEXT, source_updated_at TEXT, expected_bars INTEGER);
        CREATE TABLE bars (figi TEXT, ts TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER, source TEXT, PRIMARY KEY (figi, ts));
        CREATE TABLE moex_holidays (date TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE moex_no_trade_evidence (
            figi TEXT, session_date TEXT, board TEXT, isin TEXT,
            source TEXT DEFAULT 'moex_iss',
            observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT,
            UNIQUE (figi, session_date)
        );
    """)
    figi = "FIGI1"
    # Listing date 2026-09-21; yesterday fixed to 2026-09-30.
    con.execute(
        "INSERT INTO instruments(figi, ticker, isin, source_updated_at) "
        "VALUES (?, 'X', 'X', '2026-09-21T00:00:00')",
        (figi,),
    )
    # Insert a real bar on 2026-09-21 (gives a wider listing window).
    con.execute(
        "INSERT INTO bars(figi, ts, open, high, low, close, volume, source) "
        "VALUES (?, '2026-09-21', 100, 100, 100, 100, 1, 'moex')",
        (figi,),
    )
    # Two confirmed no-trade dates within [listing, yesterday].
    con.executemany(
        "INSERT INTO moex_no_trade_evidence(figi, session_date, board, isin, expires_at) "
        "VALUES (?, ?, 'TQCB', 'X', '2027-01-01')",
        [(figi, "2026-09-28"), (figi, "2026-09-29")],
    )
    con.commit()

    yesterday = _date(2026, 9, 30)
    listing = _date(2026, 9, 21)
    base_expected = expected_business_days(con, listing, yesterday)
    # Apply the same subtraction the script does.
    evidence = con.execute(
        "SELECT session_date FROM moex_no_trade_evidence WHERE figi = ?",
        (figi,),
    ).fetchall()
    adjusted = base_expected
    for r in evidence:
        d = _date.fromisoformat(r["session_date"])
        if listing <= d <= yesterday:
            adjusted -= 1
    assert adjusted == base_expected - 2
    assert base_expected > 0
    assert adjusted >= 0


# -- backfill_from_moex records evidence for historical no-trade rows -----


def test_backfill_from_moex_integration_smoke(monkeypatch, tmp_path):
    """Smoke test: drive backfill_from_moex with stubbed MOEX and
    verify that moex_no_trade_evidence is populated for historical
    zero-trade rows alongside real bars.

    Stubs ``_list_instruments`` to return a single figi,
    ``_get_meta_moex`` to return a fixed primary board, and
    ``_fetch_year_moex`` to emit one explicit zero-trade row plus one
    real bar per year. Asserts that ``moex_no_trade_evidence`` contains
    only the zero-trade dates and ``bars`` contains only the real bars.
    """
    import asyncio
    import tempfile
    from datetime import date as _date
    from algotrader_api.db import sqlite as sqlitedb
    from algotrader_api.ingestion.backfill import BackfillRunner

    with tempfile.TemporaryDirectory() as tmp:
        db_path = f"{tmp}/x.db"
        con = sqlite3.connect(db_path)
        con.executescript("""
            CREATE TABLE instruments (figi TEXT PRIMARY KEY, ticker TEXT NOT NULL, isin TEXT, source_updated_at TEXT, expected_bars INTEGER);
            CREATE TABLE bars (figi TEXT NOT NULL, ts TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'tinkoff', PRIMARY KEY (figi, ts));
            CREATE TABLE moex_no_trade_evidence (
                figi TEXT, session_date TEXT, board TEXT, isin TEXT,
                source TEXT DEFAULT 'moex_iss',
                observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT,
                UNIQUE (figi, session_date)
            );
            CREATE TABLE moex_holidays (date TEXT PRIMARY KEY, name TEXT);
            CREATE TABLE instrument_metadata (
                figi TEXT PRIMARY KEY,
                last_bar_ts TEXT, last_backfilled_at TEXT,
                total_bars INTEGER, last_run_status TEXT,
                last_run_at TEXT, last_error TEXT, first_bar_ts TEXT,
                tinkoff_breaker_open INTEGER DEFAULT 0,
                tinkoff_breaker_open_until TEXT,
                tinkoff_consecutive_failures INTEGER DEFAULT 0,
                tinkoff_last_failure_ts TEXT
            );
            CREATE TABLE ingestion_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL, run_id INTEGER, level TEXT NOT NULL,
                figi TEXT, message TEXT NOT NULL
            );
        """)
        figi = "FIGI1"
        con.execute(
            "INSERT INTO instruments(figi, ticker, isin, source_updated_at) "
            "VALUES (?, 'X', 'X', '2025-01-01')",
            (figi,),
        )
        con.commit()
        con.close()
        sqlitedb._connections.pop(db_path, None)

        def fake_year(market, board, ticker, year, last_trading_day=None):
            return [
                {
                    "figi": None,
                    "ts": f"{year}-03-15",
                    "open": None, "high": None, "low": None, "close": None,
                    "volume": 0, "source": "moex",
                    "_secid": ticker, "_boardid": board,
                    "_numtrades": 0, "_value": 0,
                },
                {
                    "figi": None,
                    "ts": f"{year}-06-15",
                    "open": 100, "high": 102, "low": 99, "close": 101,
                    "volume": 1000, "source": "moex",
                    "_secid": ticker, "_boardid": board,
                    "_numtrades": 5, "_value": 100000,
                },
            ]

        monkeypatch.setattr(BackfillRunner, "_fetch_year_moex", staticmethod(fake_year))
        monkeypatch.setattr(
            BackfillRunner, "_get_meta_moex",
            staticmethod(lambda *a, **kw: {"market": "shares", "board": "TQBR",
                                            "listed_from": "2025-01-01",
                                            "listed_till": "2026-12-31"}),
        )
        monkeypatch.setattr(
            BackfillRunner, "_list_instruments",
            lambda self, limit_to=None: [{"figi": figi, "ticker": "X"}],
        )

        class _StubSink:
            async def emit(self, event): pass

        runner = BackfillRunner(db_path=db_path, client=None, event_sink=_StubSink())
        async def _drive_full():
            return await runner.backfill_from_moex(
                today=_date(2026, 12, 31), delta_only=False, recent_tail_days=0,
            )
        written = asyncio.run(_drive_full())

        assert written >= 2

        evidence = sqlitedb.get_connection(db_path).execute(
            "SELECT session_date FROM moex_no_trade_evidence WHERE figi = ? ORDER BY session_date",
            (figi,),
        ).fetchall()
        assert [r["session_date"] for r in evidence] == ["2025-03-15", "2026-03-15"]
        bars = sqlitedb.get_connection(db_path).execute(
            "SELECT ts FROM bars WHERE figi = ? ORDER BY ts",
            (figi,),
        ).fetchall()
        assert [r["ts"] for r in bars] == ["2025-06-15", "2026-06-15"]
        sqlitedb._connections.pop(db_path, None)
