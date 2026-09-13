"""Tests for the data-quality completeness module."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

import pytest

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations
from algotrader_api.data_quality.completeness import find_gap_intervals


@pytest.fixture
def db(tmp_path):
    """A fresh state.db with the schema applied (including moex_holidays)."""
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    return p


def _seed_instrument_and_bars(db, figi, dates):
    from algotrader_api.db.sqlite import get_connection
    cur = get_connection(db).cursor()
    cur.execute(
        "INSERT INTO instruments(ticker, figi, class, name, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("T" + figi[-3:], figi, "share", figi, "RUB", 1),
    )
    cur.executemany(
        "INSERT INTO bars(figi, ts, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
        [(figi, d, 100, 101, 99, 100, 1000) for d in dates],
    )
    cur.connection.commit()


def _seed_holidays(db, dates):
    from algotrader_api.db.sqlite import get_connection
    cur = get_connection(db).cursor()
    cur.executemany(
        "INSERT OR REPLACE INTO moex_holidays(date, name) VALUES (?, ?)",
        [(d, "test") for d in dates],
    )
    cur.connection.commit()


def test_find_gap_intervals_returns_empty_for_complete_history(db):
    figi = "FIGI-FULL"
    _seed_instrument_and_bars(db, figi, [
        "2024-01-10", "2024-01-15", "2024-01-22", "2024-01-29",
    ])
    gaps = find_gap_intervals(db, figi, min_gap_days=5)
    assert gaps == []


def test_find_gap_intervals_returns_gap_for_14_day_hole(db):
    figi = "FIGI-GAP"
    _seed_instrument_and_bars(db, figi, [
        "2024-03-01",   # before the gap
        "2024-03-22",   # after the gap (21 calendar days = 14 trading days)
    ])
    gaps = find_gap_intervals(db, figi, min_gap_days=5)
    assert len(gaps) == 1
    start, end = gaps[0]
    assert start == date(2024, 3, 1)
    assert end == date(2024, 3, 22)


def test_find_gap_intervals_ignores_weekend_only_gap(db):
    figi = "FIGI-WEEKEND"
    # Friday → Monday: 3 calendar days, 1 trading day — below threshold
    _seed_instrument_and_bars(db, figi, [
        "2024-03-08",  # Friday
        "2024-03-11",  # Monday
    ])
    gaps = find_gap_intervals(db, figi, min_gap_days=5)
    assert gaps == []


def test_find_gap_intervals_handles_holiday_in_gap(db):
    figi = "FIGI-HOL"
    # 8 calendar days, 6 weekdays, but with a 3-day MOEX holiday inside: 3 trading days
    # — still below threshold.
    _seed_holidays(db, ["2024-03-12", "2024-03-13", "2024-03-14"])
    _seed_instrument_and_bars(db, figi, [
        "2024-03-08",  # Friday
        "2024-03-18",  # Monday (8 calendar days)
    ])
    gaps = find_gap_intervals(db, figi, min_gap_days=5)
    assert gaps == []  # holiday adjustment brought it below threshold


def test_find_gap_intervals_returns_empty_for_single_bar(db):
    figi = "FIGI-SINGLE"
    _seed_instrument_and_bars(db, figi, ["2024-03-08"])
    assert find_gap_intervals(db, figi, min_gap_days=5) == []


def test_find_gap_intervals_is_idempotent(db):
    figi = "FIGI-IDEMP"
    _seed_instrument_and_bars(db, figi, [
        "2024-03-01",
        "2024-03-22",
    ])
    first = find_gap_intervals(db, figi, min_gap_days=5)
    second = find_gap_intervals(db, figi, min_gap_days=5)
    assert first == second


# ---------------------------------------------------------------------------
# Tests for backfill_gaps / run_completeness_pass (Task 4)
# ---------------------------------------------------------------------------


@dataclass
class _FakeClient:
    """Minimal async get_candles stub for backfill_gaps tests.

    Implements the same protocol surface `backfill_gaps` relies on
    (only `get_candles`); records every call so tests can assert
    that the broker was queried exactly once per gap.
    """

    responses: dict[tuple[str, str], list[dict]]
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    async def get_candles(
        self,
        *,
        figi: str,
        date_from: str,
        date_to: str,
        interval: str = "CANDLE_INTERVAL_DAY",
    ) -> list[dict]:
        self.calls.append((figi, date_from, date_to))
        return list(self.responses.get((figi, date_from), []))


def _make_report(figi: str, *, has_incomplete_history: bool, ticker: str = "T"):
    """Build a HealthReport minimal for the completeness pass."""
    from algotrader_api.data_quality.health import HealthIssue, HealthReport

    issues: list[HealthIssue] = []
    if has_incomplete_history:
        issues.append(HealthIssue.INCOMPLETE_HISTORY)
    return HealthReport(
        figi=figi,
        ticker=ticker,
        health_score=50 if has_incomplete_history else 100,
        issues=issues,
    )


def _seed_figi_metadata(db, figi, *, last_run_status="ok", total_bars=0):
    from algotrader_api.db.sqlite import get_connection

    cur = get_connection(db).cursor()
    cur.execute(
        "INSERT OR REPLACE INTO instrument_metadata("
        "figi, last_run_status, total_bars) VALUES (?, ?, ?)",
        (figi, last_run_status, total_bars),
    )
    cur.connection.commit()


@pytest.mark.asyncio
async def test_backfill_gaps_writes_only_in_gap_range(db):
    """backfill_gaps fills the gap range and leaves bars outside it alone.

    Setup: figi has bars on 2024-03-01 and 2024-03-22 — a 14-trading-day
    hole. The fake client returns 5 candles in between. After the call,
    the figi should have 7 bars total, and the dates outside [Mar 2,
    Mar 22] must still be exactly the originals (100, 101, 99, 100, 1000).
    """
    figi = "FIGI-FILL"
    _seed_instrument_and_bars(db, figi, [
        "2024-03-01",  # before the gap (must remain untouched)
        "2024-03-22",  # after the gap (must remain untouched)
    ])
    _seed_figi_metadata(db, figi)

    # Fake client: the broker returns 5 bars covering the gap.
    gap_candles = [
        {"ts": "2024-03-04", "open": 110, "high": 111, "low": 109, "close": 110, "volume": 100},
        {"ts": "2024-03-05", "open": 112, "high": 113, "low": 111, "close": 112, "volume": 200},
        {"ts": "2024-03-06", "open": 113, "high": 114, "low": 112, "close": 113, "volume": 300},
        {"ts": "2024-03-07", "open": 114, "high": 115, "low": 113, "close": 114, "volume": 400},
        {"ts": "2024-03-08", "open": 115, "high": 116, "low": 114, "close": 115, "volume": 500},
    ]
    client = _FakeClient(
        responses={
            (figi, "2024-03-02"): [
                c for c in gap_candles if c["ts"] >= "2024-03-02"
            ],
        },
    )

    from algotrader_api.data_quality.completeness import backfill_gaps

    added = await backfill_gaps(client, figi, [(date(2024, 3, 1), date(2024, 3, 22))], db)

    assert added == 5, "expected exactly the 5 new candles to be inserted"
    # One broker call, with the correct date_from (= start + 1 day) and date_to (= end).
    assert client.calls == [(figi, "2024-03-02", "2024-03-22")]

    # Inspect what's in the bars table — originals untouched, gap filled.
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT ts, open, high, low, close, volume FROM bars WHERE figi = ? ORDER BY ts",
        (figi,),
    ).fetchall()
    con.close()

    dates = [r[0] for r in rows]
    assert dates == [
        "2024-03-01",
        "2024-03-04",
        "2024-03-05",
        "2024-03-06",
        "2024-03-07",
        "2024-03-08",
        "2024-03-22",
    ]
    # The pre-existing bar (2024-03-01) must remain exactly as seeded.
    march_first = rows[0]
    assert march_first[1:] == (100, 101, 99, 100, 1000)
    # The post-existing bar (2024-03-22) must remain untouched.
    march_22 = rows[-1]
    assert march_22[1:] == (100, 101, 99, 100, 1000)


@pytest.mark.asyncio
async def test_completeness_pass_marks_exhausted_when_no_bars_added(db):
    """A figi whose gap fetch returns zero bars gets completeness_exhausted.

    Setup: figi has the canonical gap from Mar 1 → Mar 22 (14 trading
    days). The fake broker returns an empty list — meaning the broker
    no longer has data for that range. The pass must:
      - return a CompletenessSummary with bars_added=0, gaps_found=1, exhausted=1
      - write `completeness_exhausted` to instrument_metadata.last_run_status
    """
    figi = "FIGI-EMPTY"
    _seed_instrument_and_bars(db, figi, ["2024-03-01", "2024-03-22"])
    _seed_figi_metadata(db, figi)

    client = _FakeClient(responses={(figi, "2024-03-02"): []})  # broker has nothing

    from algotrader_api.data_quality.completeness import run_completeness_pass

    summary = await run_completeness_pass(
        db,
        client,
        runner=None,  # completeness pass does not need a runner
        reports={figi: _make_report(figi, has_incomplete_history=True)},
    )

    # Summary fields.
    assert summary.figis_examined == 1
    assert summary.gaps_found == 1
    assert summary.bars_added == 0
    assert summary.exhausted == 1

    # Exhaustion marker written.
    con = sqlite3.connect(db)
    row = con.execute(
        "SELECT last_run_status FROM instrument_metadata WHERE figi = ?",
        (figi,),
    ).fetchone()
    con.close()
    assert row is not None and row[0] == "completeness_exhausted"


@pytest.mark.asyncio
async def test_completeness_pass_skips_exhausted_figis(db):
    """A figi already marked completeness_exhausted is skipped (no broker call).

    This is the "exhaustion guard" — once a figi has been marked
    exhausted, we don't re-query the broker for the same missing range.
    Setup: the figi has the canonical gap, but its metadata already
    says `completeness_exhausted`. The pass must NOT call get_candles
    and the summary must report zero gaps / zero added / zero exhausted.
    """
    figi = "FIGI-EXHAUSTED"
    _seed_instrument_and_bars(db, figi, ["2024-03-01", "2024-03-22"])
    _seed_figi_metadata(db, figi, last_run_status="completeness_exhausted")

    # If the pass DOES call get_candles, this will fail the test (populated list below).
    client = _FakeClient(
        responses={
            (figi, "2024-03-02"): [
                {"ts": "2024-03-05", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
            ],
        },
    )

    from algotrader_api.data_quality.completeness import run_completeness_pass

    summary = await run_completeness_pass(
        db,
        client,
        runner=None,
        reports={figi: _make_report(figi, has_incomplete_history=True)},
    )

    assert summary.figis_examined == 1  # it was examined (saw the INCOMPLETE_HISTORY issue)
    assert summary.gaps_found == 0
    assert summary.bars_added == 0
    assert summary.exhausted == 0
    assert client.calls == [], "broker must not be queried for an exhausted figi"


@pytest.mark.asyncio
async def test_completeness_pass_skips_figis_without_incomplete_history(db):
    """A figi without ``INCOMPLETE_HISTORY`` is never examined.

    The pass only acts on figis flagged with the
    ``INCOMPLETE_HISTORY`` sub-problem — healthier figis (or figis
    flagged only with other issues like ``MISSING_RECENT``) are
    silently skipped. The broker must not be queried and the summary
    must report zero examined.
    """
    figi = "FIGI-HEALTHY-ENOUGH"
    _seed_instrument_and_bars(db, figi, ["2024-03-01", "2024-03-22"])
    _seed_figi_metadata(db, figi)

    client = _FakeClient(
        responses={
            (figi, "2024-03-02"): [
                {"ts": "2024-03-05", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
            ],
        },
    )

    from algotrader_api.data_quality.completeness import run_completeness_pass

    summary = await run_completeness_pass(
        db,
        client,
        runner=None,
        reports={figi: _make_report(figi, has_incomplete_history=False)},
    )

    assert summary.figis_examined == 0
    assert summary.gaps_found == 0
    assert summary.bars_added == 0
    assert summary.exhausted == 0
    assert client.calls == [], "broker must not be queried for a non-INCOMPLETE_HISTORY figi"


@pytest.mark.asyncio
async def test_backfill_gaps_no_op_when_gaps_empty(db):
    """``backfill_gaps`` with an empty list returns 0 and never calls the broker."""
    from algotrader_api.data_quality.completeness import backfill_gaps

    client = _FakeClient(responses={})

    added = await backfill_gaps(client, "FIGI-ANY", [], db)

    assert added == 0
    assert client.calls == [], "no broker call expected when there are no gaps"