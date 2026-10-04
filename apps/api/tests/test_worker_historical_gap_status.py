"""Actual worker historical-gap error attribution acceptance matrix."""
from datetime import date
import sqlite3
from types import SimpleNamespace

import pytest
import worker
from algotrader_api.db import sqlite as sqlitedb
from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.ingestion.backfill import BackfillRunner
from algotrader_api.ingestion.backfill import BackfillEvent
from algotrader_api.data_quality.gap_recovery import find_gaps


@pytest.mark.parametrize("fail", [False, True], ids=["healthy-empty", "upstream-error"])
def test_actual_historical_phase_reports_transport_error(tmp_path, monkeypatch, fail):
    db = tmp_path / "state.db"
    sqlitedb.close_all()
    sqlitedb.run_migrations(str(db), MIGRATIONS_DIR)
    sqlitedb.close_all()
    con = sqlite3.connect(db)
    con.execute("INSERT INTO instruments(figi,ticker,class,name,currency,lot_size,isin,source_updated_at,expected_bars) VALUES ('PARENT-HIST','GAZP','share','fixture','RUB',1,'RU0007661625','2026-09-07',3)")
    for day in ("2026-09-07", "2026-09-09"):
        con.execute("INSERT INTO bars(figi,ts,open,high,low,close,volume,source) VALUES ('PARENT-HIST',?,1,1,1,1,1,'tinkoff')", (day,))
    con.commit()
    before = con.execute("SELECT * FROM bars ORDER BY ts").fetchall()
    con.close()
    gaps = find_gaps(str(db))
    assert len(gaps) == 1 and gaps[0].from_ == gaps[0].to_ == date(2026, 9, 8)

    class Client:
        def __init__(self):
            self.calls = 0
            self.closed = False

        async def get_candles(self, **kwargs):
            self.calls += 1
            assert self.calls <= 1
            assert kwargs["figi"] == "PARENT-HIST"
            assert kwargs["date_from"] == kwargs["date_to"] == date(2026, 9, 8)
            if fail:
                raise RuntimeError("synthetic upstream failure")
            return []

        async def aclose(self):
            self.closed = True

    client = Client()
    monkeypatch.setattr(worker.client_mod, "make_client", lambda **kwargs: client)
    monkeypatch.setattr(worker, "_collect_trailing_gaps", lambda *args: [])
    events = []
    original_emit = BackfillRunner._emit

    async def capture(self, event_type, payload):
        if event_type == "ticker_progress":
            events.append({"figi": payload.get("figi"), "status": payload.get("status")})
        return await original_emit(self, event_type, payload)

    monkeypatch.setattr(BackfillRunner, "_emit", capture)
    try:
        ok, detail = worker._step_gap_recovery(str(db))
        con = sqlite3.connect(db)
        after = con.execute("SELECT * FROM bars ORDER BY ts").fetchall()
        status = con.execute("SELECT last_run_status FROM instrument_metadata WHERE figi='PARENT-HIST'").fetchone()[0]
        con.close()
        assert client.closed and client.calls == 1
        assert after == before
        assert events == [{"figi": "PARENT-HIST", "status": "error" if fail else "empty"}]
        assert status == ("error" if fail else "skipped")
        assert ok is (not fail), "Historical error must fail; healthy empty remains successful"
        if fail:
            assert "failed=1" in detail
    finally:
        sqlitedb.close_all()


