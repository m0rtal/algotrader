"""Tests for the calendar-aware trailing-gap pass added in PR #101.

These cover the new helpers (``_load_holiday_dates``,
``_load_restricted_dates``, ``_trailing_trading_days``,
``_collect_trailing_gaps``) plus the routing detail-string format of
``_step_gap_recovery`` for the trailing segment. The pre-existing
``test_worker_daily_chain.py`` mocks ``find_gaps``/``recover_gaps`` and
so cannot exercise the trailing path at all.

The test DBs are built with the same migrations the production worker
uses so the schema (``bars``, ``instruments``, ``moex_holidays``,
``restricted_periods``) is the same shape the worker sees at runtime.
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations


# Ensure apps/api/src AND apps/api are importable when pytest is invoked
# from the repo root (apps/api itself is the worker.py location).
_API_ROOT = Path(__file__).resolve().parent.parent
_API_SRC = _API_ROOT / "src"
for _p in (str(_API_SRC), str(_API_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _import_worker():
    """Reload the worker module fresh so patched symbols stick."""
    sys.modules.pop("worker", None)
    return importlib.import_module("worker")


@pytest.fixture
def db(tmp_path):
    """Fresh state.db with the full migration stack applied."""
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    return p


def _seed_bar(cur, figi: str, ts: str, **fields) -> None:
    cur.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            figi, ts,
            fields.get("open", 1.0),
            fields.get("high", 1.0),
            fields.get("low", 1.0),
            fields.get("close", 1.0),
            fields.get("volume", 0),
        ),
    )


def _seed_instrument(cur, figi: str, ticker: str) -> None:
    cur.execute(
        "INSERT INTO instruments "
        "(ticker, figi, class, name, currency, lot_size) "
        "VALUES (?, ?, 'stock', ?, 'RUB', 1)",
        (ticker, figi, ticker),
    )


def _seed_holiday(cur, d: str, name: str = "holiday") -> None:
    cur.execute(
        "INSERT INTO moex_holidays (date, name) VALUES (?, ?)",
        (d, name),
    )


def _seed_restricted(cur, d: str, reason: str = "restricted") -> None:
    cur.execute(
        "INSERT INTO restricted_periods (date, reason) VALUES (?, ?)",
        (d, reason),
    )


def _row_connect(db_path: str):
    """Open a connection with sqlite3.Row factory — matches production."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


# --------------------------------------------------------------------------- #
# 1. _trailing_trading_days — pure function, no IO
# --------------------------------------------------------------------------- #


def test_trailing_trading_days_skips_weekend():
    """Friday → Monday returns only Monday (Sat/Sun filtered out)."""
    worker = _import_worker()
    days = worker._trailing_trading_days(
        last_ts=date(2026, 9, 18),  # Friday
        today=date(2026, 9, 21),    # Monday
        holidays=set(),
        restricted=set(),
    )
    assert days == [date(2026, 9, 21)]


def test_trailing_trading_days_skips_moex_holiday():
    """Monday holiday in (last_ts, today] must be filtered out."""
    worker = _import_worker()
    days = worker._trailing_trading_days(
        last_ts=date(2026, 9, 18),  # Friday
        today=date(2026, 9, 22),    # Tuesday
        holidays={date(2026, 9, 21)},
        restricted=set(),
    )
    assert days == [date(2026, 9, 22)]


def test_trailing_trading_days_skips_restricted_period():
    """Restricted-period dates are filtered out alongside holidays.

    Window is strictly past last_ts, so (last_ts=Mon, today=Wed) covers
    Tue + Wed. With Tue restricted, only Wed survives.
    """
    worker = _import_worker()
    days = worker._trailing_trading_days(
        last_ts=date(2026, 9, 21),  # Monday
        today=date(2026, 9, 23),    # Wednesday
        holidays=set(),
        restricted={date(2026, 9, 22)},
    )
    assert days == [date(2026, 9, 23)]


def test_trailing_trading_days_empty_when_last_ts_equals_today():
    """No trailing work when the figi is already up to date."""
    worker = _import_worker()
    assert worker._trailing_trading_days(
        last_ts=date(2026, 9, 22),
        today=date(2026, 9, 22),
        holidays=set(),
        restricted=set(),
    ) == []


def test_trailing_trading_days_empty_when_last_ts_in_future():
    """Clock-skew guard: last_ts > today must not produce a negative window."""
    worker = _import_worker()
    assert worker._trailing_trading_days(
        last_ts=date(2026, 9, 23),
        today=date(2026, 9, 22),
        holidays=set(),
        restricted=set(),
    ) == []


