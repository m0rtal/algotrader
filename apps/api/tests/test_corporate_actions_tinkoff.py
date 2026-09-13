"""Tests for the Tinkoff dividends fetcher (Task 3).

The Tinkoff `client.instruments.get_dividends(figi, from_, to)` call is
synchronous, so we mock it with `unittest.mock.MagicMock` (NOT the async
`_FakeClient` used in completeness tests).

Each event payload has:
- `.last_buy_date.{year,month,day}` (Timestamp-like)
- `.dividend_net.{units,nano}` (Quotation-like)
- `.currency` (str)
- `.payment_date.{year,month,day}` (Timestamp-like)
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations


# --- helpers ---------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """Migrated DB on a fresh temp path."""
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    yield p


def _seed_figis(db_path, figis):
    import sqlite3

    con = sqlite3.connect(db_path)
    # Use a small per-test ticker derivation; uniqueness guaranteed
    # by iterating the figis with their index.
    rows = [
        (figi, f"T{idx}", "share", figi, "rub", 10)
        for idx, figi in enumerate(figis)
    ]
    con.executemany(
        "INSERT INTO instruments(figi, ticker, class, name, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.commit()
    con.close()


class _Timestamp:
    def __init__(self, yyyymmdd: str):
        from datetime import datetime as _dt

        d = _dt.strptime(yyyymmdd, "%Y%m%d").date()
        self.year = d.year
        self.month = d.month
        self.day = d.day


class _Quotation:
    def __init__(self, units: int, nano: int = 0):
        self.units = units
        self.nano = nano


def _make_event(yyyymmdd: str, dividend_net_units: int, currency: str = "rub"):
    """Build a Tinkoff Dividend protobuf-shaped object."""

    class _Event:
        def __init__(self):
            self.last_buy_date = _Timestamp(yyyymmdd)
            self.dividend_net = _Quotation(dividend_net_units)
            self.currency = currency
            self.payment_date = _Timestamp(yyyymmdd)

    return _Event()


# --- tests -----------------------------------------------------------------


def test_fetch_dividends_returns_rows_per_event():
    # Tinkoff Quotations: cash_amount = units + nano/1e9.
    # 387 RUB / share = units=387, nano=0.
    client = MagicMock()
    client.instruments.get_dividends.return_value = MagicMock(
        events=[
            _make_event("20240708", 387),  # 387 RUB / share
            _make_event("20250615", 250),  # 250 RUB / share
        ]
    )
    from algotrader_api.scripts_import.import_corporate_actions_tinkoff import (
        fetch_dividends_for_figi,
    )

    rows = fetch_dividends_for_figi(
        client,
        figi="BBG004730N88",
        from_=date(2024, 1, 1),
        to=date(2025, 12, 31),
    )
    assert len(rows) == 2
    assert rows[0].figi == "BBG004730N88"
    assert rows[0].action_type == "dividend"
    assert rows[0].cash_amount == 387.0
    assert rows[0].note.startswith("tinkoff:")


def test_fetch_dividends_returns_empty_when_no_events():
    client = MagicMock()
    client.instruments.get_dividends.return_value = MagicMock(events=[])
    from algotrader_api.scripts_import.import_corporate_actions_tinkoff import (
        fetch_dividends_for_figi,
    )

    rows = fetch_dividends_for_figi(
        client, "BBG004730N88", date(2024, 1, 1), date(2024, 12, 31)
    )
    assert rows == []


def test_fetch_dividends_filters_events_outside_window():
    client = MagicMock()
    client.instruments.get_dividends.return_value = MagicMock(
        events=[
            _make_event("20230708", 100),  # outside [2024,2025]
            _make_event("20240708", 387),
        ]
    )
    from algotrader_api.scripts_import.import_corporate_actions_tinkoff import (
        fetch_dividends_for_figi,
    )

    rows = fetch_dividends_for_figi(
        client,
        "BBG004730N88",
        date(2024, 1, 1),
        date(2025, 12, 31),
    )
    assert len(rows) == 1
    assert rows[0].ex_date == date(2024, 7, 8)


def test_fetch_dividends_converts_nano_fraction():
    """1.25 RUB = units=1, nano=250_000_000 → 1.25"""
    ev = _make_event("20240708", 0)  # units=0; override quotient
    ev.dividend_net = _Quotation(units=1, nano=250_000_000)
    client = MagicMock()
    client.instruments.get_dividends.return_value = MagicMock(events=[ev])
    from algotrader_api.scripts_import.import_corporate_actions_tinkoff import (
        fetch_dividends_for_figi,
    )

    rows = fetch_dividends_for_figi(
        client, "BBG004730N88", date(2024, 1, 1), date(2024, 12, 31)
    )
    assert rows[0].cash_amount == 1.25


def test_iterate_all_instruments_calls_get_dividends_per_figi(db):
    """High-level: import_corporate_actions_tinkoff iterates every
    tradeable figi and calls get_dividends for each."""
    _seed_figis(db, ["BBG004730N88", "BBG004730RP0"])
    fake_client = MagicMock()
    fake_client.instruments.get_dividends.return_value = MagicMock(events=[])
    from algotrader_api.scripts_import.import_corporate_actions_tinkoff import (
        import_corporate_actions_tinkoff,
    )

    n = import_corporate_actions_tinkoff(
        db,
        client=fake_client,
        from_=date(2024, 1, 1),
        to=date(2024, 12, 31),
    )
    assert n == 0
    assert fake_client.instruments.get_dividends.call_count == 2
    # Each figi was queried exactly once
    queried_figis = [
        call.kwargs["figi"]
        for call in fake_client.instruments.get_dividends.call_args_list
    ]
    assert set(queried_figis) == {"BBG004730N88", "BBG004730RP0"}


def test_iterate_all_instruments_writes_rows_to_db(db):
    """High-level: rows returned from per-figi fetcher get merged into
    corporate_actions via the common writer."""
    _seed_figis(db, ["BBG004730N88"])

    # Build a client whose get_dividends returns one event for the figi.
    fake_client = MagicMock()
    fake_client.instruments.get_dividends.return_value = MagicMock(
        events=[_make_event("20240708", 387)]
    )
    from algotrader_api.scripts_import.import_corporate_actions_tinkoff import (
        import_corporate_actions_tinkoff,
    )

    n = import_corporate_actions_tinkoff(
        db,
        client=fake_client,
        from_=date(2024, 1, 1),
        to=date(2024, 12, 31),
    )
    assert n == 1

    import sqlite3

    con = sqlite3.connect(db)
    row = con.execute(
        "SELECT action_type, cash_amount, note FROM corporate_actions "
        "WHERE figi=?",
        ("BBG004730N88",),
    ).fetchone()
    assert row[0] == "dividend"
    assert row[1] == 387.0
    assert row[2].startswith("tinkoff:")