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


def _make_conn(db_path):
    """Open a file-backed SQLite connection for the duration of a test.

    Task 3: the public evidence wrappers acquire the shared writer
    lock via ``flock`` on ``<db_path>.writer.lock``. ``:memory:``
    has no backing file so the lock would be useless. Each test
    that exercises the public wrappers runs against a temp dir DB;
    the few private-tx tests that only need the SQL semantics may
    still pass an in-memory connection because the lock is not
    part of their contract.
    """
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE instruments (
            figi TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            isin TEXT,
            source_updated_at TEXT,
            expected_bars INTEGER,
            listed_till TEXT
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


def test_record_skips_dates_that_have_real_bars(tmp_path):
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    _insert_bar(con, "FIGI1", "2026-09-28")
    written = record_no_trade_evidence(
        con,
        db_path=str(db),
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


def test_record_idempotent_on_conflict_refreshes_timestamps(tmp_path):
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    today = date(2026, 9, 28)
    rows = [{"ts": "2026-09-26"}]

    record_no_trade_evidence(
        con, db_path=str(db), figi="FIGI1", rows=rows, board="TQCB", isin="X",
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
        con, db_path=str(db), figi="FIGI1", rows=rows, board="TQCB", isin="X",
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


def test_record_assigns_recent_vs_historical_expiry(tmp_path):
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
        RECENT_EVIDENCE_EXPIRY,
        HISTORICAL_EVIDENCE_EXPIRY,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    today = date(2026, 9, 28)
    record_no_trade_evidence(
        con,
        db_path=str(db),
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


def test_reconcile_removes_evidence_when_bar_lands(tmp_path):
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
        reconcile_no_trade_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    record_no_trade_evidence(
        con,
        db_path=str(db),
        figi="FIGI1",
        rows=[{"ts": "2026-09-26"}, {"ts": "2026-09-27"}],
        board="TQCB",
        isin="X",
    )
    _insert_bar(con, "FIGI1", "2026-09-26")
    removed = reconcile_no_trade_evidence(con, db_path=str(db))
    assert removed == 1
    remaining = con.execute(
        "SELECT session_date FROM moex_no_trade_evidence ORDER BY session_date"
    ).fetchall()
    assert [r["session_date"] for r in remaining] == ["2026-09-27"]


def test_reconcile_idempotent(tmp_path):
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
        reconcile_no_trade_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    record_no_trade_evidence(
        con,
        db_path=str(db),
        figi="FIGI1",
        rows=[{"ts": "2026-09-26"}],
        board="TQCB",
        isin="X",
    )
    _insert_bar(con, "FIGI1", "2026-09-26")
    reconcile_no_trade_evidence(con, db_path=str(db))
    # Second call must not raise or count any rows.
    assert reconcile_no_trade_evidence(con, db_path=str(db)) == 0


# -- load_no_trade_dates ---------------------------------------------------


def test_load_excludes_expired_rows(tmp_path):
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
        load_no_trade_dates,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    today = date(2026, 9, 28)
    record_no_trade_evidence(
        con,
        db_path=str(db),
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


def test_expected_sessions_subtracts_confirmed_evidence_and_holidays(tmp_path):
    from algotrader_api.ingestion.no_trade_evidence import (
        expected_sessions_for_figi,
        record_no_trade_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    # Saturday 2026-09-26 and Sunday 2026-09-27 already excluded by
    # weekday < 5. Monday 2026-09-28 is a regular session.
    con.execute(
        "INSERT INTO moex_holidays VALUES ('2026-09-29', 'X')"
    )
    record_no_trade_evidence(
        con,
        db_path=str(db),
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


def test_check_coverage_accepts_continuous_chain(tmp_path):
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
    )
    from algotrader_api.ml.features import check_coverage

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    con.execute(
        "INSERT INTO instruments(figi, ticker, isin, source_updated_at, expected_bars) "
        "VALUES ('FIGI1', 'X', 'X', '2025-01-01', 1)"
    )
    # Bars end on Friday 2026-09-25. Last completed session is the
    # following Monday 2026-09-28 (Tuesday is "today" — outside window).
    _insert_bar(con, "FIGI1", "2026-09-25")
    record_no_trade_evidence(
        con,
        db_path=str(db),
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


def test_check_coverage_rejects_partial_chain(tmp_path):
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
    )
    from algotrader_api.ml.features import check_coverage

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    con.execute(
        "INSERT INTO instruments(figi, ticker, isin, source_updated_at, expected_bars) "
        "VALUES ('FIGI1', 'X', 'X', '2025-01-01', 1)"
    )
    _insert_bar(con, "FIGI1", "2026-09-25")
    # Gap: only Mon evidence, no evidence for Tue (the last session).
    record_no_trade_evidence(
        con,
        db_path=str(db),
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


def test_check_coverage_keeps_incomplete_arm_separate(tmp_path):
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
    )
    from algotrader_api.ml.features import check_coverage

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    # Cached expected_bars artificially high; coverage ratio < 95%.
    con.execute(
        "INSERT INTO instruments(figi, ticker, isin, source_updated_at, expected_bars) "
        "VALUES ('FIGI1', 'X', 'X', '2025-01-01', 100)"
    )
    _insert_bar(con, "FIGI1", "2026-09-25")
    record_no_trade_evidence(
        con,
        db_path=str(db),
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


def test_check_coverage_treats_expired_evidence_as_unknown(tmp_path):
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
    )
    from algotrader_api.ml.features import check_coverage

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    con.execute(
        "INSERT INTO instruments(figi, ticker, isin, source_updated_at, expected_bars) "
        "VALUES ('FIGI1', 'X', 'X', '2025-01-01', 1)"
    )
    _insert_bar(con, "FIGI1", "2026-09-25")
    # Evidence recorded 30 days ago with a 7-day expiry → expired.
    record_no_trade_evidence(
        con,
        db_path=str(db),
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


# -- delisted instruments (listed_till, migration 026) ---------------------


def test_check_coverage_delisted_with_evidence_chain_not_stale(tmp_path):
    """A delisted figi whose last bar + evidence chain reach listed_till
    is complete and must not be flagged stale."""
    from algotrader_api.ingestion.no_trade_evidence import (
        record_no_trade_evidence,
    )
    from algotrader_api.ml.features import check_coverage

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    # Delisted 2026-09-10; last bar 2026-09-08; no-trade on 09-09 and 09-10.
    con.execute(
        "INSERT INTO instruments(figi, ticker, isin, source_updated_at, expected_bars, listed_till) "
        "VALUES ('FIGI1', 'X', 'X', '2025-01-01', 1, '2026-09-10')"
    )
    _insert_bar(con, "FIGI1", "2026-09-08")
    record_no_trade_evidence(
        con, db_path=str(db), figi="FIGI1",
        rows=[{"ts": "2026-09-09"}, {"ts": "2026-09-10"}],
        board="TQCB", isin="X",
    )
    # today far after the delisting
    import datetime as _dt
    today = date(2026, 10, 1)
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
    assert failing == [], f"expected no failure, got {failing}"


def test_check_coverage_delisted_without_evidence_is_stale():
    """A delisted figi with a gap between last bar and listed_till stays
    stale (no evidence, no bar)."""
    from algotrader_api.ml.features import check_coverage

    # The test does not call any public writer-lock-acquiring helper,
    # so the in-memory conn is fine here. (It does not assert
    # interprocess locking.)
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE instruments (
            figi TEXT PRIMARY KEY, ticker TEXT NOT NULL, isin TEXT,
            source_updated_at TEXT, expected_bars INTEGER, listed_till TEXT
        );
        CREATE TABLE bars (
            figi TEXT NOT NULL, ts TEXT NOT NULL,
            open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
            close REAL NOT NULL, volume INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'tinkoff',
            PRIMARY KEY (figi, ts)
        );
        CREATE TABLE moex_no_trade_evidence (
            figi TEXT NOT NULL, session_date TEXT NOT NULL,
            board TEXT NOT NULL, isin TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'moex_iss',
            observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL,
            UNIQUE (figi, session_date)
        );
        CREATE TABLE moex_holidays (date TEXT PRIMARY KEY, name TEXT NOT NULL);
    """)
    con.execute(
        "INSERT INTO instruments(figi, ticker, isin, source_updated_at, expected_bars, listed_till) "
        "VALUES ('FIGI1', 'X', 'X', '2025-01-01', 1, '2026-09-10')"
    )
    _insert_bar(con, "FIGI1", "2026-09-01")  # gap 09-02..09-10, no evidence
    import datetime as _dt
    today = date(2026, 10, 1)
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


def test_check_coverage_delisted_last_bar_on_listed_till_passes():
    """A delisted figi whose last bar IS the listed_till date passes with
    no evidence needed."""
    from algotrader_api.ml.features import check_coverage

    # No public writer-lock-acquiring helper is invoked here; the
    # in-memory conn keeps the test fast.
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE instruments (
            figi TEXT PRIMARY KEY, ticker TEXT NOT NULL, isin TEXT,
            source_updated_at TEXT, expected_bars INTEGER, listed_till TEXT
        );
        CREATE TABLE bars (
            figi TEXT NOT NULL, ts TEXT NOT NULL,
            open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
            close REAL NOT NULL, volume INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'tinkoff',
            PRIMARY KEY (figi, ts)
        );
        CREATE TABLE moex_no_trade_evidence (
            figi TEXT NOT NULL, session_date TEXT NOT NULL,
            board TEXT NOT NULL, isin TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'moex_iss',
            observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL,
            UNIQUE (figi, session_date)
        );
        CREATE TABLE moex_holidays (date TEXT PRIMARY KEY, name TEXT NOT NULL);
    """)
    con.execute(
        "INSERT INTO instruments(figi, ticker, isin, source_updated_at, expected_bars, listed_till) "
        "VALUES ('FIGI1', 'X', 'X', '2025-01-01', 1, '2026-09-10')"
    )
    _insert_bar(con, "FIGI1", "2026-09-10")
    import datetime as _dt
    today = date(2026, 10, 1)
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
    assert failing == [], f"expected no failure, got {failing}"


def test_check_coverage_without_listed_till_column_still_works():
    """Old databases without migration 026 keep the previous behaviour."""
    from algotrader_api.ml.features import check_coverage

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
            figi TEXT NOT NULL, ts TEXT NOT NULL,
            open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
            close REAL NOT NULL, volume INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'tinkoff',
            PRIMARY KEY (figi, ts)
        );
        CREATE TABLE moex_no_trade_evidence (
            figi TEXT NOT NULL, session_date TEXT NOT NULL,
            board TEXT NOT NULL, isin TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'moex_iss',
            observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL,
            UNIQUE (figi, session_date)
        );
        CREATE TABLE moex_holidays (date TEXT PRIMARY KEY, name TEXT NOT NULL);
    """)
    con.execute(
        "INSERT INTO instruments(figi, ticker, isin, source_updated_at, expected_bars) "
        "VALUES ('FIGI1', 'X', 'X', '2025-01-01', 1)"
    )
    con.execute(
        "INSERT INTO bars(figi, ts, open, high, low, close, volume, source) "
        "VALUES ('FIGI1', '2026-09-10', 1, 1, 1, 1, 1, 'tinkoff')"
    )
    import datetime as _dt
    today = date(2026, 10, 1)
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
    # No listed_till column -> cut-off is the plain last session
    # (2026-09-30); bar on 09-10 is stale.
    reasons = {f["figi"]: f["reason"] for f in failing}
    assert "stale" in reasons["FIGI1"]


def test_populate_script_bounds_by_listed_till_and_subtracts_evidence(tmp_path):
    """End-to-end: run the REAL populate script on a temp DB.

    expected_bars must be bounded by listed_till (delisted instrument)
    and reduced by confirmed no-trade evidence days inside the window.
    """
    import os
    import subprocess
    from pathlib import Path
    from datetime import date as _date

    db = tmp_path / "x.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE instruments (
            figi TEXT PRIMARY KEY, ticker TEXT NOT NULL, class TEXT NOT NULL,
            isin TEXT, source_updated_at TEXT, expected_bars INTEGER,
            listed_till TEXT
        );
        CREATE TABLE bars (
            figi TEXT NOT NULL, ts TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            source TEXT, PRIMARY KEY (figi, ts)
        );
        CREATE TABLE moex_holidays (date TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE moex_no_trade_evidence (
            figi TEXT NOT NULL, session_date TEXT NOT NULL,
            board TEXT NOT NULL, isin TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'moex_iss',
            observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL,
            UNIQUE (figi, session_date)
        );
    """)
    # Delisted fixture: listing 2025-01-01, delisted 2026-09-10.
    con.execute(
        "INSERT INTO instruments(figi, ticker, class, source_updated_at, listed_till) "
        "VALUES ('FIGI1', 'X', 'bond', '2025-01-01', '2026-09-10')"
    )
    # Evidence: two confirmed no-trade weekdays inside the window.
    con.executemany(
        "INSERT INTO moex_no_trade_evidence(figi, session_date, board, isin, expires_at) "
        "VALUES ('FIGI1', ?, 'TQCB', 'X', '2027-01-01')",
        [("2026-09-09",), ("2026-09-10",)],
    )
    con.commit()
    con.close()

    # Compute the raw business-day count the script would use WITHOUT
    # the two adjustments (same helper the script uses).
    import sys as _sys
    _sys.path.insert(0, "/home/hermes/algotrader/apps/api/src")
    from algotrader_api.ml.coverage import expected_business_days
    con2 = sqlite3.connect(str(db))
    base = expected_business_days(con2, _date(2025, 1, 1), _date(2026, 9, 10))
    con2.close()
    assert base > 2

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    r = subprocess.run(
        ["/home/hermes/algotrader/apps/api/.venv/bin/python",
         "/home/hermes/algotrader/apps/api/scripts/populate_expected_bars.py",
         "--db", str(db)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert r.returncode == 0, f"script failed: {r.stdout} {r.stderr}"

    con3 = sqlite3.connect(str(db))
    value = con3.execute("SELECT expected_bars FROM instruments WHERE figi='FIGI1'").fetchone()[0]
    con3.close()
    assert value == base - 2, (
        f"expected_bars={value}; want {base - 2} "
        f"(bounded by listed_till, minus 2 evidence days)"
    )


def test_populate_script_ignores_listed_till_before_listing(tmp_path):
    """A listed_till BEFORE the listing window is a MOEX board artefact
    for foreign securities traded via the broker (TSLA: MOEX boards
    closed 2020-09, bars through today). The guard must ignore it so
    expected_bars is not zeroed.
    """
    import os
    import subprocess
    from datetime import date as _date

    db = tmp_path / "y.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE instruments (
            figi TEXT PRIMARY KEY, ticker TEXT NOT NULL, class TEXT NOT NULL,
            isin TEXT, source_updated_at TEXT, expected_bars INTEGER,
            listed_till TEXT
        );
        CREATE TABLE bars (
            figi TEXT NOT NULL, ts TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            source TEXT, PRIMARY KEY (figi, ts)
        );
        CREATE TABLE moex_holidays (date TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE moex_no_trade_evidence (
            figi TEXT NOT NULL, session_date TEXT NOT NULL,
            board TEXT NOT NULL, isin TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'moex_iss',
            observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL,
            UNIQUE (figi, session_date)
        );
    """)
    # TSLA-like: first bar 2025-08-18, MOEX listed_till 2020-09-07.
    con.execute(
        "INSERT INTO instruments(figi, ticker, class, source_updated_at, listed_till) "
        "VALUES ('FIGI1', 'TSLA', 'share', '2025-08-18', '2020-09-07')"
    )
    con.execute(
        "INSERT INTO bars(figi, ts, open, high, low, close, volume, source) "
        "VALUES ('FIGI1', '2025-08-18', 1, 1, 1, 1, 1, 'tinkoff')"
    )
    con.commit()
    con.close()

    env = dict(os.environ); env.pop("PYTHONPATH", None)
    r = subprocess.run(
        ["/home/hermes/algotrader/apps/api/.venv/bin/python",
         "/home/hermes/algotrader/apps/api/scripts/populate_expected_bars.py",
         "--db", str(db)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert r.returncode == 0, f"{r.stdout} {r.stderr}"

    import sys as _sys
    _sys.path.insert(0, "/home/hermes/algotrader/apps/api/src")
    from algotrader_api.ml.coverage import expected_business_days
    from datetime import timedelta as _td
    con2 = sqlite3.connect(str(db))
    value = con2.execute("SELECT expected_bars FROM instruments WHERE figi='FIGI1'").fetchone()[0]
    con2.close()
    want = expected_business_days(
        sqlite3.connect(str(db)), _date(2025, 8, 18),
        _date.today() - _td(days=1),
    )
    assert value == want, f"expected_bars={value}, want {want} (guard must ignore 2020 listed_till)"


# -- MOEXFetchOutcome (Task 1) -------------------------------------------


def test_fetch_year_moex_outcome_complete_full_pagination():
    """Single-page full cursor yields outcome == 'complete'."""
    from algotrader_api.ingestion import backfill
    from algotrader_api.ingestion.no_trade_evidence import (
        MOEXFetchOutcome,
    )

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [
                ["2025-09-29", "GAZP", "TQBR",
                 None, None, None, None, 0, 0, 0],
                ["2025-09-30", "GAZP", "TQBR",
                 100, 102, 99, 101, 1000, 5, 100000],
            ],
        },
        # cursor: offset 0, total 2, page_size 2 (loop asked for 500,
        # the loop is satisfied because 0 + 2 >= 2).
        "history.cursor": {"data": [[0, 2, 2]]},
    }

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "complete"
    assert len(rows) == 2
    assert rows[0]["_secid"] == "GAZP"


def test_fetch_year_moex_outcome_error_on_network_failure():
    """requests.get raising -> outcome == 'error', rows == []."""
    from algotrader_api.ingestion import backfill
    from algotrader_api.ingestion.no_trade_evidence import (
        MOEXFetchOutcome,
    )

    import unittest.mock as _mock

    def boom(*a, **kw):
        raise ConnectionError("net")

    with _mock.patch.object(backfill.requests, "get",
                            side_effect=boom):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "error"
    assert rows == []


def test_fetch_year_moex_outcome_malformed_missing_tradedate():
    """history.columns missing TRADEDATE -> 'malformed'."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["SECID", "BOARDID", "OPEN", "CLOSE"],
            "data": [["GAZP", "TQBR", 100, 101]],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"
    assert rows == []


def test_fetch_year_moex_outcome_short_page_no_cursor_is_malformed():
    """No cursor + short page cannot certify completeness."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID"],
            "data": [["2025-09-29", "GAZP", "TQBR"]],
        },
        # No history.cursor; len(rows) < page_size (500).
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"


def test_fetch_year_moex_outcome_partial_incomplete_cursor():
    """True 2-page partial: cursor advances on page 1, the final page's
    ``offset + len(rows) < total`` makes the per-fetch outcome ``partial``.

    Genuine incomplete pagination — the cursor MUST advance (the spec
    forbids repeated offsets; the dedicated repeated-offset test below
    pins that rule separately). The first page returns a full 100 rows
    with offset 0; the second page advances to offset 100 and returns
    only 50 rows against a promised total of 300. The per-fetch
    outcome becomes ``partial`` because the final page can't close the
    loop.
    """
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    page1 = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [["2025-09-29", "GAZP", "TQBR",
                      100, 102, 99, 101, 1000, 5, 100000]] * 100,
        },
        # offset 0, total 300, page_size 100 — full first page, more
        # to come.
        "history.cursor": {"data": [[0, 300, 100]]},
    }
    page2 = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [["2025-09-30", "GAZP", "TQBR",
                      100, 102, 99, 101, 1000, 5, 100000]] * 50,
        },
        # offset 100 (advanced), total 300, page_size 100 — 50 rows
        # returned, 100+50=150 < 300 → partial final page.
        "history.cursor": {"data": [[100, 300, 100]]},
    }
    responses = [page1, page2]

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(responses.pop(0))):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "partial"
    assert len(rows) == 150
    assert responses == [], "loop did not advance past page 2"


def test_fetch_year_moex_outcome_multipage_complete():
    """True multi-page completion: ≥2 pages with advancing cursor that
    closes the loop on the final page.

    Distinct from ``test_fetch_year_moex_outcome_complete_full_pagination``
    (which is single-page disguised — 2 rows returned, cursor closes
    immediately). This test forces the loop to actually walk past page 1
    and verify it terminates on the final page with a full
    ``offset + len(rows) == total`` close.
    """
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    page1 = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [["2025-09-29", "GAZP", "TQBR",
                      100, 102, 99, 101, 1000, 5, 100000]] * 100,
        },
        # offset 0, total 200, page_size 100 — full first page, more
        # to come.
        "history.cursor": {"data": [[0, 200, 100]]},
    }
    page2 = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [["2025-09-30", "GAZP", "TQBR",
                      100, 102, 99, 101, 1000, 5, 100000]] * 100,
        },
        # offset 100 (advanced), total 200, page_size 100 — 100 rows
        # returned, 100+100=200 >= 200 → loop closes, outcome complete.
        "history.cursor": {"data": [[100, 200, 100]]},
    }
    responses = [page1, page2]

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(responses.pop(0))):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "complete"
    assert len(rows) == 200
    assert responses == [], "loop did not advance past page 2"


def test_fetch_year_moex_outcome_identity_mismatch_poisons_batch():
    """A single cross-listed mirror row poisons the whole fetch."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [
                ["2025-09-29", "GAZP", "TQBR",
                 100, 102, 99, 101, 1000, 5, 100000],
                # identity mismatch — single row, batch poisoned.
                ["2025-09-30", "SBER", "TQBR",
                 200, 202, 199, 201, 2000, 10, 200000],
            ],
        },
        "history.cursor": {"data": [[0, 2, 2]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "identity_mismatch"
    assert len(rows) == 2


def test_fetch_year_moex_outcome_worst_severity_wins():
    """First page error forces outcome == 'error' even if subsequent
    pages parse.
    """
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    call_count = {"n": 0}

    def fake_get(url, params=None, timeout=None):  # noqa: ARG001
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ConnectionError("net")
        return _FakeResp({
            "history": {
                "columns": ["TRADEDATE", "SECID", "BOARDID"],
                "data": [["2025-09-29", "GAZP", "TQBR"]],
            },
            "history.cursor": {"data": [[0, 1, 1]]},
        })

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=fake_get):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "error"
    assert rows == []


def test_fetch_year_moex_outcome_non_200_status_is_malformed():
    """HTTP 500 is malformed, not complete, even if body parses."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 500

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID"],
            "data": [["2025-09-29", "GAZP", "TQBR"]],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"
    assert rows == []


def test_fetch_year_moex_outcome_initial_cursor_offset_mismatch_is_malformed():
    """Loop asks for start=0, server reports offset=2 -> malformed."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID"],
            "data": [["2025-09-29", "GAZP", "TQBR"]],
        },
        # offset 2, total 3, page_size 1 -> loop asked start=0.
        "history.cursor": {"data": [[2, 3, 1]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"


def test_fetch_year_moex_outcome_repeated_cursor_offset_is_malformed():
    """Two pages with the same cursor offset -> malformed."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    page = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID"],
            "data": [["2025-09-29", "GAZP", "TQBR"]],
        },
        # Same offset on every page — no progress, malformed.
        "history.cursor": {"data": [[0, 999, 1]]},
    }

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(page)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"


def test_fetch_year_moex_outcome_missing_ohlc_nonzero_volume_is_malformed():
    """OPEN/HIGH/LOW/CLOSE absent on a non-zero VOLUME row -> malformed."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    # Columns list includes all OHLC + VOLUME, but the row is short
    # on the OHLC side (None) AND has a non-zero VOLUME.
    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [
                ["2025-09-29", "GAZP", "TQBR",
                 None, None, None, None, 1000, 5, 100000],
            ],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"


def test_fetch_year_moex_outcome_zero_trade_missing_counters_is_malformed():
    """VOLUME=0 row without explicit NUMTRADES=0/VALUE=0 -> malformed."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [
                # NUMTRADES / VALUE missing (None) on a zero-volume
                # row — must be malformed, not complete.
                ["2025-09-29", "GAZP", "TQBR",
                 None, None, None, None, 0, None, None],
            ],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"


# -- Task1 R2: strict raw-row poison (parent repro) ---------------------


def test_fetch_year_moex_outcome_malformed_short_row_poisons_outcome():
    """Parent repro: 1 valid row + 1 short row (len 9 vs cols 10). The
    short row must poison the outcome to ``malformed`` BEFORE the loop
    continues, and the valid row is preserved in the returned list
    (bar consumers keep their data, evidence path is refused).

    Without the fix: outcome=complete, len(rows)=1 (silent drop on
    the short row, cursor close still reads complete). Spec demands
    malformed.
    """
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    valid_row = ["2025-09-01", "GAZP", "TQBR",
                 None, None, None, None, 0, 0, 0]
    # short_row: drops the last column (VALUE) — len 9 vs cols 10.
    short_row = valid_row[:-1]
    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [valid_row, short_row],
        },
        # Raw length 2, but kept length 1. Spec: any malformed raw row
        # poisons outcome — cursor close is irrelevant.
        "history.cursor": {"data": [[0, 1, 1]]},
    }

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed", f"want malformed, got {outcome!r}"
    # The valid clean row is preserved — bar consumers still get the
    # data they would have accepted today.
    assert len(rows) == 1
    assert rows[0]["_secid"] == "GAZP"


def test_fetch_year_moex_outcome_malformed_long_row_poisons_outcome():
    """A row LONGER than the columns list must also poison the outcome
    (the strict feed contract is column-list-anchored, not "row shorter
    than cols only"). Kept clean row preserved.
    """
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    valid_row = ["2025-09-01", "GAZP", "TQBR",
                 None, None, None, None, 0, 0, 0]
    long_row = valid_row + [99]  # one extra trailing value
    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [valid_row, long_row],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed", f"want malformed, got {outcome!r}"
    assert len(rows) == 1
    assert rows[0]["_secid"] == "GAZP"


def test_fetch_year_moex_outcome_row_is_none_does_not_crash():
    """A row whose value is ``None`` (where the strict contract expects a
    scalar) must not crash the parser; it counts as malformed and poisons
    the outcome. The remaining valid rows are preserved.
    """
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    valid_row = ["2025-09-01", "GAZP", "TQBR",
                 None, None, None, None, 0, 0, 0]
    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            # Second "row" is the literal None — common when a server
            # pads an empty slot. Must not crash; must poison outcome.
            "data": [valid_row, None],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed", f"want malformed, got {outcome!r}"
    assert len(rows) == 1
    assert rows[0]["_secid"] == "GAZP"


def test_fetch_year_moex_outcome_row_is_string_does_not_crash():
    """A row whose value is a string (where the contract expects a
    sequence) must not crash. The length check catches it cleanly:
    ``len("a string") == 8`` which is shorter than the columns list
    (10), so it drops + poisons. No exception bubbles to the caller.
    """
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    valid_row = ["2025-09-01", "GAZP", "TQBR",
                 None, None, None, None, 0, 0, 0]
    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [valid_row, "not-a-row"],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed", f"want malformed, got {outcome!r}"
    assert len(rows) == 1
    assert rows[0]["_secid"] == "GAZP"


def test_fetch_year_moex_outcome_cursor_offset_wrong_type_is_malformed():
    """Spec cursor: ``[offset, total, page_size]`` integers only. A
    fractional ``offset`` (``0.5``) is malformed — we cannot use a
    non-integer as a pagination index. Bool, float fraction, None are
    all malformed. The kept clean row is preserved.
    """
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    valid_row = ["2025-09-01", "GAZP", "TQBR",
                 None, None, None, None, 0, 0, 0]
    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [valid_row],
        },
        # offset is a fraction — the strict cursor contract is integer.
        "history.cursor": {"data": [[0.5, 1, 1]]},
    }

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed", f"want malformed, got {outcome!r}"
    assert len(rows) == 1


def test_fetch_year_moex_outcome_cursor_page_size_inconsistent_is_malformed():
    """F1: cursor promises page_size=100 but the server returned 200
    rows on the same page. Spec line 30-31: page_size consistency check
    → malformed. Kept clean rows preserved.

    Independent of the parent repro: this is the F1 page_size
    consistency guard.
    """
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    valid_row = ["2025-09-29", "GAZP", "TQBR",
                 100, 102, 99, 101, 1000, 5, 100000]
    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [valid_row] * 200,  # 200 rows
        },
        # cursor says page_size=100; server returned 200. Inconsistent.
        "history.cursor": {"data": [[0, 200, 100]]},
    }

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed", f"want malformed, got {outcome!r}"


def test_fetch_year_moex_outcome_missing_status_code_is_malformed():
    """F3: a response with no ``status_code`` attribute at all must
    fail closed to ``malformed`` (cannot certify HTTP success without
    the attribute). The legacy test that omits status_code goes
    through the bar-list wrapper, which never reads status_code, so
    that test is unaffected.
    """
    from algotrader_api.ingestion import backfill

    class _FakeRespNoStatus:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    valid_row = ["2025-09-01", "GAZP", "TQBR",
                 None, None, None, None, 0, 0, 0]
    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [valid_row],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeRespNoStatus(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed", f"want malformed, got {outcome!r}"


def test_fetch_year_moex_outcome_fractional_volume_is_malformed():
    """F4: a non-integer VOLUME (e.g. 1000.5) is malformed. The strict
    contract requires integer share counts; ``int(1000.5) == 1000``
    would silently truncate. Reject fractional VOLUME explicitly.
    """
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [
                ["2025-09-29", "GAZP", "TQBR",
                 100, 102, 99, 101, 1000.5, 5, 100000],
            ],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed", f"want malformed, got {outcome!r}"


# -- bar-list compatibility (Step 2 / Step 3) --------------------------


def test_fetch_year_moex_list_only_signature_preserved_positional():
    """The existing list-only path keeps the
    positional-or-keyword ``last_trading_day`` signature; callers
    can pass it positionally or by keyword.
    """
    from algotrader_api.ingestion import backfill
    import datetime as _dt

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [
                ["2025-09-29", "GAZP", "TQBR",
                 None, None, None, None, 0, 0, 0],
            ],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        # last_trading_day passed POSITIONALLY (legacy call style).
        rows = backfill._fetch_year_moex(
            "shares", "TQBR", "GAZP", 2025, _dt.date(2025, 9, 29),
        )
    assert len(rows) == 1
    assert rows[0]["_secid"] == "GAZP"
    assert rows[0]["_boardid"] == "TQBR"


def test_fetch_year_moex_outcome_emits_raw_columns_per_dict():
    """Regression: the existing raw-columns test must still pass after
    the outcome addition; the outcome variant agrees on shape.
    """
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [
                ["2025-09-29", "GAZP", "TQBR",
                 None, None, None, None, 0, 0, 0],
            ],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "complete"
    assert rows[0]["_secid"] == "GAZP"
    assert rows[0]["_numtrades"] == 0
