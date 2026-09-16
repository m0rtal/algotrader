"""Coverage tests for issue #64: bump backfill.py coverage above 95% gate.

Target: `apps/api/src/algotrader_api/ingestion/backfill.py`. The MOEX-driven
backfill path (`BackfillRunner.backfill_from_moex()`) added in PR #61/#62
introduced several untested branches that drag the aggregate below the 95%
gate. This file exercises them so the aggregate can pass without raising
the global gate.

Each test follows the same conventions as `test_backfill_moex_runner.py`:
- a fresh sqlite DB seeded with instruments via the migrations
- `responses` to mock iss.moex.com HTTP calls
- a no-op event sink so we don't need the SSE broadcaster
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import responses

from algotrader_api.ingestion.backfill import BackfillRunner


# ---------- shared fixtures ----------


@pytest.fixture
def fresh_db(tmp_path):
    """A clean sqlite DB with three instruments: SBER, GAZP, OFZ."""
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
        INSERT INTO instruments (ticker, figi, class, name, currency, lot_size)
        VALUES ('SBER', 'BBG004730N88', 'share', 'Sber', 'rub', 10);
        INSERT INTO instruments (ticker, figi, class, name, currency, lot_size)
        VALUES ('GAZP', 'BBG004730RP0', 'share', 'Gazprom', 'rub', 10);
        INSERT INTO instruments (ticker, figi, class, name, currency, lot_size)
        VALUES ('SU46020RMFS2', 'FIGI-BOND', 'bond', 'OFZ', 'rub', 1);
    """)
    con.commit()
    con.close()
    return db_path


async def _noop_sink(_ev):
    return None


# ---------- pagination edge: short page = last (no cursor) ----------


@responses.activate
async def test_pagination_short_page_no_cursor_is_last(fresh_db):
    """When the server returns NO `history.cursor` block at all, the
    fetcher must use a `short page = last` heuristic. This covers the
    `else` branch at lines 495-498.

    Mock returns a single page with fewer than `page_size` rows and no
    cursor — must terminate after that one page per year.
    """
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/SBER.json",
        # listed_from = listed_till = 2024 only → fetcher visits one year
        json={"boards": {"data": [["SBER", "TQBR", "x", 0, 0, "shares", 0, 1, 1, 0,
                                     "2024-01-01", "2024-12-31", "2024-01-01", "2024-12-31",
                                     1, "SUR", "%"]]}},
    )

    def short_page_cb(request):
        # 50 rows (well below the server's default page_size of 500),
        # no history.cursor block at all.
        rows = []
        base = date(2024, 1, 1)
        for i in range(50):
            rows.append([(base + timedelta(days=i)).isoformat(), 100.0 + i, 101.0 + i,
                         99.0 + i, 100.5 + i, 1000 + i])
        # Note: no "history.cursor" key in the response — the fetcher
        # must fall back to "short page = last".
        return (
            200,
            {},
            json.dumps({"history": {"columns": ["TRADEDATE", "OPEN", "HIGH", "LOW",
                                                  "CLOSE", "VOLUME"], "data": rows}}),
        )

    responses.add_callback(
        responses.GET,
        "https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities/SBER.json",
        callback=short_page_cb,
    )

    # GAZP and OFZ get empty boards → Tinkoff fallback path
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/GAZP.json",
        json={"boards": {"data": []}},
    )
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/SU46020RMFS2.json",
        json={"boards": {"data": []}},
    )

    client = MagicMock()
    client.get_candles = AsyncMock(return_value=[])

    runner = BackfillRunner(client=client, db_path=fresh_db, event_sink=_noop_sink)
    written = await runner.backfill_from_moex(today=date(2026, 9, 15))

    assert written == 50, f"expected 50 bars (one short SBER page), got {written}"


# ---------- pagination edge: malformed cursor (offset is None) ----------
# SKIPPED: this exposes a real production bug — the `except (TypeError,
# ValueError): pass` block at lines 493-494 does not break the loop, so a
# malformed cursor causes an infinite HTTP fetch loop. Fixing that requires
# touching `backfill.py` and exceeds the safety interlock's single-file
# scope. Test stays off until the production bug is fixed separately.


# ---------- instrument without ticker ----------
# SKIPPED: `instruments.ticker` is PRIMARY KEY (NOT NULL by definition in
# migration 002). The "no ticker" branch at line 535-536 is unreachable in
# current schema, so a unit test against it would require either a schema
# change (out of scope for the safety interlock) or a fake row that the
# DB rejects. Path remains covered by the existing
# `test_backfill_from_moex_writes_bars_with_dynamic_dates` test which
# exercises the normal ticker path.


# ---------- date parse error in Tinkoff fallback ----------
# SKIPPED: `backfill_from_moex` reads `inst.get("listed_from")` from the
# SQL `SELECT ticker, figi, class FROM instruments` result (line 928),
# which does NOT include `listed_from`. The `inst.get("listed_from") or
# "2014-01-01"` branch at line 542 always hits the default fallback,
# making the date-parse error path unreachable without a code change to
# the SELECT. Same out-of-scope reason as above.


# ---------- date parse error in MOEX path (lines 558-561) ----------


