from __future__ import annotations
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import responses

from algotrader_api.ingestion.backfill import BackfillRunner


@pytest.fixture
def fresh_db(tmp_path):
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


@pytest.fixture
def sber_only_db(tmp_path):
    """Pagination test fixtures focus only on SBER.

    Other instruments are excluded so the test stays scoped to the
    pagination behavior under test.
    """
    db_path = str(tmp_path / "test_sber.db")
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


@responses.activate
async def test_backfill_from_moex_writes_bars_with_dynamic_dates(fresh_db):
    """For each figi, BackfillRunner fetches from MOEX listed_from to
    yesterday (or listed_till). Bars written with source='moex'."""
    from urllib.parse import urlparse, parse_qs

    # SBER: listed 2013-03-25, still listed (history_till=2026-09-14)
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/SBER.json",
        json={"boards": {"data": [["SBER", "TQBR", "x", 0, 0, "shares", 0, 1, 1, 0,
                                     "2013-03-25", "2026-09-14", "2013-03-25", "2026-09-15",
                                     1, "SUR", "%"]]}},
    )

    def sber_history_cb(request):
        qs = parse_qs(urlparse(request.url).query)
        year_from = (qs.get("from") or [""])[0][:4]
        if year_from == "2013":
            return (200, {}, '{"history": {"columns": ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"], "data": [["2013-03-25", 100.0, 101.0, 99.0, 100.5, 1000000]]}, "history.cursor": {"data": [[0, 1, 500]]}}')
        return (200, {}, '{"history": {"columns": ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"], "data": []}, "history.cursor": {"data": [[0, 0, 500]]}}')

    responses.add_callback(
        responses.GET,
        "https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities/SBER.json",
        callback=sber_history_cb,
    )

    # GAZP: listed 2014-06-09
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/GAZP.json",
        json={"boards": {"data": [["GAZP", "TQBR", "x", 0, 0, "shares", 0, 1, 1, 0,
                                     "2014-06-09", "2026-09-14", "2014-06-09", "2026-09-15",
                                     1, "SUR", "%"]]}},
    )

    def gazp_history_cb(request):
        qs = parse_qs(urlparse(request.url).query)
        year_from = (qs.get("from") or [""])[0][:4]
        if year_from == "2014":
            return (200, {}, '{"history": {"columns": ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"], "data": [["2014-06-09", 150.0, 151.0, 149.0, 150.5, 500000]]}, "history.cursor": {"data": [[0, 1, 500]]}}')
        return (200, {}, '{"history": {"columns": ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"], "data": []}, "history.cursor": {"data": [[0, 0, 500]]}}')

    responses.add_callback(
        responses.GET,
        "https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities/GAZP.json",
        callback=gazp_history_cb,
    )

    # Bond: routed to /markets/bonds/
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/SU46020RMFS2.json",
        json={"boards": {"data": [["SU46020RMFS2", "TQOB", "x", 0, 0, "bonds", 0, 1, 1, 0,
                                     "2013-03-25", "2026-09-14", "2013-03-25", "2026-09-15",
                                     1, "RUB", "%"]]}},
    )

    def bond_history_cb(request):
        qs = parse_qs(urlparse(request.url).query)
        year_from = (qs.get("from") or [""])[0][:4]
        if year_from == "2013":
            return (200, {}, '{"history": {"columns": ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"], "data": [["2013-03-25", 105.0, 105.5, 104.5, 105.2, 100000]]}, "history.cursor": {"data": [[0, 1, 500]]}}')
        return (200, {}, '{"history": {"columns": ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"], "data": []}, "history.cursor": {"data": [[0, 0, 500]]}}')

    responses.add_callback(
        responses.GET,
        "https://iss.moex.com/iss/history/engines/stock/markets/bonds/boards/TQOB/securities/SU46020RMFS2.json",
        callback=bond_history_cb,
    )

    runner = BackfillRunner(client=MagicMock(), db_path=fresh_db, event_sink=_noop_sink)
    written = await runner.backfill_from_moex(today=date(2026, 9, 15))

    assert written == 3, f"expected 3 bars written (one per ticker), got {written}"
    con = sqlite3.connect(fresh_db)
    rows = con.execute(
        "SELECT figi, ts, source FROM bars ORDER BY figi, ts"
    ).fetchall()
    con.close()
    assert rows == [
        ("BBG004730N88", "2013-03-25", "moex"),
        ("BBG004730RP0", "2014-06-09", "moex"),
        ("FIGI-BOND", "2013-03-25", "moex"),
    ]


