"""Offline behavioral checks for legacy backfill failure and completion paths."""
from datetime import date, timedelta
import sqlite3
from types import SimpleNamespace

import pytest
import responses
import structlog

from algotrader_api.ingestion import backfill
from algotrader_api.db import bars_sqlite

FIGI = "BBG004730N88"
ISIN = "RU0009029540"
TODAY = date(2026, 9, 15)
META_URL = "https://iss.moex.com/iss/securities/SBER.json"
HISTORY_URL = "https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities/SBER.json"


class Broker:
    def __init__(self, rows=(), error=None):
        self.rows = list(rows)
        self.error = error
        self.calls = []

    async def get_candles(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.rows


@pytest.fixture
def runner(fresh_db, monkeypatch):
    # Rebinding a fresh proxy avoids leaking a finalized structlog logger.
    class Clock(date):
        @classmethod
        def today(cls):
            return TODAY
    monkeypatch.setattr(backfill, "date", Clock)
    monkeypatch.setattr(backfill, "logger", structlog.get_logger("completion"))
    with sqlite3.connect(fresh_db) as conn:
        conn.execute(
            "INSERT INTO instruments (ticker,figi,class,name,currency,lot_size,isin) "
            "VALUES ('SBER',?,'share','Sber','rub',10,?)", (FIGI, ISIN),
        )
    events = []

    async def sink(event):
        events.append(event)

    result = backfill.BackfillRunner(client=Broker(), db_path=fresh_db, event_sink=sink)
    result.test_events = events
    return result


def metadata(http, *, listed_from="2026-09-01", listed_till="2026-09-14", status=200):
    http.add(responses.GET, META_URL, status=status, json={
        "boards": {"data": [["SBER", "TQBR", "x", 0, 0, "shares", 0, 1, 1, 0,
                             listed_from, listed_till, listed_from, listed_till, 1, "SUR", "%"]]},
        "description": {"data": [["NAME", "NAME", "Sber"], ["ISIN", "ISIN", ISIN]]},
    })


def history(http, *, zero=False, status=200, wrong_identity=False):
    http.add(responses.GET, HISTORY_URL, status=status, json={
        "history": {
            "columns": ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME",
                        "SECID", "BOARDID", "NUMTRADES", "VALUE"],
            "data": [["2026-09-14", *([None]*4 if zero else [100, 101, 99, 100]),
                      0 if zero else 1000, "OTHER" if wrong_identity else "SBER",
                      "TQBR", 0 if zero else 10, 0 if zero else 100000]],
        },
        "history.cursor": {"data": [[0, 1, 100]]},
    })


def bar(ts="2026-09-14"):
    return dict(ts=ts, open=100, high=101, low=99, close=100, volume=1000, is_complete=True)


def stored(runner):
    with sqlite3.connect(runner.db_path) as conn:
        return conn.execute("SELECT figi,ts,source FROM bars ORDER BY ts").fetchall()


def messages(runner):
    with sqlite3.connect(runner.db_path) as conn:
        return [r[0] for r in conn.execute("SELECT message FROM ingestion_logs")]


@pytest.mark.parametrize("days", [0, -1])
async def test_recent_tail_disabled_without_http(runner, days):
    with responses.RequestsMock() as http:
        assert await runner.backfill_moex_recent_tail(days=days) == 0
        assert len(http.calls) == 0
    assert stored(runner) == []


@pytest.mark.parametrize("condition", ["stopped", "no_ticker", "invalid_date", "delisted", "wrong_rows"])
async def test_recent_tail_skips_unusable_instrument_without_writes(runner, condition):
    with responses.RequestsMock(assert_all_requests_are_fired=False) as http:
        metadata(http, listed_till="bad" if condition == "invalid_date" else
                 "2020-01-01" if condition == "delisted" else "2026-09-14")
        if condition == "stopped":
            runner._stop_flag.set()
        if condition == "no_ticker":
            with sqlite3.connect(runner.db_path) as conn:
                conn.execute("UPDATE instruments SET ticker='' WHERE figi=?", (FIGI,))
        if condition == "wrong_rows":
            history(http, wrong_identity=True)
        assert await runner.backfill_moex_recent_tail(today=TODAY) == 0
    assert stored(runner) == []
    assert runner.client.calls == []