def test_trailing_trading_days_full_week():
    """Mon→Fri (no holidays, no restricted) yields all five trading days."""
    worker = _import_worker()
    days = worker._trailing_trading_days(
        last_ts=date(2026, 9, 14),  # Mon
        today=date(2026, 9, 18),    # Fri
        holidays=set(),
        restricted=set(),
    )
    assert days == [
        date(2026, 9, 15),
        date(2026, 9, 16),
        date(2026, 9, 17),
        date(2026, 9, 18),
    ]


# --------------------------------------------------------------------------- #
# 2. _load_holiday_dates / _load_restricted_dates — schema-defensive queries
# --------------------------------------------------------------------------- #


def test_load_holiday_dates_returns_set(db):
    worker = _import_worker()
    with closing(_row_connect(db)) as con:
        _seed_holiday(con, "2026-09-21", "Independence Day")
        _seed_holiday(con, "2026-09-22", "Bridge Day")
        con.commit()
        with closing(_row_connect(db)) as c2:
            holidays = worker._load_holiday_dates(
                c2, date(2026, 9, 1), date(2026, 9, 30),
            )
    assert holidays == {date(2026, 9, 21), date(2026, 9, 22)}


def test_load_holiday_dates_returns_empty_when_table_missing(tmp_path):
    """Defensive contract: missing moex_holidays -> empty set, no crash."""
    worker = _import_worker()
    p = str(tmp_path / "bare.db")  # NO migrations applied
    with closing(_row_connect(p)) as con:
        holidays = worker._load_holiday_dates(
            con, date(2026, 9, 1), date(2026, 9, 30),
        )
    assert holidays == set()


def test_load_restricted_dates_returns_set(db):
    worker = _import_worker()
    with closing(_row_connect(db)) as con:
        _seed_restricted(con, "2026-09-21", "T+ settlement halt")
        con.commit()
        with closing(_row_connect(db)) as c2:
            restricted = worker._load_restricted_dates(
                c2, date(2026, 9, 1), date(2026, 9, 30),
            )
    assert restricted == {date(2026, 9, 21)}


def test_load_restricted_dates_returns_empty_when_table_missing(tmp_path):
    """Defensive contract: missing restricted_periods -> empty set, no crash.

    This is the bug fixed in this PR — pre-fix, the trailing pass raised
    sqlite3.OperationalError when restricted_periods was absent, even
    though ``_collect_trailing_gaps`` advertises defensive behaviour for
    all three tables (bars / moex_holidays / restricted_periods).
    """
    worker = _import_worker()
    p = str(tmp_path / "bare.db")  # NO migrations applied
    with closing(_row_connect(p)) as con:
        restricted = worker._load_restricted_dates(
            con, date(2026, 9, 1), date(2026, 9, 30),
        )
    assert restricted == set()


# --------------------------------------------------------------------------- #
# 3. _collect_trailing_gaps — end-to-end against an in-memory DB
# --------------------------------------------------------------------------- #


def test_collect_trailing_gaps_holiday_and_weekend_filter(db):
    """Friday last bar, weekend + Mon holiday, Tuesday today → Tue only.

    last_ts = Friday, today = Tuesday. The trailing window is Mon + Tue;
    Mon is filtered out as a holiday, so only Tue survives. Friday is
    4 days before today, so stale=False (boundary is 7 days).
    """
    worker = _import_worker()
    with closing(_row_connect(db)) as con:
        _seed_bar(con, "F1", "2026-09-18")  # Friday
        _seed_holiday(con, "2026-09-21")     # Monday holiday
        con.commit()
        trailing = worker._collect_trailing_gaps(db, date(2026, 9, 22))
    assert trailing == [("F1", date(2026, 9, 22), date(2026, 9, 22), False)]


def test_collect_trailing_gaps_respects_both_calendars(db):
    """A mid-week restricted date must be excluded along with holidays.

    last_ts = Mon 9-14, today = Fri 9-18. Window is Tue–Fri. Wed is a
    holiday, Thu is restricted. Surviving: Tue + Fri. last_ts is 4 days
    before today so stale=False (boundary is 7 days).
    """
    worker = _import_worker()
    with closing(_row_connect(db)) as con:
        _seed_bar(con, "F1", "2026-09-14")  # Monday
        _seed_holiday(con, "2026-09-16")    # Wednesday holiday
        _seed_restricted(con, "2026-09-17") # Thursday restricted
        con.commit()
        trailing = worker._collect_trailing_gaps(db, date(2026, 9, 18))
    figis = {t[0]: t for t in trailing}
    assert figis["F1"] == ("F1", date(2026, 9, 15), date(2026, 9, 18), False)