def run_matrix(tmp_path, monkeypatch, figis, *, failed=frozenset(), successful=frozenset(), trailing=False, unrelated=False):
    db = tmp_path / "matrix.db"
    sqlitedb.close_all()
    sqlitedb.run_migrations(str(db), MIGRATIONS_DIR)
    sqlitedb.close_all()
    con = sqlite3.connect(db)
    for figi in figis:
        con.execute("INSERT INTO instruments(figi,ticker,class,name,currency,lot_size,isin,source_updated_at,expected_bars) VALUES (?,'GAZP','share','fixture','RUB',1,'RU0007661625','2026-09-07',3)", (figi,))
        for day in ("2026-09-07", "2026-09-09"):
            con.execute("INSERT INTO bars(figi,ts,open,high,low,close,volume,source) VALUES (?,?,1,1,1,1,1,'tinkoff')", (figi, day))
    con.commit()
    original_rows = con.execute("SELECT * FROM bars ORDER BY figi,ts").fetchall()
    con.close()
    gaps = find_gaps(str(db))
    assert {gap.figi for gap in gaps} == set(figis)
    assert len(gaps) == len(figis)
    assert all(gap.from_ == gap.to_ == date(2026, 9, 8) for gap in gaps)
    if len(figis) > 1:
        assert gaps[0].figi == "AAA-FAIL"

    class Client:
        def __init__(self):
            self.calls = []
            self.closed = False

        async def get_candles(self, **kwargs):
            self.calls.append((kwargs["figi"], kwargs["date_from"], kwargs["date_to"]))
            assert len(self.calls) <= len(figis) + int(trailing)
            allowed_dates = {date(2026, 9, 8)} | ({date(2026, 9, 10)} if trailing else set())
            assert kwargs["date_from"] == kwargs["date_to"] and kwargs["date_from"] in allowed_dates
            assert kwargs["interval"] == "CANDLE_INTERVAL_DAY"
            if kwargs["figi"] in failed:
                raise RuntimeError("synthetic upstream failure")
            if kwargs["figi"] in successful:
                quote = lambda: SimpleNamespace(units=1, nano=0)
                return [SimpleNamespace(time=SimpleNamespace(year=2026, month=9, day=8), open=quote(), high=quote(), low=quote(), close=quote(), volume=1, is_complete=True)]
            return []

        async def aclose(self):
            self.closed = True

    client = Client()
    monkeypatch.setattr(worker.client_mod, "make_client", lambda **kwargs: client)
    selected = [(figis[0], date(2026, 9, 10), date(2026, 9, 10), False)] if trailing else []
    monkeypatch.setattr(worker, "_collect_trailing_gaps", lambda *args: selected)
    observed = []
    injected = []
    original_emit = BackfillRunner._emit

    async def emit(self, kind, payload):
        if kind == "ticker_progress":
            observed.append((payload.get("figi"), payload.get("status")))
            if unrelated:
                for other in ("UNREQUESTED", None, 1, []):
                    injected.append((other, "error"))
                    await self.event_sink(BackfillEvent(type=kind, run_id=self.run_id, ts="fixture", payload={"figi": other, "status": "error"}))
        return await original_emit(self, kind, payload)

    monkeypatch.setattr(BackfillRunner, "_emit", emit)
    try:
        result = worker._step_gap_recovery(str(db))
        con = sqlite3.connect(db)
        rows = con.execute("SELECT * FROM bars ORDER BY figi,ts").fetchall()
        states = dict(con.execute("SELECT figi,last_run_status FROM instrument_metadata"))
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        con.close()
        assert client.closed
        # Actual runner observations must correspond exactly to transport calls;
        # unrelated injected events are distinguishable and never add calls.
        assert [figi for figi, _ in observed] == [call[0] for call in client.calls]
        return result, original_rows, rows, states, client.calls, observed, injected
    finally:
        sqlitedb.close_all()


def test_historical_failure_preserves_committed_success_and_continues(tmp_path, monkeypatch):
    (ok, detail), before, after, states, calls, _, _ = run_matrix(tmp_path, monkeypatch, ["AAA-FAIL", "BBB-GOOD", "CCC-EMPTY"], failed={"AAA-FAIL"}, successful={"BBB-GOOD"})
    assert ok is False and "failed=1" in detail
    assert "historical=1 across 3 gaps" in detail
    assert [call[0] for call in calls] == ["AAA-FAIL", "BBB-GOOD", "CCC-EMPTY"]
    assert len(after) == len(before) + 1 and all(row in after for row in before)
    assert [row for row in after if row not in before] == [("BBB-GOOD", "2026-09-08", 1.0, 1.0, 1.0, 1.0, 1, "tinkoff")]
    assert states == {"AAA-FAIL": "error", "BBB-GOOD": "ok", "CCC-EMPTY": "skipped"}


def test_same_figi_historical_and_trailing_failure_counts_once(tmp_path, monkeypatch):
    (ok, detail), before, after, states, calls, _, _ = run_matrix(tmp_path, monkeypatch, ["AAA-FAIL"], failed={"AAA-FAIL"}, trailing=True)
    assert ok is False and "failed=1" in detail
    assert before == after and states == {"AAA-FAIL": "error"}
    assert calls == [("AAA-FAIL", date(2026, 9, 8), date(2026, 9, 8)), ("AAA-FAIL", date(2026, 9, 10), date(2026, 9, 10))]


def test_unrequested_or_nonstring_error_events_are_not_attributed(tmp_path, monkeypatch):
    (ok, detail), before, after, states, calls, observed, injected = run_matrix(tmp_path, monkeypatch, ["AAA-EMPTY"], unrelated=True)
    assert ok is True and "failed=0" in detail
    assert before == after and states == {"AAA-EMPTY": "skipped"}
    assert calls == [("AAA-EMPTY", date(2026, 9, 8), date(2026, 9, 8))]
    assert injected == [("UNREQUESTED", "error"), (None, "error"), (1, "error"), ([], "error")]