async def test_recent_tail_rebuilds_absent_metadata_caches(runner):
    runner._moex_meta = None
    runner._moex_meta_lock = None
    with responses.RequestsMock() as http:
        metadata(http)
        history(http)
        assert await runner.backfill_moex_recent_tail(today=TODAY) == 1
    assert runner._moex_meta["SBER"]["isin"] == ISIN
    assert stored(runner) == [(FIGI, "2026-09-14", "moex")]


async def test_recent_tail_zero_feed_persists_evidence_not_price_bar(runner):
    with responses.RequestsMock() as http:
        metadata(http)
        history(http, zero=True)
        assert await runner.backfill_moex_recent_tail(today=TODAY) == 0
    with sqlite3.connect(runner.db_path) as conn:
        rows = conn.execute("SELECT figi,session_date FROM moex_no_trade_evidence").fetchall()
    assert rows == [(FIGI, "2026-09-14")]
    assert stored(runner) == []


@pytest.mark.parametrize("failed_count", [1, 2])
async def test_recent_tail_count_read_failure_reports_attempts_but_commits_bar(runner, monkeypatch, failed_count):
    counts = 0

    class CountFaultConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            nonlocal counts
            if "COUNT(*) FROM bars" in sql and "BETWEEN" in sql:
                counts += 1
                if counts == failed_count:
                    # Fail through SQLite's real authorization error path.
                    self.set_authorizer(lambda *args: sqlite3.SQLITE_DENY)
                    try:
                        return super().execute(sql, parameters)
                    finally:
                        self.set_authorizer(None)
            return super().execute(sql, parameters)

    conn = sqlite3.connect(runner.db_path, factory=CountFaultConnection)
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(bars_sqlite, "get_connection", lambda path: conn)
    try:
        with responses.RequestsMock() as http:
            metadata(http)
            history(http)
            assert await runner.backfill_moex_recent_tail(today=TODAY) == 1
        assert counts >= failed_count
        assert stored(runner) == [(FIGI, "2026-09-14", "moex")]
    finally:
        conn.close()


async def test_recent_tail_writer_failure_isolated_and_logged(runner):
    # A real SQLite trigger refuses bars; no replacement of the writer itself.
    with sqlite3.connect(runner.db_path) as conn:
        conn.execute("CREATE TRIGGER refuse_bar BEFORE INSERT ON bars BEGIN SELECT RAISE(FAIL,'fixture writer unavailable'); END")
    with responses.RequestsMock() as http:
        metadata(http)
        history(http)
        assert await runner.backfill_moex_recent_tail(today=TODAY) == 0
    assert stored(runner) == []
    assert any("moex_recent_tail failed" in m and "fixture writer unavailable" in m for m in messages(runner))


@pytest.mark.parametrize("error,expected", [(RuntimeError("rate quota denied"), "rate-limited"),
                                           (RuntimeError("Channel is closed"), "channel closed")])
async def test_tinkoff_abort_stops_remaining_chunks_and_records_error(runner, error, expected):
    runner.client = Broker(error=error)
    assert await runner._backfill_one_tinkoff(figi=FIGI, ticker="SBER", from_=date(2026, 8, 1), to=date(2026, 9, 14)) == 0
    assert len(runner.client.calls) == 1
    assert runner._get_metadata(FIGI)["last_run_status"] == "error"
    assert any(expected in m for m in messages(runner))
    assert runner.test_events[-1].payload["status"] == "error"
    assert stored(runner) == []


async def test_tinkoff_drops_missing_time_but_accepts_object_time_shape(runner):
    runner.client = Broker(rows=[dict(time=SimpleNamespace(year=2026, month=9, day=14),
                                     open=100, high=101, low=99, close=100, volume=1000, is_complete=True),
                                dict(open=100, high=101, low=99, close=100, volume=1000, is_complete=True)])
    assert await runner._backfill_one_tinkoff(figi=FIGI, ticker="SBER", from_=date(2026, 9, 14), to=date(2026, 9, 14)) == 1
    assert stored(runner)[0][:2] == (FIGI, "2026-09-14")


def test_latest_date_ignores_bad_sdk_timestamps_and_invalid_calendar_dates(runner):
    assert runner._extract_last_bar_ts([
        {"ts": "invalid", "time": {"year": 2026, "month": 9, "day": 14}},
        {"time": {"year": 2026, "month": 2, "day": 30}},
        {"ts": "2026-09-13"},
    ]) == "2026-09-14"


