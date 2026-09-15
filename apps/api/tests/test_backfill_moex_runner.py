from __future__ import annotations
import sqlite3
from datetime import date
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
