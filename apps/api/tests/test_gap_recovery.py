"""Tests for the gap-recovery module."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import asyncio

import pytest

from algotrader_api.data_quality.gap_recovery import (
    BarGap,
    find_gaps,
    recover_gaps,
)
from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import get_connection, run_migrations


@pytest.fixture
def db(tmp_path):
    """A fresh state.db with the schema applied."""
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    return p


def _seed_instrument(cur, figi: str, ticker: str | None = None) -> None:
    if ticker is None:
        ticker = "T" + figi[-4:]
    cur.execute(
        "INSERT INTO instruments(ticker, figi, class, name, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ticker, figi, "share", figi, "RUB", 1),
    )


def _seed_bars(cur, figi: str, dates: list[str]) -> None:
    cur.executemany(
        "INSERT INTO bars(figi, ts, open, high, low, close, volume) "
        "VALUES (?,?,?,?,?,?,?)",
        [(figi, d, 100, 101, 99, 100, 1000) for d in dates],
    )


def _seed_restricted(cur, dates: list[str]) -> None:
    cur.executemany(
        "INSERT INTO restricted_periods(date, reason) VALUES (?, ?)",
        [(d, "test restricted") for d in dates],
    )


def test_find_gaps_returns_no_gaps_for_complete_history(db):
    """14 weekday span, every weekday covered -> no gaps."""
    figi = "FIGI-FULL"
    cur = get_connection(db).cursor()
    _seed_instrument(cur, figi)
    # Mon..Fri the first week, Mon..Fri the second week = 10 weekdays.
    _seed_bars(cur, figi, [
        "2024-03-04", "2024-03-05", "2024-03-06", "2024-03-07", "2024-03-08",
        "2024-03-11", "2024-03-12", "2024-03-13", "2024-03-14", "2024-03-15",
    ])
    cur.connection.commit()

    assert find_gaps(db) == []


def test_find_gaps_detects_single_missing_day(db):
    """Mon-Fri complete, then a 1-day gap on Tue, then Wed-Fri complete."""
    figi = "FIGI-ONE"
    cur = get_connection(db).cursor()
    _seed_instrument(cur, figi)
    # Week 1: Mon..Fri. Week 2: only Mon, Wed, Thu, Fri -> missing Tue.
    _seed_bars(cur, figi, [
        "2024-03-04", "2024-03-05", "2024-03-06", "2024-03-07", "2024-03-08",
        "2024-03-11",                       "2024-03-13", "2024-03-14", "2024-03-15",
    ])
    cur.connection.commit()

    gaps = find_gaps(db)
    assert len(gaps) == 1
    assert gaps[0] == BarGap(figi=figi, from_=date(2024, 3, 12), to_=date(2024, 3, 12))


def test_find_gaps_collapses_consecutive_missing_days(db):
    """Mon and Fri present, Tue-Thu collapsed into one BarGap."""
    figi = "FIGI-MULTI"
    cur = get_connection(db).cursor()
    _seed_instrument(cur, figi)
    _seed_bars(cur, figi, [
        "2024-03-04",  # Mon
        # missing Tue, Wed, Thu
        "2024-03-08",  # Fri
    ])
    cur.connection.commit()

    gaps = find_gaps(db)
    assert len(gaps) == 1
    assert gaps[0] == BarGap(figi=figi, from_=date(2024, 3, 5), to_=date(2024, 3, 7))


def test_find_gaps_skips_restricted_period_dates(db):
    """A restricted date inside a missing range must not appear in any BarGap.

    Bars: Mon of week 1, Wed (restricted middle), Mon of week 2.
    Expected trading days in the [Mon-w1, Mon-w2] span = 9 weekdays
    (Mon-w1 .. Fri-w1 = 5, Mon-w2 = 1, Mon-w2 is the last; total 6 weekdays
    in range, minus 1 restricted Wed = 5 expected). Wait — corrected below.
    The point of the test is that the restricted Wed date must NOT appear
    as from_ or to_ of any emitted BarGap.
    """
    figi = "FIGI-RESTR"
    cur = get_connection(db).cursor()
    _seed_instrument(cur, figi)
    # Wed of week 1 (2024-03-06) is restricted.
    _seed_restricted(cur, ["2024-03-06"])
    # Seed only Mon-w1 and Mon-w2; Wed (restricted) has no bar, but it must
    # not appear in any emitted BarGap.
    _seed_bars(cur, figi, [
        "2024-03-04",  # Mon week 1
        # Tue-w1, Wed-w1 (restricted), Thu-w1, Fri-w1 all missing
        "2024-03-11",  # Mon week 2
    ])
    cur.connection.commit()

    gaps = find_gaps(db)
    # Wed (2024-03-06) is restricted -> not in expected set -> no gap ends
    # on Wed. The missing range is split into Tue (isolated above Wed) and
    # Thu-Fri (below Wed) and Tue-w2 .. Fri-w2 ... actually the second-week
    # Mon is the LAST bar, so expected continues Mon-w2 = present. So
    # missing days in [Mon-w1, Mon-w2] minus restricted Wed = Tue-w1 (1 day)
    # and Thu-w1..Fri-w1 (2 days). Two gaps, neither touching Wed.
    assert all(g.figi == figi for g in gaps)
    restricted = date(2024, 3, 6)
    for g in gaps:
        assert g.from_ != restricted
        assert g.to_ != restricted
    # The Tue-w1 gap exists, and the Thu..Fri-w1 gap exists.
    expected_gaps = {
        BarGap(figi=figi, from_=date(2024, 3, 5), to_=date(2024, 3, 5)),
        BarGap(figi=figi, from_=date(2024, 3, 7), to_=date(2024, 3, 8)),
    }
    assert set(gaps) == expected_gaps


def test_find_gaps_skips_figis_with_zero_bars(db):
    """A figi with no bars at all is skipped (Task 7 owns its backfill)."""
    cur = get_connection(db).cursor()
    # Instrument exists in the registry but has no rows in bars.
    _seed_instrument(cur, "FIGI-EMPTY", ticker="EMPT")
    # Add another figi WITH bars so find_gaps has something to enumerate.
    _seed_instrument(cur, "FIGI-OK", ticker="OKOK")
    _seed_bars(cur, "FIGI-OK", ["2024-03-04", "2024-03-05", "2024-03-06"])
    cur.connection.commit()

    gaps = find_gaps(db)
    # FIGI-OK has all 3 weekdays covered -> no gap. FIGI-EMPTY is skipped.
    assert all(g.figi != "FIGI-EMPTY" for g in gaps)
    assert gaps == []


@dataclass
class FakeRunner:
    """Duck-typed runner for recover_gaps — records every _backfill_one call."""
    calls: list[tuple[str, str, date, date]] = field(default_factory=list)
    return_value: int = 7  # bars_added reported on each call

    async def _backfill_one(self, *, figi: str, ticker: str | None, from_: date, to: date) -> int:
        self.calls.append((figi, ticker, from_, to))
        return self.return_value


def test_recover_gaps_calls_runner_for_each_gap(db):
    """recover_gaps calls _backfill_one once per gap with correct args; sums bars_added."""
    figi_a = "FIGI-A"
    figi_b = "FIGI-B"
    cur = get_connection(db).cursor()
    _seed_instrument(cur, figi_a, ticker="AAA")
    _seed_instrument(cur, figi_b, ticker="BBB")
    # Seed one bar each so the figis are not "empty" (find_gaps not invoked here
    # — we pass gaps directly to recover_gaps).
    _seed_bars(cur, figi_a, ["2024-03-04"])
    _seed_bars(cur, figi_b, ["2024-03-04"])
    cur.connection.commit()

    runner = FakeRunner(return_value=3)
    gaps = [
        BarGap(figi=figi_a, from_=date(2024, 3, 5), to_=date(2024, 3, 7)),
        BarGap(figi=figi_b, from_=date(2024, 3, 12), to_=date(2024, 3, 12)),
    ]

    added = asyncio.run(recover_gaps(db, runner, gaps))

    # One _backfill_one call per gap, with ticker resolved from instruments.
    assert runner.calls == [
        (figi_a, "AAA", date(2024, 3, 5), date(2024, 3, 7)),
        (figi_b, "BBB", date(2024, 3, 12), date(2024, 3, 12)),
    ]
    # Each gap reports 3 bars added -> total per figi is 3.
    assert added == {figi_a: 3, figi_b: 3}


def test_recover_gaps_empty_list_returns_empty_dict(db):
    """recover_gaps([]) -> {} and no runner calls."""
    runner = FakeRunner()
    assert asyncio.run(recover_gaps(db, runner, [])) == {}
    assert runner.calls == []