def test_last_trading_day_handles_unmigrated_database_and_exhausted_holidays(tmp_path):
    path = str(tmp_path / "calendar.db")
    assert backfill._last_trading_day(date(2026, 9, 14), path) == date(2026, 9, 11)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE moex_holidays(date TEXT)")
        conn.executemany("INSERT INTO moex_holidays VALUES (?)", [("2026-09-14",), ("2026-09-11",)])
    assert backfill._last_trading_day(TODAY, path, max_lookback_days=3) == date(2026, 9, 11)


def test_breaker_without_expiry_remains_open_until_success(runner):
    runner._seed_metadata_for_figi(FIGI)
    with sqlite3.connect(runner.db_path) as conn:
        conn.execute("UPDATE instrument_metadata SET tinkoff_breaker_open=1,tinkoff_breaker_open_until=NULL WHERE figi=?", (FIGI,))
    assert backfill._tinkoff_breaker_is_open(runner.db_path, FIGI)
    backfill._tinkoff_breaker_record_success(runner.db_path, FIGI)
    assert not backfill._tinkoff_breaker_is_open(runner.db_path, FIGI)


def test_connection_path_closed_connection_fails_with_cause(tmp_path):
    conn = sqlite3.connect(tmp_path / "closed.db")
    conn.close()
    with pytest.raises(RuntimeError, match="cannot read PRAGMA database_list") as exc:
        backfill._resolve_db_path_from_connection(conn)
    assert isinstance(exc.value.__cause__, sqlite3.ProgrammingError)


@pytest.mark.parametrize("rows", [[{}], [{}, SimpleNamespace()]])
async def test_bond_depth_unusable_timestamp_feed_is_not_written(runner, monkeypatch, rows):
    from algotrader_api.ingestion import client, rate_limit
    with sqlite3.connect(runner.db_path) as conn:
        conn.execute("UPDATE instruments SET class='bond'")
    broker = Broker(rows=rows)
    monkeypatch.setattr(client, "make_client", lambda **kwargs: broker)
    class Limiter:
        async def acquire(self, method):
            return None
    monkeypatch.setattr(rate_limit, "get_global", lambda: Limiter())
    with sqlite3.connect(runner.db_path) as conn:
        result = await backfill._async_backfill_impl(conn=conn)
    assert result == dict(figis_processed=1, bars_added=0, skipped=0, errors=0)
    assert stored(runner) == []


@pytest.mark.parametrize("condition", ["full", "covered", "future", "stopped"])
async def test_historical_walker_honors_full_delta_listing_and_stop(runner, condition):
    if condition == "covered":
        bars_sqlite.replace_bars_for_figi(runner.db_path, FIGI,
            [bar((date(2026, 9, 1) + timedelta(days=i)).isoformat()) for i in range(14)], replace=False)
    before = stored(runner)
    with responses.RequestsMock(assert_all_requests_are_fired=False) as http:
        metadata(http, listed_from="2027-01-01" if condition == "future" else "2026-09-01")
        if condition == "stopped":
            def stop(request):
                runner._stop_flag.set()
                return (200, {}, '{"boards":{"data":[]}}')
            http.reset()
            http.add_callback(responses.GET, META_URL, callback=stop)
        history(http)
        written = await runner.backfill_from_moex(today=TODAY, delta_only=condition == "covered")
    assert written == (1 if condition == "full" else 0)
    if condition == "full":
        assert stored(runner) == [(FIGI, "2026-09-14", "moex")]
    else:
        assert stored(runner) == before


@pytest.mark.parametrize("listing", ["bad", "2010-01-01"])
async def test_historical_broker_fallback_bounds_bad_or_old_listing_and_writes(runner, monkeypatch, listing):
    instruments = runner._list_instruments()
    # The public walker accepts instrument adapters with extra listing metadata.
    for instrument in instruments:
        instrument["listed_from"] = listing
    monkeypatch.setattr(runner, "_list_instruments", lambda **kwargs: instruments)
    runner.client = Broker(rows=[bar()])
    with responses.RequestsMock() as http:
        http.add(responses.GET, META_URL, status=200, json={"boards": {"data": []}})
        assert await runner.backfill_from_moex(today=TODAY) >= 1
    assert stored(runner) == [(FIGI, "2026-09-14", "tinkoff")]
    assert min(call["date_from"] for call in runner.client.calls) >= date(2026, 6, 16)
    assert not backfill._tinkoff_breaker_is_open(runner.db_path, FIGI)


