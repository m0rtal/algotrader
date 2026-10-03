"""Regression proofs for raw evidence, caller windows and malformed pages.

Only HTTP/metadata boundaries are faked; fetchers, CLI, SQL and locks are real.
"""
from datetime import date
import importlib.util
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from algotrader_api.ingestion import backfill, no_trade_evidence as nte


@pytest.fixture
def evidence_db(tmp_path):
    db = tmp_path / "evidence.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE instruments (figi TEXT PRIMARY KEY, ticker TEXT, isin TEXT,
                                  class TEXT, listed_till TEXT);
        INSERT INTO instruments VALUES ('F', 'GAZP', 'RU', 'share', NULL);
        CREATE TABLE moex_holidays (date TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE bars (figi TEXT, ts TEXT, open REAL, high REAL, low REAL,
                           close REAL, volume INTEGER, source TEXT);
        CREATE TABLE moex_no_trade_evidence (
            figi TEXT, session_date TEXT, board TEXT, isin TEXT,
            observed_at TEXT, expires_at TEXT, PRIMARY KEY(figi, session_date));
    """)
    yield db, con
    con.close()


def zero(ts="2026-09-01"):
    return dict(ts=ts, open=None, high=None, low=None, close=None, volume=0,
                _numtrades=0, _value=0, _secid="GAZP", _boardid="TQBR")


def record(db, con, rows, **kw):
    return nte.record_historical_no_trade_evidence(
        con, db_path=str(db), figi="F", ticker="GAZP", rows=rows,
        board="TQBR", isin="RU", outcome="complete", today=date(2026, 10, 2),
        **({"from_d": date(2026, 9, 1), "to_d": date(2026, 9, 30)} | kw),
    )


@pytest.mark.parametrize("change", [
    {"open": 1, "high": 1, "low": 1, "close": 1, "volume": 10,
     "_numtrades": 2, "_value": 10},
    {"open": "MISSING"}, {"high": "MISSING"}, {"low": "MISSING"},
    {"close": "MISSING"}, {"volume": "MISSING"},
    {"_numtrades": "MISSING"}, {"_value": "MISSING"},
    {"volume": False}, {"_numtrades": False}, {"_value": False},
    {"volume": 0.5}, {"volume": float("nan")}, {"volume": -1},
    {"volume": None}, {"volume": float("inf")},
    {"_numtrades": float("nan")}, {"_numtrades": 0.5}, {"_numtrades": -1},
    {"_value": float("inf")}, {"_value": -1},
    {"_numtrades": None}, {"_value": None}, {"volume": "0"},
])
def test_f1_helper_requires_explicit_zero_shape(evidence_db, change):
    db, con = evidence_db
    row = zero()
    for key, value in change.items():
        if value == "MISSING":
            row.pop(key)
        else:
            row[key] = value
    assert record(db, con, [row]) == 0
    assert con.execute("SELECT COUNT(*) FROM moex_no_trade_evidence").fetchone()[0] == 0


COLUMNS = ["TRADEDATE", "SECID", "BOARDID", "OPEN", "HIGH", "LOW", "CLOSE",
           "VOLUME", "NUMTRADES", "VALUE"]
POSITIVE = ["2026-09-02", "GAZP", "TQBR", 1, 2, 1, 2, 10, 2, 20]
ZERO = ["2026-09-01", "GAZP", "TQBR", None, None, None, None, 0, 0, 0]


def page(rows, cursor=(0, 2, 2)):
    return {"history": {"columns": COLUMNS, "data": rows},
            "history.cursor": {"data": [list(cursor)]}}


def http_pages(monkeypatch, payloads):
    remaining = iter(payloads)
    def get(*args, **kw):
        payload = next(remaining)  # finite fixture: unexpected requests fail
        return SimpleNamespace(status_code=200, json=lambda: payload)
    monkeypatch.setattr(backfill.requests, "get", get)


def cli(monkeypatch, db):
    path = Path(__file__).resolve().parents[1] / "scripts/backfill_no_trade_evidence.py"
    spec = importlib.util.spec_from_file_location("final_evidence_cli", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "path", sys.path[:])
    spec.loader.exec_module(mod)
    class Clock(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 2)
    monkeypatch.setattr(mod, "date", Clock)
    monkeypatch.setattr(mod, "_last_trading_day", lambda *a: date(2026, 10, 1))
    monkeypatch.setattr(mod, "_get_meta_moex", lambda *a, **kw: {
        "board": "TQBR", "market": "shares", "listed_from": "2026-01-01"})
    monkeypatch.setattr(nte, "fetch_issuer_identity", lambda *a: {"isin": "RU"})
    monkeypatch.setattr(sys, "argv", ["evidence", "--db", str(db), "--days", "31", "--sleep", "0"])
    return mod


def test_f1_actual_cli_mixed_feed_does_not_certify_positive_missing_bar(evidence_db, monkeypatch):
    db, con = evidence_db
    con.execute("INSERT INTO bars VALUES ('F', '2026-08-31', 1, 1, 1, 1, 1, 'tinkoff')")
    con.commit()
    before = list(map(tuple, con.execute("SELECT * FROM bars")))
    mod = cli(monkeypatch, db)
    http_pages(monkeypatch, [page([ZERO, POSITIVE])])
    assert mod.main() == 0
    assert [r[0] for r in con.execute("SELECT session_date FROM moex_no_trade_evidence")] == ["2026-09-01"]
    assert list(map(tuple, con.execute("SELECT * FROM bars"))) == before


@pytest.mark.parametrize("ts", ["2026-01-05", "2026-10-05", "2026-10-02", "2099-01-05",
                                 "2026-09-01garbage", "20260901", None, 123])
def test_f2_helper_rejects_outside_window_or_unpublished(evidence_db, ts):
    db, con = evidence_db
    assert record(db, con, [zero(ts)], from_d=date(2026, 9, 1), to_d=date(2099, 12, 31)) == 0


def test_f2_helper_respects_requested_september_window(evidence_db):
    db, con = evidence_db
    assert record(db, con, [zero("2026-01-05"), zero("2026-09-01"), zero("2026-10-01")],
                  from_d=date(2026, 9, 1), to_d=date(2026, 9, 30)) == 1
    assert [r[0] for r in con.execute("SELECT session_date FROM moex_no_trade_evidence")] == ["2026-09-01"]


@pytest.mark.parametrize("index,value,base", [
    (7, -1, POSITIVE), (8, False, ZERO), (9, False, ZERO),
    (8, float("nan"), POSITIVE), (9, float("inf"), POSITIVE),
    (3, "bad", POSITIVE), (4, False, POSITIVE), (5, float("nan"), POSITIVE),
    (6, float("inf"), POSITIVE), (8, -1, POSITIVE), (9, -1, POSITIVE),
])
def test_f3_invalid_numeric_row_poisons_but_preserves_good(monkeypatch, index, value, base):
    bad = base.copy()
    bad[index] = value
    http_pages(monkeypatch, [page([POSITIVE, bad])])
    rows, outcome = backfill._fetch_year_moex_outcome("shares", "TQBR", "GAZP", 2026)
    assert outcome == "malformed"
    assert len(rows) == 1


@pytest.mark.parametrize("bad", [None, [], {"history": None}, {"history": []},
    {"history": {"columns": "bad", "data": []}},
    {"history": {"columns": [None], "data": []}},
    {"history": {"columns": COLUMNS, "data": None}},
    {"history": {"columns": COLUMNS, "data": {}}},
    {"history": {"columns": COLUMNS}},
    {"history": {"columns": COLUMNS, "data": []}, "history.cursor": None},
    {"history": {"columns": COLUMNS, "data": []}, "history.cursor": []},
    {"history": {"columns": COLUMNS, "data": []}, "history.cursor": {"data": "bad"}},
])
def test_f3_malformed_second_page_preserves_first_bar(evidence_db, monkeypatch, bad):
    http_pages(monkeypatch, [page([POSITIVE], (0, 2, 1)), bad])
    rows, outcome = backfill._fetch_year_moex_outcome("shares", "TQBR", "GAZP", 2026)
    assert outcome == "malformed"
    assert len(rows) == 1
    assert rows[0]["close"] == 2


@pytest.mark.parametrize("edit,expected,kept", [
    ("none_volume", "malformed", 0), ("string_volume", "malformed", 0),
    ("whole_float_volume", "complete", 1), ("status", "malformed", 1),
    ("cursor_shape", "malformed", 1), ("cursor_row_type", "malformed", 1),
    ("cursor_multiple", "malformed", 1), ("cursor_bad_string", "malformed", 1),
    ("cursor_size_zero", "malformed", 1), ("cursor_total_zero", "malformed", 1),
    ("cursor_offset", "malformed", 1), ("cursor_columns", "malformed", 1),
    ("no_cursor_short", "malformed", 1), ("no_cursor_empty", "complete", 0),
    ("cursor_empty", "malformed", 1), ("columns_duplicate", "malformed", 0),
    ("rows_dict", "malformed", 0), ("positive_float_prices", "complete", 1),
])
def test_parser_contract_guards(monkeypatch, edit, expected, kept):
    payload = page([POSITIVE.copy()], (0, 1, 1))
    status = 200
    if edit == "none_volume": payload["history"]["data"][0][7] = None
    elif edit == "string_volume": payload["history"]["data"][0][7] = "10"
    elif edit == "whole_float_volume": payload["history"]["data"][0][7] = 10.0
    elif edit == "status": status = 500
    elif edit == "cursor_shape": payload["history.cursor"]["data"] = [[0, 1]]
    elif edit == "cursor_row_type": payload["history.cursor"]["data"] = ["abc"]
    elif edit == "cursor_multiple": payload["history.cursor"]["data"] *= 2
    elif edit == "cursor_bad_string": payload["history.cursor"]["data"] = [[0, 1, "bad"]]
    elif edit == "cursor_size_zero": payload["history.cursor"]["data"] = [[0, 1, 0]]
    elif edit == "cursor_total_zero": payload["history.cursor"]["data"] = [[0, 0, 1]]
    elif edit == "cursor_offset": payload["history.cursor"]["data"] = [[2, 3, 1]]
    elif edit == "cursor_columns": payload["history.cursor"]["columns"] = "bad"
    elif edit == "cursor_empty": payload["history.cursor"]["data"] = []
    elif edit == "columns_duplicate": payload["history"]["columns"] = COLUMNS + ["VALUE"]
    elif edit == "rows_dict": payload["history"]["data"] = [dict.fromkeys(COLUMNS)]
    elif edit == "positive_float_prices": payload["history"]["data"][0][3:7] = [1.0, 2.0, 1.0, 2.0]
    else:
        del payload["history.cursor"]
        if edit == "no_cursor_empty": payload["history"]["data"] = []
    monkeypatch.setattr(backfill.requests, "get", lambda *a, **kw: SimpleNamespace(
        status_code=status, json=lambda: payload))
    rows, outcome = backfill._fetch_year_moex_outcome("shares", "TQBR", "GAZP", 2026)
    assert (outcome, len(rows)) == (expected, kept)


def test_json_decode_failure_preserves_partial_bar(monkeypatch):
    first = page([POSITIVE], (0, 2, 1))
    def fail():
        raise ValueError("malformed JSON")
    responses = iter([SimpleNamespace(status_code=200, json=lambda: first),
                      SimpleNamespace(status_code=200, json=fail)])
    monkeypatch.setattr(backfill.requests, "get", lambda *a, **kw: next(responses))
    rows, outcome = backfill._fetch_year_moex_outcome("shares", "TQBR", "GAZP", 2026)
    assert (outcome, len(rows)) == ("malformed", 1)


def test_cursor_total_changes_preserves_bars(monkeypatch):
    http_pages(monkeypatch, [page([POSITIVE], (0, 3, 1)), page([POSITIVE], (1, 4, 1))])
    rows, outcome = backfill._fetch_year_moex_outcome("shares", "TQBR", "GAZP", 2026)
    assert (outcome, len(rows)) == ("malformed", 2)


def test_unbounded_no_cursor_feed_stops_at_page_cap(monkeypatch):
    payload = {"history": {"columns": COLUMNS, "data": [POSITIVE] * 500}}
    http_pages(monkeypatch, [payload] * 20)
    rows, outcome = backfill._fetch_year_moex_outcome("shares", "TQBR", "GAZP", 2026)
    assert (outcome, len(rows)) == ("partial", 10000)


@pytest.mark.parametrize("from_d,to_d", [(None, date(2026, 9, 30)),
    (date(2026, 9, 1), "2026-09-30"), (date(2026, 10, 1), date(2026, 9, 1))])
def test_window_is_mandatory_and_validated(evidence_db, from_d, to_d):
    db, con = evidence_db
    with pytest.raises(ValueError, match="ordered date window"):
        record(db, con, [zero()], from_d=from_d, to_d=to_d)
    assert con.execute("SELECT COUNT(*) FROM moex_no_trade_evidence").fetchone()[0] == 0


def test_helper_empty_wrong_board_and_sql_read_failure(evidence_db):
    db, con = evidence_db
    assert record(db, con, []) == 0
    assert record(db, con, [zero() | {"_boardid": "TQCB"}]) == 0
    con.execute("DROP TABLE instruments")
    assert record(db, con, [zero()]) == 0
    assert con.execute("SELECT COUNT(*) FROM moex_no_trade_evidence").fetchone()[0] == 0
    assert con.execute("SELECT 1").fetchone()[0] == 1  # borrowed connection stays open


def test_business_date_rejects_invalid_calendar_and_holiday(evidence_db):
    _, con = evidence_db
    assert not nte._is_business_date_for_evidence(con, "2026-09-31", today=date(2026, 10, 2))
    con.execute("INSERT INTO moex_holidays VALUES ('2026-09-01', 'holiday')")
    assert not nte._is_business_date_for_evidence(con, "2026-09-01", today=date(2026, 10, 2))


def test_f2_actual_cli_filters_year_rows_to_chosen_window(evidence_db, monkeypatch, caplog):
    db, con = evidence_db
    con.execute("INSERT INTO bars VALUES ('F', '2026-08-31', 1, 1, 1, 1, 1, 'tinkoff')")
    con.commit()
    mod = cli(monkeypatch, db)
    jan = ZERO.copy(); jan[0] = "2026-01-05"
    oct = ZERO.copy(); oct[0] = "2026-10-05"
    current = ZERO.copy(); current[0] = "2026-10-02"
    future = ZERO.copy(); future[0] = "2099-01-05"
    http_pages(monkeypatch, [page([jan, ZERO, oct, current, future], (0, 5, 5))])
    with caplog.at_level("INFO", logger="algotrader.ingestion"):
        assert mod.main() == 0
    assert [r[0] for r in con.execute("SELECT session_date FROM moex_no_trade_evidence")] == ["2026-09-01"]
    assert sum("reason=out_of_window" in r.message for r in caplog.records) == 1
    assert [r[0] for r in con.execute("SELECT ts FROM bars")] == ["2026-08-31"]
