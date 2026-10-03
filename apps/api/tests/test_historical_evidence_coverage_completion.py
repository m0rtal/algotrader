"""Behavior proofs for legacy evidence boundaries, using migrated temporary DBs."""
from datetime import date
import sqlite3
from types import SimpleNamespace

import pytest

from algotrader_api.db.migrations_runner import run_migrations
from algotrader_api.ingestion import backfill, no_trade_evidence as evidence
from algotrader_api.ingestion.writer_lock import writer_lock


COLUMNS = ["TRADEDATE", "SECID", "BOARDID", "OPEN", "HIGH", "LOW", "CLOSE",
           "VOLUME", "NUMTRADES", "VALUE"]
ZERO = ["2026-09-01", "GAZP", "TQBR", None, None, None, None, 0, 0, 0]
URL = "https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities/GAZP.json"


def page(rows, cursor=None, columns=COLUMNS):
    payload = {"history": {"columns": columns, "data": rows}}
    if cursor is not None:
        payload["history.cursor"] = {"data": [cursor]}
    return payload


def upstream(monkeypatch, exchanges):
    """Finite HTTP boundary; mismatched or extra requests fail outside fetch catches."""
    pending = list(exchanges)
    errors = []

    def get(url, *, params=None, timeout=None):
        if not pending:
            errors.append("unexpected HTTP request")
            raise AssertionError(errors[-1])
        expected_url, expected_params, payload = pending.pop(0)
        if (url, params, timeout) != (expected_url, expected_params, (5, 30)):
            errors.append((url, params, timeout))
            raise AssertionError(errors[-1])
        if isinstance(payload, Exception):
            raise payload
        return SimpleNamespace(status_code=200, json=lambda: payload)

    monkeypatch.setattr(backfill, "_get_moex_session", lambda: SimpleNamespace(get=get))
    return pending, errors


def fetch(**kw):
    return evidence.fetch_no_trade_rows(
        market="shares", board="TQBR", ticker="GAZP",
        **({"from_d": date(2026, 9, 1), "to_d": date(2026, 9, 30)} | kw),
    )


def params(year=2026, start=0, till="2026-09-30"):
    return {"from": f"{year}-01-01", "till": till, "start": start}


@pytest.mark.parametrize("payload,want", [
    ({}, None),
    ({"boards": {"data": [[0, "WRONG", 0, 0, 0, 0, 0, 0, 1],
                           [0, "TQBR", 0, 0, 0, 0, 0, 0, 0], []]}}, None),
    ({"boards": {"data": [[0, "TQBR", 0, 0, 0, 0, 0, 0, 1]]}},
     {"board": "TQBR", "isin": ""}),
    ({"boards": {"data": [[0, "TQBR", 0, 0, 0, 0, 0, 0, 1]]},
      "description": {"data": [[], ["ISIN", "ISIN", None],
                                  ["NAME", "Name", "GAZP"], ["ISIN", "ISIN", "RU"]]}},
     {"board": "TQBR", "isin": "RU"}),
    (ConnectionError("offline"), None),
])
def test_identity_requires_traded_supported_board_and_string_isin(monkeypatch, payload, want):
    pending, errors = upstream(monkeypatch, [
        ("https://iss.moex.com/iss/securities/GAZP%20X.json", None, payload),
    ])
    assert evidence.fetch_issuer_identity("GAZP X") == want
    assert not pending and not errors


@pytest.mark.parametrize("payload", [
    ConnectionError("offline"), {}, page([], columns=["SECID"]), page([]),
    page([[*ZERO[:1], "SBER", *ZERO[2:]]]),
    page([[*ZERO[:2], "TQCB", *ZERO[3:]]]),
    *[page([[*ZERO[:i], 1, *ZERO[i + 1:]]]) for i in range(3, 10)],
    page([ZERO], [None, 1, 500]), page([ZERO], [0, None, 500]),
    page([ZERO], [0, 2, 500]), page([ZERO], ["0", 1, 500]),
    page([ZERO], [0, 1]), page([ZERO] * 500),
])
def test_legacy_fetch_rejects_unusable_or_partial_entire_batch(monkeypatch, payload):
    pending, errors = upstream(monkeypatch, [(URL, params(), payload)])
    assert fetch() == []
    assert not pending and not errors


def test_legacy_fetch_cursor_continuation_failure_discards_previous_rows(monkeypatch):
    pending, errors = upstream(monkeypatch, [
        (URL, params(), page([ZERO], [0, 1, 500])),
        (URL, params(start=1), ConnectionError("next page failed")),
    ])
    assert fetch() == []
    assert not pending and not errors


