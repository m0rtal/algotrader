"""Identity guards for every non-same-day MOEX bar-write path in backfill.

PR #176 added a per-row ``_secid``/``_boardid`` check + an ISIN cross-check
to ``catchup_same_day.py`` only. The same-day script's identity filter
doesn't reach the three bulk paths:

  * ``backfill_from_moex._process_moex_year`` — historical multi-year walk
  * ``backfill_moex_recent_tail._process_one`` — recent-tail pass
  * ``BackfillRunner._backfill_one_moex``     — full-history walker

Without the filter, MOEX's ``/iss/history/.../securities/{ticker}.json``
endpoint serves whatever matches the ticker — for the colliding T /
DIOD / ROST cases in production, that's the RU instrument only, but the
endpoint can also leak rows from a different board or a stale SECID when
the upstream board lookup is wrong. The downstream
``replace_bars_for_figi`` writes whatever it's given, keyed on figi.

The fix is a data-driven filter (``_filter_moex_bars_by_identity``)
applied at the three call sites; it drops any bar whose raw
``_secid``/``_boardid`` doesn't match the ticker/board we asked MOEX
for. No new HTTP round-trip per figi.

These tests pin the post-fix behavior so the guard can't quietly
regress on any of the three paths.

Real fixture data: ``RU000A107UL4`` is a Russian share ISIN; the
``US00206R1023`` mirrors it on the T ticker. The US figi must NEVER
receive the RU bar through any of the three paths.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import responses

from algotrader_api.ingestion.backfill import (
    BackfillRunner,
    _filter_moex_bars_by_identity,
)


RU_ISIN = "RU000A107UL4"
US_ISIN = "US00206R1023"
RU_FIGI = "BBG00RU000A1"
US_FIGI = "BBG00US00002"


# ─── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path):
    """Two-instrument DB: RU + US share the ticker T."""
    db_path = str(tmp_path / "test.db")
    from algotrader_api.db import sqlite as sqlitedb

    migrations_dir = str(
        Path(__file__).resolve().parent.parent
        / "src/algotrader_api/db/migrations"
    )
    sqlitedb.run_migrations(db_path, migrations_dir)
    sqlitedb.close_all()
    con = sqlite3.connect(db_path)
    con.executescript("""
        INSERT INTO instruments
            (ticker, figi, class, name, currency, lot_size, isin)
        VALUES
            ('T', 'BBG00RU000A1', 'share', 'T RU',  'rub', 1, 'RU000A107UL4'),
            ('T', 'BBG00US00002', 'share', 'T US',  'rub', 1, 'US00206R1023');
    """)
    con.commit()
    con.close()
    return db_path


async def _noop_sink(_ev):
    return None


def _bar_count(db_path: str, figi: str) -> int:
    con = sqlite3.connect(db_path)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM bars WHERE figi = ?", (figi,)
        ).fetchone()[0]
    finally:
        con.close()


# ─── unit tests for the filter helper ─────────────────────────────────


def test_filter_drops_wrong_secid():
    """A row with the wrong _secid is dropped."""
    bars = [
        {"ts": "2026-09-21", "_secid": "T",   "_boardid": "TQBR"},
        {"ts": "2026-09-22", "_secid": "X",   "_boardid": "TQBR"},
    ]
    out = _filter_moex_bars_by_identity(bars, ticker="T", board="TQBR")
    assert [b["ts"] for b in out] == ["2026-09-21"]


def test_filter_drops_wrong_boardid():
    """A row with the wrong _boardid (cross-listed mirror) is dropped."""
    bars = [
        {"ts": "2026-09-21", "_secid": "T", "_boardid": "TQBR"},
        {"ts": "2026-09-22", "_secid": "T", "_boardid": "SMAL"},
    ]
    out = _filter_moex_bars_by_identity(bars, ticker="T", board="TQBR")
    assert [b["ts"] for b in out] == ["2026-09-21"]


def test_filter_drops_missing_identity_columns():
    """A row whose _secid/_boardid are missing must be dropped.

    A missing identity pair is treated as "could not verify" and is
    refused — the alternative (silently accepting it) would let any
    upstream that omits those columns leak rows through.
    """
    bars = [
        {"ts": "2026-09-21"},  # no identity columns
        {"ts": "2026-09-22", "_secid": "T", "_boardid": "TQBR"},
    ]
    out = _filter_moex_bars_by_identity(bars, ticker="T", board="TQBR")
    assert [b["ts"] for b in out] == ["2026-09-22"]


def test_filter_empty_list_passthrough():
    """An empty bar list returns empty (no work to do, no NPE)."""
    assert _filter_moex_bars_by_identity([], ticker="T", board="TQBR") == []


# ─── integration: backfill_from_moex historical walk ──────────────────


@responses.activate
async def test_historical_walk_skips_wrong_identity_for_us_mirror(
    fresh_db, monkeypatch,
):
    """Two figis share ticker T; the historical multi-year walker must
    only attach bars to the RU one.

    Mock MOEX so that both the ``/iss/securities/T.json`` (board lookup)
    and the ``/iss/history/.../T.json`` (history rows) return only
    the RU instrument. The US figi must receive zero rows: the
    ``_filter_moex_bars_by_identity`` inside ``_process_moex_year``
    drops the rows before ``figi`` is set on them.

    Without the filter, the historical walker used to issue one
    INSERT OR IGNORE per US figi as well — the wrong-instrument
    cross-pollution documented in the audit.
    """
    # MOEX reports TQBR / 2014..2026 for ticker T.
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/T.json",
        json={"boards": {"data": [["T", "TQBR", "x", 0, 0, "shares", 0, 1, 1, 0,
                                    "2014-01-01", "2026-09-14",
                                    "2014-01-01", "2026-09-15",
                                    1, "SUR", "%"]]},
              "description": {"data": [["ISIN", "ISIN", "RU000A107UL4"]]}},
    )

    # History endpoint returns one bar in 2026 with the RU identity.
    # Pre-fix this bar would be attached to BOTH figis (one per
    # ``_process_moex_year`` call scheduled for each figi's
    # year-batch), because the historical walker fetches once per
    # figi-year and stamps `b["figi"] = inst["figi"]` on every row
    # returned by `_fetch_year_moex`.
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities/T.json",
        json={"history": {
            "columns": ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "SECID", "BOARDID"],
            "data": [["2026-09-10", 100.0, 101.0, 99.0, 100.5,
                      1000, "T", "TQBR"]],
        }, "history.cursor": {"data": [[0, 1, 500]]}},
    )

    # Pin _last_trading_day so the walker uses yesterday = 2026-09-14.
    import algotrader_api.ingestion.backfill as bf_mod
    monkeypatch.setattr(
        bf_mod, "_last_trading_day",
        lambda today, db_path, **_kw: date(2026, 9, 14),
    )

    runner = BackfillRunner(
        client=MagicMock(), db_path=fresh_db, event_sink=_noop_sink,
    )
    written = await runner.backfill_from_moex(today=date(2026, 9, 15))

    # Both figis are processed; the RU one gets the bar, the US one
    # gets nothing. Pre-fix the US figi would have inherited the same
    # INSERT OR IGNORE row keyed on figi=US_FIGI.
    assert _bar_count(fresh_db, RU_FIGI) == 1, (
        f"RU figi must receive the matched-identity bar; got "
        f"{_bar_count(fresh_db, RU_FIGI)} rows"
    )
    assert _bar_count(fresh_db, US_FIGI) == 0, (
        f"US mirror figi must NOT receive the RU bar (the audit's "
        f"core collision); got {_bar_count(fresh_db, US_FIGI)} rows"
    )


# ─── integration: backfill_moex_recent_tail ───────────────────────────


@responses.activate
async def test_recent_tail_skips_wrong_identity_for_us_mirror(
    fresh_db, monkeypatch,
):
    """Same two-fixture watch but on the recent-tail pass.

    The recent-tail pass routes by ticker too. The data-driven
    ``_filter_moex_bars_by_identity`` is applied to the
    ``_fetch_moex_range`` result inside ``_process_one``; the US figi
    gets nothing.
    """
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/T.json",
        json={"boards": {"data": [["T", "TQBR", "x", 0, 0, "shares", 0, 1, 1, 0,
                                    "2014-01-01", "2026-09-14",
                                    "2014-01-01", "2026-09-15",
                                    1, "SUR", "%"]]},
              "description": {"data": [["ISIN", "ISIN", "RU000A107UL4"]]}},
    )
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities/T.json",
        json={"history": {
            "columns": ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "SECID", "BOARDID"],
            "data": [["2026-09-12", 200.0, 201.0, 199.0, 200.5,
                      2000, "T", "TQBR"]],
        }, "history.cursor": {"data": [[0, 1, 500]]}},
    )

    import algotrader_api.ingestion.backfill as bf_mod
    monkeypatch.setattr(
        bf_mod, "_last_trading_day",
        lambda today, db_path, **_kw: date(2026, 9, 14),
    )

    written = await runner_recent_tail(fresh_db)

    assert _bar_count(fresh_db, RU_FIGI) == 1
    assert _bar_count(fresh_db, US_FIGI) == 0, (
        f"US mirror figi must NOT receive the RU bar via recent-tail "
        f"either; got {_bar_count(fresh_db, US_FIGI)} rows"
    )


async def runner_recent_tail(fresh_db):
    runner = BackfillRunner(
        client=MagicMock(), db_path=fresh_db, event_sink=_noop_sink,
    )
    return await runner.backfill_moex_recent_tail(
        days=5, today=date(2026, 9, 15),
    )


# ─── integration: _backfill_one_moex (full-history walker) ────────────


@responses.activate
async def test_full_history_walker_skips_wrong_identity_for_us_mirror(
    fresh_db, monkeypatch,
):
    """Same two-fixture watch but on the explicit full-history walker.

    ``_backfill_one_moex`` is the lowest-level MOEX-by-symbol entry
    point — invoked by ``backfill.full_history``/``backfill.bulk``
    routes and by direct CLI users. Pre-fix it ran with
    hard-coded ``("shares", "TQBR", ticker, year)`` and stamped
    ``figi`` on every row, no matter what the upstream returned.
    """
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities/T.json",
        json={"history": {
            "columns": ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "SECID", "BOARDID"],
            "data": [["2026-09-13", 300.0, 301.0, 299.0, 300.5,
                      3000, "T", "TQBR"]],
        }, "history.cursor": {"data": [[0, 1, 500]]}},
    )

    import algotrader_api.ingestion.backfill as bf_mod
    from algotrader_api.ingestion import no_trade_evidence
    monkeypatch.setattr(
        bf_mod, "_last_trading_day",
        lambda today, db_path, **_kw: date(2026, 9, 14),
    )
    # ``_backfill_one_moex`` looks up the ISIN MOEX considers
    # authoritative for the ticker via ``fetch_issuer_identity``
    # (separate from ``_get_meta_moex`` because this entry point
    # takes figi+ticker directly, not an ``inst`` dict with a
    # prefetched meta). Stub it: RU ISIN for ticker "T".
    monkeypatch.setattr(
        no_trade_evidence, "fetch_issuer_identity",
        lambda ticker: {"board": "TQBR", "isin": "RU000A107UL4"},
    )

    runner = BackfillRunner(
        client=MagicMock(), db_path=fresh_db, event_sink=_noop_sink,
    )
    added_ru = await runner._backfill_one_moex(
        figi=RU_FIGI, ticker="T",
        from_=date(2026, 1, 1), to=date(2026, 9, 14),
    )
    added_us = await runner._backfill_one_moex(
        figi=US_FIGI, ticker="T",
        from_=date(2026, 1, 1), to=date(2026, 9, 14),
    )

    assert added_ru == 1, f"RU figi expected 1 row; got {added_ru}"
    assert added_us == 0, (
        f"US mirror figi must NOT receive the RU bar via "
        f"_backfill_one_moex; got {added_us}"
    )


# ─── mixed: legitimate + wrong-identity rows in same response ──────


@responses.activate
async def test_recent_tail_drops_wrong_secid_rows_keeps_correct(
    fresh_db, monkeypatch,
):
    """If MOEX mixes legitimate RU rows with rows from a different
    SECID in the same response, only the legitimate rows must survive.
    """
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/T.json",
        json={"boards": {"data": [["T", "TQBR", "x", 0, 0, "shares", 0, 1, 1, 0,
                                    "2014-01-01", "2026-09-14",
                                    "2014-01-01", "2026-09-15",
                                    1, "SUR", "%"]]},
              "description": {"data": [["ISIN", "ISIN", "RU000A107UL4"]]}},
    )
    # Mixed response: 2 rows tagged "T" + 1 row tagged "OTHER".
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities/T.json",
        json={"history": {
            "columns": ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "SECID", "BOARDID"],
            "data": [
                ["2026-09-12", 100.0, 101.0, 99.0, 100.5,
                1000, "T", "TQBR"],
                ["2026-09-13", 101.0, 102.0, 100.0, 101.5,
                1100, "OTHER", "TQBR"],
                ["2026-09-14", 102.0, 103.0, 101.0, 102.5,
                1200, "T", "TQBR"],
            ],
        }, "history.cursor": {"data": [[0, 3, 500]]}},
    )

    import algotrader_api.ingestion.backfill as bf_mod
    monkeypatch.setattr(
        bf_mod, "_last_trading_day",
        lambda today, db_path, **_kw: date(2026, 9, 14),
    )

    runner = BackfillRunner(
        client=MagicMock(), db_path=fresh_db, event_sink=_noop_sink,
    )
    await runner.backfill_moex_recent_tail(
        days=5, today=date(2026, 9, 15),
    )

    # RU figi must have 2 rows (the OTHER row dropped before
    # INSERT OR IGNORE); US figi must have 0.
    assert _bar_count(fresh_db, RU_FIGI) == 2, (
        f"RU figi expected 2 matched-identity rows; got "
        f"{_bar_count(fresh_db, RU_FIGI)}"
    )
    assert _bar_count(fresh_db, US_FIGI) == 0, (
        f"US figi must receive zero rows; got "
        f"{_bar_count(fresh_db, US_FIGI)}"
    )