async def test_historical_broker_fallback_skips_old_gap_when_recent_window_complete(runner):
    bars_sqlite.replace_bars_for_figi(runner.db_path, FIGI,
        [bar((TODAY - timedelta(days=i)).isoformat()) for i in range(1, 100)], replace=False)
    before = stored(runner)
    with responses.RequestsMock() as http:
        http.add(responses.GET, META_URL, status=200, json={"boards": {"data": []}})
        assert await runner.backfill_from_moex(today=TODAY) == 0
    assert runner.client.calls == []
    assert stored(runner) == before


async def test_full_history_stop_event_prevents_anchor_walk(runner):
    async def sink(event):
        if event.type == "status":
            runner._stop_flag.set()
    runner.event_sink = sink
    assert await runner.run_full_history() == 0
    assert runner.tickers_done == 0
    assert runner.client.calls == []


@pytest.mark.parametrize("path", ["tail", "public", "per_ticker"])
async def test_evidence_failure_does_not_lose_valid_bar(runner, path):
    with sqlite3.connect(runner.db_path) as conn:
        conn.execute("CREATE TRIGGER refuse_evidence BEFORE INSERT ON moex_no_trade_evidence BEGIN SELECT RAISE(FAIL,'fixture evidence unavailable'); END")
    with responses.RequestsMock() as http:
        metadata(http)
        http.add(responses.GET, HISTORY_URL, status=200, json={
            "history": {"columns": ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "SECID", "BOARDID", "NUMTRADES", "VALUE"],
                        "data": [["2026-09-11", None, None, None, None, 0, "SBER", "TQBR", 0, 0],
                                 ["2026-09-14", 100, 101, 99, 100, 1000, "SBER", "TQBR", 10, 100000]]},
            "history.cursor": {"data": [[0, 2, 100]]}})
        if path == "tail":
            written = await runner.backfill_moex_recent_tail(today=TODAY)
        elif path == "public":
            written = await runner.backfill_from_moex(today=TODAY)
        else:
            written = await runner._backfill_one_moex(figi=FIGI, ticker="SBER", from_=date(2026, 9, 1), to=TODAY)
        assert written == 1
    assert stored(runner) == [(FIGI, "2026-09-14", "moex" if path != "per_ticker" else "tinkoff")]
    with sqlite3.connect(runner.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM moex_no_trade_evidence").fetchone()[0] == 0
    if path == "tail":
        assert any("no-trade evidence failed" in m for m in messages(runner))


async def test_moex_walker_current_window_does_not_bridge_to_broker(runner):
    with responses.RequestsMock() as http:
        metadata(http)
        history(http)
        assert await runner._backfill_one_moex(figi=FIGI, ticker="SBER", from_=date(2026, 9, 1), to=TODAY) == 1
    assert runner.client.calls == []
    assert stored(runner)[0][:2] == (FIGI, "2026-09-14")


async def test_moex_walker_trailing_bridge_writes_broker_rows_additively(runner):
    runner.client = Broker(rows=[bar("2026-09-10")])
    with responses.RequestsMock() as http:
        metadata(http)
        history(http)
        assert await runner._backfill_one_moex(figi=FIGI, ticker="SBER", from_=date(2026, 9, 1), to=TODAY - timedelta(days=1)) > 1
    assert len(runner.client.calls) > 1
    assert [row[:2] for row in stored(runner)] == [(FIGI, "2026-09-10"), (FIGI, "2026-09-14")]


async def test_discovery_incomplete_broker_rows_do_not_create_instruments(runner):
    class DiscoveryBroker:
        async def get_shares(self):
            return [{"ticker": "NOFIGI"}, {"figi": "NOTICKER"}]
        async def get_bonds(self):
            return []
        async def get_etfs(self):
            return []
    runner.client = DiscoveryBroker()
    assert await runner._discover_universe() == 2
    assert [r["figi"] for r in runner._list_instruments()] == [FIGI]
    assert runner._get_metadata("NOTICKER")["last_run_status"] == "pending"
    assert runner._get_metadata("NOFIGI") is None
    assert len(runner.test_events) == 2


async def test_legacy_list_only_fetcher_writes_bars_without_certifying_evidence(runner):
    runner._fetch_year_moex_outcome = None
    with responses.RequestsMock() as http:
        metadata(http)
        history(http)
        assert await runner._backfill_one_moex(figi=FIGI, ticker="SBER", from_=date(2026, 9, 1), to=TODAY) == 1
    assert stored(runner)[0][:2] == (FIGI, "2026-09-14")
    with sqlite3.connect(runner.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM moex_no_trade_evidence").fetchone()[0] == 0