def test_collect_trailing_gaps_empty_when_last_ts_equals_today(db):
    """No trailing entries when the figi is already current."""
    worker = _import_worker()
    with closing(_row_connect(db)) as con:
        _seed_bar(con, "F1", "2026-09-22")  # Tuesday = today
        con.commit()
        trailing = worker._collect_trailing_gaps(db, date(2026, 9, 22))
    assert trailing == []


def test_collect_trailing_gaps_routing_stale_flag_at_7_day_boundary(db):
    """Boundary: last_ts == today - 7d is NOT stale (strict `<`)."""
    worker = _import_worker()
    today = date(2026, 9, 22)
    with closing(_row_connect(db)) as con:
        # Fresh: last_ts = today - 7 (boundary; should be routed to tinkoff)
        _seed_bar(con, "FRESH", "2026-09-15")
        # Stale: last_ts = today - 8 (routed to moex)
        _seed_bar(con, "STALE", "2026-09-14")
        con.commit()
        trailing = worker._collect_trailing_gaps(db, today)
    by_figi = {t[0]: t for t in trailing}
    assert by_figi["FRESH"][3] is False   # tinkoff
    assert by_figi["STALE"][3] is True    # moex


def test_collect_trailing_gaps_returns_empty_when_no_bars_table(tmp_path):
    """Bare DB (no schema) — trailing pass returns [], does not crash."""
    worker = _import_worker()
    p = str(tmp_path / "bare.db")
    trailing = worker._collect_trailing_gaps(p, date(2026, 9, 22))
    assert trailing == []


def test_collect_trailing_gaps_with_only_bars_table(tmp_path):
    """Bars exist but no calendar tables — still works (degrades gracefully).

    This is the regression case the PR #101 review flagged: pre-fix, a
    bare ``bars`` table plus no ``restricted_periods`` would raise
    OperationalError inside ``_load_restricted_dates``. The fix wraps
    that query in the same try/except as ``_load_holiday_dates``.
    """
    worker = _import_worker()
    p = str(tmp_path / "partial.db")
    with closing(_row_connect(p)) as con:
        con.execute(
            "CREATE TABLE bars ("
            "  figi TEXT NOT NULL,"
            "  ts DATE NOT NULL,"
            "  open REAL, high REAL, low REAL, close REAL, volume INTEGER,"
            "  PRIMARY KEY (figi, ts)"
            ")"
        )
        _seed_bar(con, "F1", "2026-09-18")
        con.commit()
        trailing = worker._collect_trailing_gaps(p, date(2026, 9, 22))
    # Mon=21 is a normal trading day (no calendars → treat as trading day).
    # last_ts = Fri 9-18 is 4 days before today, so stale=False (boundary 7d).
    assert trailing == [("F1", date(2026, 9, 21), date(2026, 9, 22), False)]


# --------------------------------------------------------------------------- #
# 4. _step_gap_recovery — routing detail-string format
# --------------------------------------------------------------------------- #


def _stub_runner_with_recorder(records: list, n_returned: int = 1):
    """Build a fake BackfillRunner whose _backfill_one appends to records.

    Matches the keyword names used by the worker's call site
    (``figi``, ``ticker``, ``from_``, ``to``, ``source``).
    """
    runner = MagicMock()

    async def _backfill_one(*, figi, ticker, from_, to, source):
        records.append((figi, from_, to, source))
        return n_returned

    runner._backfill_one.side_effect = _backfill_one
    return runner


def test_step_gap_recovery_routes_fresh_figi_to_tinkoff(tmp_path):
    """last_ts = today - 1d → tinkoff (recent, no need for MOEX bridge)."""
    worker = _import_worker()
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    today = date(2026, 9, 22)
    with closing(_row_connect(p)) as con:
        _seed_bar(con, "F1", "2026-09-21")
        _seed_instrument(con, "F1", "TKR1")
        con.commit()

    fake_gr = MagicMock()
    fake_gr.find_gaps.return_value = []

    records: list = []
    runner = _stub_runner_with_recorder(records, n_returned=1)
    fake_br = MagicMock()
    fake_br.BackfillRunner.return_value = runner
    fake_client_mod = MagicMock()
    fake_client_mod.make_client.return_value = MagicMock()

    with patch("algotrader_api.ingestion.client.make_client",
               return_value=MagicMock()), \
         patch.dict(sys.modules, {
             "algotrader_api.data_quality.gap_recovery": fake_gr,
             "algotrader_api.ingestion.backfill": fake_br,
         }), \
         patch.object(worker, "client_mod", fake_client_mod):
        ok, detail = worker._step_gap_recovery(p)

    assert ok is True
    assert "trailing=" in detail
    assert "tinkoff=1" in detail
    # Routing assertion: fresh figi -> tinkoff source.
    assert records and records[0][3] == "tinkoff"