@responses.activate
async def test_malformed_listed_dates_in_moex_path_is_skipped(fresh_db):
    """If MOEX returns a board row with malformed listed_from/listed_till
    dates, the instrument must be skipped silently (lines 558-561).
    """
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/SBER.json",
        json={"boards": {"data": [["SBER", "TQBR", "x", 0, 0, "shares", 0, 1, 1, 0,
                                     "not-a-date", "also-bad", "2014-01-01", "2026-09-15",
                                     1, "SUR", "%"]]}},
    )
    # Other tickers → empty boards → Tinkoff fallback (returns empty)
    for ticker in ["GAZP", "SU46020RMFS2"]:
        responses.add(
            responses.GET,
            f"https://iss.moex.com/iss/securities/{ticker}.json",
            json={"boards": {"data": []}},
        )

    client = MagicMock()
    client.get_candles = AsyncMock(return_value=[])

    runner = BackfillRunner(client=client, db_path=fresh_db, event_sink=_noop_sink)
    written = await runner.backfill_from_moex(today=date(2026, 9, 15))

    # SBER skipped due to date parse error; GAZP/OFZ → Tinkoff returns empty
    assert written == 0


# ---------- delta_only branch (line 574) ----------


@responses.activate
async def test_delta_only_skips_figi_with_existing_bars_covering_listed_window(fresh_db):
    """When delta_only=True and the figi already has bars covering the
    MOEX listed_from window, the instrument must be skipped (line 574)."""
    # Pre-populate SBER with bars covering its listed window
    con = sqlite3.connect(fresh_db)
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume, source) "
        "VALUES ('BBG004730N88', '2013-01-01', 100, 101, 99, 100.5, 1000, 'moex')"
    )
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume, source) "
        "VALUES ('BBG004730N88', '2014-01-01', 110, 111, 109, 110.5, 1100, 'moex')"
    )
    con.commit()
    con.close()

    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/SBER.json",
        json={"boards": {"data": [["SBER", "TQBR", "x", 0, 0, "shares", 0, 1, 1, 0,
                                     "2014-01-01", "2026-09-14", "2014-01-01", "2026-09-15",
                                     1, "SUR", "%"]]}},
    )
    for ticker in ["GAZP", "SU46020RMFS2"]:
        responses.add(
            responses.GET,
            f"https://iss.moex.com/iss/securities/{ticker}.json",
            json={"boards": {"data": []}},
        )

    client = MagicMock()
    client.get_candles = AsyncMock(return_value=[])

    runner = BackfillRunner(client=client, db_path=fresh_db, event_sink=_noop_sink)
    written = await runner.backfill_from_moex(today=date(2026, 9, 15), delta_only=True)

    # SBER skipped (already has bars at 2014-01-01 ≤ listed_from 2014-01-01)
    # GAZP/OFZ → Tinkoff returns empty
    assert written == 0


# ---------- from_d > to_d (line 578-579) ----------


@responses.activate
async def test_listed_from_after_today_is_skipped(fresh_db):
    """If listed_from > yesterday, the instrument must be skipped (line 578-579)."""
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/SBER.json",
        # listed_from is in the future — listed_from > to_d → skip
        json={"boards": {"data": [["SBER", "TQBR", "x", 0, 0, "shares", 0, 1, 1, 0,
                                     "2030-01-01", "2035-01-01", "2030-01-01", "2035-01-02",
                                     1, "SUR", "%"]]}},
    )
    for ticker in ["GAZP", "SU46020RMFS2"]:
        responses.add(
            responses.GET,
            f"https://iss.moex.com/iss/securities/{ticker}.json",
            json={"boards": {"data": []}},
        )

    client = MagicMock()
    client.get_candles = AsyncMock(return_value=[])

    runner = BackfillRunner(client=client, db_path=fresh_db, event_sink=_noop_sink)
    written = await runner.backfill_from_moex(today=date(2026, 9, 15))

    assert written == 0


# ---------- Tinkoff fallback returns empty (line 590) ----------


@responses.activate
async def test_tinkoff_fallback_no_data_skips_figi(fresh_db):
    """When MOEX returns no boards AND Tinkoff returns no candles, the
    figi is skipped silently (lines 590 / 548-550)."""
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/SBER.json",
        json={"boards": {"data": []}},
    )
    for ticker in ["GAZP", "SU46020RMFS2"]:
        responses.add(
            responses.GET,
            f"https://iss.moex.com/iss/securities/{ticker}.json",
            json={"boards": {"data": []}},
        )

    # Tinkoff returns empty for everyone
    client = MagicMock()
    client.get_candles = AsyncMock(return_value=[])

    runner = BackfillRunner(client=client, db_path=fresh_db, event_sink=_noop_sink)
    written = await runner.backfill_from_moex(today=date(2026, 9, 15))

    # No MOEX boards → all 3 fall to Tinkoff → all return empty → 0 written
    assert written == 0


# ---------- unhandled exception in backfill loop (line 230-232) ----------


@responses.activate
async def test_unhandled_exception_in_figi_loop_is_logged_and_continues(fresh_db):
    """If processing a single figi raises an unexpected exception, the
    outer loop must log it and continue with the next instrument.

    Covers the `except Exception` block at lines ~227-232.
    """
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/SBER.json",
        json={"boards": {"data": []}},
    )
    # GAZP triggers an unhandled exception inside the Tinkoff fallback path
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/GAZP.json",
        json={"boards": {"data": []}},
    )
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/SU46020RMFS2.json",
        json={"boards": {"data": []}},
    )

    client = MagicMock()

    async def get_candles_explosive(*_a, **_kw):
        raise RuntimeError("simulated Tinkoff outage")

    client.get_candles = AsyncMock(side_effect=get_candles_explosive)

    runner = BackfillRunner(client=client, db_path=fresh_db, event_sink=_noop_sink)
    # Should NOT raise — the per-figi try/except catches and logs
    written = await runner.backfill_from_moex(today=date(2026, 9, 15))

    assert written == 0
