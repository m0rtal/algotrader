"""Tests for the Tinkoff dividends fetcher + writer."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from algotrader_api.scripts_import import import_dividends_tinkoff as fetcher
from algotrader_api.scripts_import.import_corporate_actions_common import (
    DividendRow,
    merge_into_dividends,
)


# --------------------------------------------------------------------------- #
# Schema helpers
# --------------------------------------------------------------------------- #


_DIVIDENDS_DDL = """
CREATE TABLE dividends (
    figi TEXT,
    ex_date TEXT,
    pay_date TEXT,
    record_date TEXT,
    declared_at TEXT,
    period_year INTEGER,
    period_no INTEGER,
    currency TEXT,
    amount_per_share REAL,
    fx_rate_used REAL,
    dividend_type TEXT,
    regularity TEXT,
    close_price REAL,
    yield_value REAL,
    yield_pct REAL,
    tax_withheld_pct REAL,
    cancelled_at TEXT,
    source TEXT,
    source_revision_ts TEXT,
    retrieved_at TEXT,
    revision_n INTEGER,
    note TEXT,
    PRIMARY KEY (figi, ex_date, period_year, period_no, revision_n)
);
"""


_INSTRUMENTS_DDL = """
CREATE TABLE instruments (
    figi TEXT PRIMARY KEY,
    class TEXT,
    ticker TEXT
);
"""


def _make_db(tmp_path) -> str:
    """Create a fresh DB with the dividends + instruments tables."""
    db = tmp_path / "state.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_INSTRUMENTS_DDL + _DIVIDENDS_DDL)
    conn.commit()
    conn.close()
    return str(db)


def _seed_figis(db_path: str, figis: list[str], klass: str = "share") -> None:
    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT INTO instruments (figi, class, ticker) VALUES (?, ?, ?)",
        [(f, klass, f.replace("BBG", "")) for f in figis],
    )
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- #
# FakeClient
# --------------------------------------------------------------------------- #


class _FakeClient:
    """Minimal fake matching the wrapper's shape."""

    def __init__(self, by_figi):
        self._by_figi = by_figi

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def get_dividends(self, figi, from_, to):
        return self._by_figi.get(figi, [])


def _div_dict(
    figi_suffix: str,
    *,
    ex_date: str = "2024-06-15",
    declared_at: str = "2024-03-15",
    amount: float = 10.0,
    yield_value=None,
    close_price=None,
    currency: str = "rub",
    div_type: str = "REGULAR",
):
    return {
        "figi": f"BBG{figi_suffix}",
        "dividend_id": f"d-{figi_suffix}",
        "ex_date": ex_date,
        "pay_date": "2024-07-01",
        "record_date": "2024-06-20",
        "declared_at": declared_at,
        "currency": currency,
        "amount_per_share": amount,
        "close_price": close_price,
        "yield_value": yield_value,
        "dividend_type": div_type,
        "regularity": "annual",
        "created_at": "2024-03-15T10:00:00Z",
    }


# --------------------------------------------------------------------------- #
# _to_row mapping
# --------------------------------------------------------------------------- #


def test_to_row_extracts_ex_date_and_amount():
    """Fixture dict → DividendRow with ex_date, amount_per_share, currency correct."""
    d = _div_dict("0001", ex_date="2024-06-15", amount=12.34, currency="usd")
    row = fetcher._to_row("BBG0001", d, retrieved_at="2024-09-14T12:00:00")
    assert row.figi == "BBG0001"
    assert row.ex_date == "2024-06-15"
    assert row.amount_per_share == 12.34
    assert row.currency == "usd"


def test_to_row_derives_period_year_from_declared_at():
    """declared_at="2024-03-15" → period_year=2024."""
    d = _div_dict("0001", declared_at="2024-03-15")
    row = fetcher._to_row("BBG0001", d, retrieved_at="2024-09-14T12:00:00")
    assert row.period_year == 2024


def test_to_row_derives_yield_pct_when_close_price_set():
    """yield_value=5, close_price=100 → yield_pct=0.05."""
    d = _div_dict("0001", yield_value=5.0, close_price=100.0)
    row = fetcher._to_row("BBG0001", d, retrieved_at="2024-09-14T12:00:00")
    assert row.yield_pct == pytest.approx(0.05)


# --------------------------------------------------------------------------- #
# merge_into_dividends writer
# --------------------------------------------------------------------------- #


def test_merge_into_dividends_is_idempotent(tmp_path):
    """Insert 1 row, insert same again → 0 new on second call."""
    db_path = _make_db(tmp_path)
    row = DividendRow(
        figi="BBG0001",
        ex_date="2024-06-15",
        period_year=2024,
        amount_per_share=10.0,
        retrieved_at="2024-09-14T12:00:00",
    )
    assert merge_into_dividends(db_path, [row]) == 1
    assert merge_into_dividends(db_path, [row]) == 0


def test_merge_inserts_retroactive_revision(tmp_path):
    """revision_n=1 row + revision_n=2 row → both written (different PK)."""
    db_path = _make_db(tmp_path)
    base = dict(
        figi="BBG0001",
        ex_date="2024-06-15",
        period_year=2024,
        amount_per_share=10.0,
        retrieved_at="2024-09-14T12:00:00",
    )
    r1 = DividendRow(revision_n=1, **base)
    r2 = DividendRow(revision_n=2, **base)
    assert merge_into_dividends(db_path, [r1, r2]) == 2

    conn = sqlite3.connect(db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM dividends WHERE figi='BBG0001'"
    ).fetchone()[0]
    assert n == 2


# --------------------------------------------------------------------------- #
# fetch_and_persist end-to-end
# --------------------------------------------------------------------------- #


def test_fetch_and_persist_writes_rows_for_every_figi(tmp_path):
    """FakeClient returns 2 figis' dividends → fetch_and_persist writes both."""
    db_path = _make_db(tmp_path)
    _seed_figis(db_path, ["BBG0001", "BBG0002"])
    client = _FakeClient({
        "BBG0001": [_div_dict("0001", amount=10.0)],
        "BBG0002": [_div_dict("0002", amount=20.0)],
    })

    written = fetcher.fetch_and_persist(
        db_path,
        client=client,
        from_year=date.today().year,
    )
    assert written == 2

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT figi, amount_per_share FROM dividends ORDER BY figi"
    ).fetchall()
    assert rows == [
        ("BBG0001", 10.0),
        ("BBG0002", 20.0),
    ]