def test_step_gap_recovery_routes_stale_figi_to_moex(tmp_path):
    """last_ts = today - 30d → moex (prefer MOEX ISS for stale figis)."""
    worker = _import_worker()
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    today = date(2026, 9, 22)
    with closing(_row_connect(p)) as con:
        _seed_bar(con, "S1", "2026-08-23")
        _seed_instrument(con, "S1", "STALE")
        con.commit()

    fake_gr = MagicMock()
    fake_gr.find_gaps.return_value = []
    records: list = []
    runner = _stub_runner_with_recorder(records, n_returned=3)
    fake_br = MagicMock()
    fake_br.BackfillRunner.return_value = runner
    fake_client_mod = MagicMock()
    fake_client_mod.make_client.return_value = MagicMock()

    with patch("algotrader_api.ingestion.client.make_client",
               return_value=MagicMock()), \
         patch.dict(sys.modules, {
             "algotrader_api.data_quality.gap_recovery": fake_gr,
             "algotrader_api.ingestion.backfill": fake_br,
         }), \
         patch.object(worker, "client_mod", fake_client_mod):
        ok, detail = worker._step_gap_recovery(p)

    assert ok is True
    assert "moex=3" in detail
    assert records and records[0][3] == "moex"


def test_step_gap_recovery_detail_combines_historical_and_trailing(tmp_path):
    """Detail string reports both historical and trailing segments."""
    worker = _import_worker()
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    today = date(2026, 9, 22)
    with closing(_row_connect(p)) as con:
        _seed_bar(con, "S1", "2026-08-23")
        _seed_instrument(con, "S1", "STALE")
        con.commit()

    fake_gr = MagicMock()
    fake_gr.find_gaps.return_value = ["gap1", "gap2"]

    async def _fake_recover(*args, **kwargs):
        return {"gap1": 4, "gap2": 6}

    fake_gr.recover_gaps.side_effect = _fake_recover

    records: list = []
    runner = _stub_runner_with_recorder(records, n_returned=2)
    fake_br = MagicMock()
    fake_br.BackfillRunner.return_value = runner
    fake_client_mod = MagicMock()
    fake_client_mod.make_client.return_value = MagicMock()

    with patch("algotrader_api.ingestion.client.make_client",
               return_value=MagicMock()), \
         patch.dict(sys.modules, {
             "algotrader_api.data_quality.gap_recovery": fake_gr,
             "algotrader_api.ingestion.backfill": fake_br,
         }), \
         patch.object(worker, "client_mod", fake_client_mod):
        ok, detail = worker._step_gap_recovery(p)

    assert ok is True
    # historical=10 + trailing=2 = 12 bars; segments reported separately.
    assert "12 bars filled" in detail
    assert "historical=10 across 2 gaps" in detail
    assert "trailing=2 across 1 figis" in detail
    assert "moex=2" in detail
    assert "tinkoff=0" in detail


def test_step_gap_recovery_skips_figi_without_ticker(tmp_path):
    """No-ticker warning: figi present in bars but missing from instruments."""
    worker = _import_worker()
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    today = date(2026, 9, 22)
    with closing(_row_connect(p)) as con:
        _seed_bar(con, "ORPHAN", "2026-08-23")
        # NOTE: no instruments row for ORPHAN
        con.commit()

    fake_gr = MagicMock()
    fake_gr.find_gaps.return_value = []
    records: list = []
    runner = _stub_runner_with_recorder(records)
    fake_br = MagicMock()
    fake_br.BackfillRunner.return_value = runner
    fake_client_mod = MagicMock()
    fake_client_mod.make_client.return_value = MagicMock()

    with patch("algotrader_api.ingestion.client.make_client",
               return_value=MagicMock()), \
         patch.dict(sys.modules, {
             "algotrader_api.data_quality.gap_recovery": fake_gr,
             "algotrader_api.ingestion.backfill": fake_br,
         }), \
         patch.object(worker, "client_mod", fake_client_mod):
        ok, detail = worker._step_gap_recovery(p)

    assert ok is True
    # No backfill_one call (no ticker to resolve), so 0 trailing bars.
    assert records == []
    assert "trailing=0" in detail