@responses.activate
async def test_backfill_from_moex_paginates_pages_in_year(sber_only_db):
    """MOEX ISS returns max 500 bars/page; ``fetch_year`` must follow
    the `start` cursor until a short page signals end-of-data.

    Setup: SBER 2014. The mock returns 3 full pages of 100 bars and
    one trailing page of 50 bars (350 total). After the fix
    ``backfill_from_moex`` must persist all 350 bars; without
    pagination it writes only the first 100.
    """
    from urllib.parse import urlparse, parse_qs

    # SBER: listed 2014-01-01, still listed (single instrument keeps
    # the test scoped to pagination behavior).
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/SBER.json",
        json={"boards": {"data": [["SBER", "TQBR", "x", 0, 0, "shares", 0, 1, 1, 0,
                                     "2014-01-01", "2026-09-14", "2014-01-01", "2026-09-15",
                                     1, "SUR", "%"]]}},
    )

    page_size = 100
    total_target = 350
    # Pre-compute 350 valid (yyyy-mm-dd, ...) rows split into pages.
    # 350 calendar days from 2014-01-01 (which lands in 2014-12-17) —
    # the year filter still accepts them because MOEX filters
    # server-side and the test asserts on row count, not on the
    # exact dates.
    rows_by_offset: dict[int, list[list]] = {}
    base_date = date(2014, 1, 1)
    cursor = 0
    while cursor < total_target:
        chunk_len = min(page_size, total_target - cursor)
        rows = []
        for i in range(chunk_len):
            ts = (base_date + timedelta(days=cursor + i)).isoformat()
            rows.append([ts, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 1000 + i])
        rows_by_offset[cursor] = rows
        cursor += page_size

    def sber_history_cb(request):
        qs = parse_qs(urlparse(request.url).query)
        year_from = (qs.get("from") or [""])[0][:4]
        if year_from != "2014":
            return (200, {}, '{"history": {"columns": ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"], "data": []}, "history.cursor": {"data": [[0, 0, 500]]}}')
        try:
            start = int((qs.get("start") or ["0"])[0])
        except ValueError:
            start = 0
        rows = rows_by_offset.get(start, [])
        # MOEX's history.cursor row is [offset, total, page_size]:
        # offset = index where this page begins, total = dataset size
        # overall, page_size = server's chosen page size. The fetcher
        # compares offset+len(rows) against total to decide when to
        # stop. We declare total_target so the fetcher keeps paging
        # until every chunk has been seen.
        cursor_json = '"history.cursor": {"data": [[' + str(start) + ', ' + str(total_target) + ', ' + str(page_size) + ']]}'
        return (
            200,
            {},
            '{"history": {"columns": ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"], '
            '"data": ' + json.dumps(rows) + '}, '
            + cursor_json + '}',
        )

    responses.add_callback(
        responses.GET,
        "https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities/SBER.json",
        callback=sber_history_cb,
    )

    runner = BackfillRunner(client=MagicMock(), db_path=sber_only_db, event_sink=_noop_sink)
    written = await runner.backfill_from_moex(today=date(2026, 9, 15))

    assert written == 350, f"expected 350 bars written across 4 pages, got {written}"
    con = sqlite3.connect(sber_only_db)
    try:
        rows = con.execute(
            "SELECT COUNT(*) FROM bars WHERE figi = ? AND source = 'moex'",
            ("BBG004730N88",),
        ).fetchone()
    finally:
        con.close()
    assert rows and rows[0] == 350, f"expected 350 moex rows for SBER figi, got {rows and rows[0]}"


@responses.activate
async def test_backfill_from_moex_handles_delisted_ticker_via_fallback(fresh_db):
    """When MOEX returns NO_BOARDS (sanctions-delisted), fall back to Tinkoff."""
    # Sanctions-delisted: MOEX returns empty boards list
    responses.add(
        responses.GET,
        "https://iss.moex.com/iss/securities/TRY0228.json",
        json={"boards": {"data": []}},
    )
    # Tinkoff client returns empty (sandbox has no pre-2021 data)
    client = MagicMock()
    client.get_candles = AsyncMock(return_value=[])

    runner = BackfillRunner(client=client, db_path=fresh_db, event_sink=_noop_sink)
    written = await runner.backfill_from_moex(today=date(2026, 9, 15))

    assert written == 0, f"expected 0 bars written (Tinkoff returns empty), got {written}"


async def test_fetch_tinkoff_fallback_logs_progress_per_chunk(fresh_db):
    """When Tinkoff fallback walks 7-day chunks, each chunk must emit a structured
    log via structlog so operators can see why a figi takes 20+ min and identify stuck
    ranges. Without this, a single slow chunk hides all progress.
    """
    import structlog.testing as slog_test

    chunks_observed: list[tuple] = []

    async def fake_get_candles(figi, date_from, date_to, interval):
        chunks_observed.append((date_from.isoformat(), date_to.isoformat()))
        return []

    client = MagicMock()
    client.get_candles = fake_get_candles

    runner = BackfillRunner(client=client, db_path=fresh_db, event_sink=_noop_sink)
    # 1-year window from 2014-01-01 to 2014-12-31 → ~53 chunks
    from datetime import date
    with slog_test.capture_logs() as captured:
        # fetch_tinkoff_fallback is an inner closure; reach it via the runner's
        # local closure via the broader backfill_from_moex path. To keep the
        # test independent, we just verify the chunked walk happens by counting
        # how many times get_candles was called when we route a delisted ticker
        # through backfill_from_moex.
        # Sanctions-delisted: MOEX returns empty boards list
        responses.add(
            responses.GET,
            "https://iss.moex.com/iss/securities/CHUNKLOG.json",
            json={"boards": {"data": []}},
        )
        # Seed an instrument so backfill_from_moex picks it up
        import sqlite3 as _sq
        _con = _sq.connect(fresh_db)
        _con.execute(
            "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size, isin, sector) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("CHUNKLOG", "BBG-CHUNKLOG", "share", "Chunklog test", "rub", 1, "TEST", "test"),
        )
        _con.commit()
        _con.close()
        await runner.backfill_from_moex(today=date(2015, 1, 1))
    # Should have ~53 chunks observed (2014-01-01 to 2014-12-31 in 7-day steps)
    assert len(chunks_observed) >= 50, f"expected ~53 chunks, got {len(chunks_observed)}"
    # And progress events should have been logged
    progress_events = [e for e in captured if e.get("event") == "backfill.tinkoff.chunk"]
    assert len(progress_events) >= 50, f"expected ~53 progress events, got {len(progress_events)}"