def test_legacy_fetch_short_pages_filter_window_and_cap_each_year(monkeypatch):
    pending, errors = upstream(monkeypatch, [
        (URL, params(2025, till="2026-09-02"), page([
            ["2025-12-30", *ZERO[1:]], ["2025-12-31", *ZERO[1:]],
        ])),
        (URL, params(2026, till="2026-09-02"), page([
            ZERO, ["2026-09-02T00:00:00", *ZERO[1:]], ["2026-09-04", *ZERO[1:]],
        ])),
    ])
    assert fetch(from_d=date(2025, 12, 31), to_d=date(2026, 9, 3),
                 last_trading_day=date(2026, 9, 2)) == [
        {"ts": "2025-12-31", "volume": 0, "numtrades": 0, "value": 0.0},
        {"ts": "2026-09-01", "volume": 0, "numtrades": 0, "value": 0.0},
        {"ts": "2026-09-02", "volume": 0, "numtrades": 0, "value": 0.0},
    ]
    assert not pending and not errors


def test_legacy_fetch_reversed_window_never_requests_http(monkeypatch):
    pending, errors = upstream(monkeypatch, [])
    assert fetch(from_d=date(2026, 10, 1)) == []
    assert not pending and not errors


@pytest.fixture
def migrated_db(tmp_path):
    path = tmp_path / "evidence.db"
    run_migrations(str(path))
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO instruments(figi,ticker,class,name,currency,lot_size,isin) "
                 "VALUES ('F','GAZP','share','Gazprom','RUB',1,'RU')")
    conn.commit()
    yield path, conn
    conn.close()


def record(path, conn, rows, **kw):
    return evidence.record_no_trade_evidence(
        conn, db_path=str(path), figi="F", rows=rows, board="TQBR", isin="RU",
        **({"now": date(2026, 10, 2)} | kw),
    )


def stored(conn):
    return [tuple(r) for r in conn.execute(
        "SELECT session_date,board,isin,expires_at FROM moex_no_trade_evidence ORDER BY session_date")]


def test_legacy_writer_filters_empty_invalid_and_real_bar_dates(migrated_db):
    path, conn = migrated_db
    assert record(path, conn, []) == 0
    # Empty private body is also safe inside the real public writer lock.
    with writer_lock(str(path), role="no-trade-evidence", phase="evidence"):
        assert evidence._record_no_trade_evidence_tx(
            conn, figi="F", rows=[], board="TQBR", isin="RU") == 0
    conn.execute("INSERT INTO bars(figi,ts,open,high,low,close,volume,source) VALUES ('F','2026-09-30',1,1,1,1,1,'moex')")
    conn.commit()
    assert record(path, conn, [{}, {"ts": None}, {"ts": ""}, {"ts": "2026-02-30"},
                               {"ts": "2026-09-30"}]) == 0
    assert stored(conn) == []
    assert conn.execute("SELECT close FROM bars WHERE figi='F'").fetchone()[0] == 1
    assert record(path, conn, [{"ts": "2026-09-18"}, {"ts": "2026-09-17"}]) == 2
    assert stored(conn) == [("2026-09-17", "TQBR", "RU", "2027-10-02"),
                            ("2026-09-18", "TQBR", "RU", "2026-10-09")]
    with sqlite3.connect(path) as observer:
        assert observer.execute("SELECT COUNT(*) FROM moex_no_trade_evidence").fetchone()[0] == 2
    assert evidence.load_no_trade_dates(conn, "F", today=date(2026, 10, 9)) == {"2026-09-17", "2026-09-18"}
    assert evidence.load_no_trade_dates(conn, "F", today=date(2026, 10, 10)) == {"2026-09-17"}


@pytest.mark.parametrize("operation", ["record", "reconcile"])
def test_closed_connection_keeps_original_sql_error_and_releases_lock(migrated_db, operation):
    path, conn = migrated_db
    conn.close()
    with pytest.raises(sqlite3.ProgrammingError, match="Cannot operate on a closed database"):
        if operation == "record":
            record(path, conn, [{"ts": "2026-09-01"}])
        else:
            evidence.reconcile_no_trade_evidence(conn, db_path=str(path))
    with writer_lock(str(path), role="no-trade-evidence", phase="evidence", timeout_seconds=0.1):
        with sqlite3.connect(path) as observer:
            assert observer.execute("SELECT COUNT(*) FROM moex_no_trade_evidence").fetchone()[0] == 0


def test_record_sql_failure_rolls_back_partial_batch_and_allows_retry(migrated_db):
    path, conn = migrated_db
    conn.execute("""CREATE TRIGGER reject_second BEFORE INSERT ON moex_no_trade_evidence
                    WHEN NEW.session_date='2026-09-02'
                    BEGIN SELECT RAISE(ABORT,'injected constraint'); END""")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="injected constraint"):
        record(path, conn, [{"ts": "2026-09-01"}, {"ts": "2026-09-02"}])
    assert not conn.in_transaction
    assert stored(conn) == []
    conn.execute("DROP TRIGGER reject_second")
    conn.commit()
    assert record(path, conn, [{"ts": "2026-09-01"}]) == 1
    assert stored(conn) == [("2026-09-01", "TQBR", "RU", "2027-10-02")]


def test_future_sessions_count_despite_evidence_and_reversed_window_is_empty(migrated_db):
    path, conn = migrated_db
    assert record(path, conn, [{"ts": "2026-10-05"}]) == 1
    assert evidence.expected_sessions_for_figi(
        conn, "F", date(2026, 10, 2), date(2026, 10, 5), today=date(2026, 10, 2)) == 2
    assert evidence.expected_sessions_for_figi(
        conn, "F", date(2026, 10, 5), date(2026, 10, 2), today=date(2026, 10, 2)) == 0


def test_historical_mixed_invalid_rows_emit_one_bounded_diagnostic(migrated_db, caplog):
    path, conn = migrated_db
    base = dict(ts="2026-09-01", open=None, high=None, low=None, close=None,
                volume=0, _numtrades=0, _value=0, _secid="GAZP", _boardid="TQBR")
    rows = [base, *[dict(base, ts="2026-08-31") for _ in range(100)],
            dict(base, ts=None), dict(base, ts="2026-02-30"),
            dict(base, volume=None), dict(base, _numtrades=None), dict(base, _value=None)]
    with caplog.at_level("INFO", logger="algotrader.ingestion"):
        assert evidence.record_historical_no_trade_evidence(
            conn, db_path=str(path), figi="F", ticker="GAZP", rows=rows,
            board="TQBR", isin="RU", outcome="complete", from_d=date(2026, 9, 1),
            to_d=date(2026, 9, 30), today=date(2026, 10, 2)) == 1
    diagnostics = [r.getMessage() for r in caplog.records
                   if "moex_historical_evidence_rejected" in r.getMessage()]
    assert len(diagnostics) == 1
    assert "reason=out_of_window rows=100" in diagnostics[0]
    assert stored(conn) == [("2026-09-01", "TQBR", "RU", "2027-10-02")]
    assert conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 0


def test_reconcile_real_bar_wins_without_deleting_other_sessions(migrated_db):
    path, conn = migrated_db
    assert record(path, conn, [{"ts": "2026-09-01"}, {"ts": "2026-09-02"}]) == 2
    conn.execute("INSERT INTO bars(figi,ts,open,high,low,close,volume,source) "
                 "VALUES ('F','2026-09-01',1,1,1,1,1,'moex')")
    conn.commit()
    assert evidence.reconcile_no_trade_evidence(conn, db_path=str(path)) == 1
    assert evidence.reconcile_no_trade_evidence(conn, db_path=str(path)) == 0
    assert stored(conn) == [("2026-09-02", "TQBR", "RU", "2027-10-02")]
    assert evidence.expected_sessions_for_figi(
        conn, "F", date(2026, 9, 1), date(2026, 9, 2), today=date(2026, 10, 2)) == 1
    with sqlite3.connect(path) as observer:
        assert observer.execute("SELECT session_date FROM moex_no_trade_evidence").fetchall() == [("2026-09-02",)]


@pytest.mark.parametrize("outcome", ["partial", "error", "malformed", "identity_mismatch", "complete"])
def test_historical_rejection_never_writes_unknown_or_wrong_identity(migrated_db, outcome):
    path, conn = migrated_db
    row = dict(ts="2026-09-01", open=None, high=None, low=None, close=None,
               volume=0, _numtrades=0, _value=0, _secid="SBER", _boardid="TQBR")
    weekend = dict(row, ts="2026-09-05", _secid="GAZP")
    assert evidence.record_historical_no_trade_evidence(
        conn, db_path=str(path), figi="F", ticker="GAZP", rows=[row, weekend],
        board="TQBR", isin="RU", outcome=outcome, from_d=date(2026, 9, 1),
        to_d=date(2026, 9, 30), today=date(2026, 10, 2)) == 0
    assert stored(conn) == []


def test_zero_row_extractor_does_not_forward_trades_or_missing_identity_dates():
    base = dict(ts="2026-09-01", open=None, high=None, low=None, close=None,
                volume=0, _numtrades=0, _value=0, _secid="GAZP", _boardid="TQBR")
    assert evidence._extract_zero_trade_rows([
        dict(base, volume=1), dict(base, _secid=""), dict(base, _boardid=""),
        dict(base, ts=""), base,
    ]) == [{"ts": "2026-09-01", "volume": 0, "numtrades": 0, "value": 0.0}]
