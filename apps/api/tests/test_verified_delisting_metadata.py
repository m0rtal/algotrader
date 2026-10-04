import copy
import importlib.util
import sqlite3
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from algotrader_api.db import sqlite as sqlitedb
from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.ingestion import backfill, no_trade_evidence
from algotrader_api.ml import features

FIGI = "VERIFIED-F"
ISIN = "RU0007661625"

class FrozenDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 2)


def metadata():
    return {
        "description": {
            "columns": ["name", "title", "value", "type", "sort_order", "is_hidden"],
            "data": [["SECID", "Code", "GAZP", "string", 1, 0],
                     ["ISIN", "ISIN", ISIN, "string", 2, 0]],
        },
        "boards": {
            "columns": ["secid", "boardid", "title", "board_group_id",
                        "market_id", "market", "engine_id", "engine",
                        "is_traded", "decimals", "history_from", "history_till",
                        "listed_from", "listed_till"],
            "data": [["GAZP", "TQBR", "Shares", 57, 1, "shares", 1,
                      "stock", 0, 2, "2026-08-31", "2026-09-10",
                      "2026-08-31", "2026-09-10"]],
        },
    }


def snapshot(db):
    con = sqlite3.connect(str(db))
    try:
        return {
            table: con.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in ("instruments", "bars", "moex_no_trade_evidence")
        }
    finally:
        con.close()


def coverage(db):
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return features.check_coverage(con, [FIGI])
    finally:
        con.close()


def exercise(tmp_path, monkeypatch, payload, *, status=200,
             bar="2026-09-10", prior=None, history="complete", dry=False,
             local_isin=ISIN, customize=None, expect_delisting=True):
    db = tmp_path / "state.db"
    sqlitedb.close_all()
    sqlitedb.run_migrations(str(db), MIGRATIONS_DIR)
    sqlitedb.close_all()
    con = sqlite3.connect(str(db))
    try:
        con.execute(
            "INSERT INTO instruments "
            "(figi,ticker,class,name,currency,lot_size,isin,source_updated_at,"
            "expected_bars,listed_till) VALUES (?, 'GAZP','share','Gazp','RUB',"
            "1,?,'2026-08-31',1,?)", (FIGI, local_isin, prior))
        con.execute(
            "INSERT INTO bars(figi,ts,open,high,low,close,volume,source) "
            "VALUES (?,?,1,1,1,1,1,'tinkoff')", (FIGI, bar))
        con.commit()
    finally:
        con.close()
    path = Path(__file__).resolve().parents[1] / "scripts/backfill_no_trade_evidence.py"
    spec = importlib.util.spec_from_file_location("verified_delisting_cli", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    for module in (cli, backfill, no_trade_evidence, features):
        monkeypatch.setattr(module, "date", FrozenDate)
    events = []
    def traced_connect(*args, **kw):
        raw = sqlite3.connect(*args, **kw)
        def trace(statement):
            if statement.lstrip().upper().startswith("UPDATE INSTRUMENTS SET LISTED_TILL"):
                events.append("listed-till-update")
        raw.set_trace_callback(trace)
        return raw
    monkeypatch.setattr(cli, "sqlite3", SimpleNamespace(
        Row=sqlite3.Row, connect=traced_connect))

    class Response:
        def __init__(self, body, code=200):
            self.body, self.status_code = body, code
        def json(self):
            if isinstance(self.body, Exception):
                raise self.body
            return copy.deepcopy(self.body)

    def get(url, params=None, timeout=None):
        # Probe the actual flock without spawning a child or waiting.
        import fcntl
        from algotrader_api.ingestion.writer_lock import writer_lock_path
        with open(writer_lock_path(db), "a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        if "/history/" not in url:
            events.append("metadata")
            if isinstance(payload, ConnectionError):
                raise payload
            return Response(payload, status)
        # Before first history: inactive metadata is committed; active routing is not delisting.
        assert status == 200
        identity = {r[0]: r[2] for r in payload["description"]["data"]}
        assert identity["SECID"] == "GAZP" and identity["ISIN"] == ISIN
        changed = snapshot(db)["instruments"][0] != before["instruments"][0]
        assert changed is expect_delisting
        assert ("listed-till-update" in events) is expect_delisting
        events.append("history")
        body = {
            "history": {
                "columns": ["TRADEDATE", "SECID", "BOARDID", "OPEN", "HIGH",
                            "LOW", "CLOSE", "VOLUME", "NUMTRADES", "VALUE"],
                "data": [["2026-09-01", "GAZP", "TQBR", None, None,
                          None, None, 0, 0, 0]],
            },
            "history.cursor": {"columns": ["INDEX", "TOTAL", "PAGESIZE"],
                               "data": [[0, 1, 1]]},
        }
        if history == "partial":
            body["history.cursor"]["data"] = [[0, 2, 2]]
        if history == "error":
            raise ConnectionError("offline history failure")
        if history == "wrong_secid":
            body["history"]["data"][0][1] = "SBER"
        if history == "wrong_board":
            body["history"]["data"][0][2] = "TQCB"
        if history == "malformed":
            body = None
        return Response(body, 500 if history == "http500" else 200)

    session = SimpleNamespace(get=get)
    monkeypatch.setattr(backfill, "_get_moex_session", lambda: session)
    monkeypatch.setattr(cli, "_moex_session", lambda: session)
    monkeypatch.setattr(backfill, "requests", SimpleNamespace(get=get))
    monkeypatch.setattr(sys, "argv", ["cli", "--db", str(db), "--days", "60",
                                      "--limit", "1", "--sleep", "0"]
                        + (["--dry-run"] if dry else []))
    if customize is not None:
        customize(cli, db)
    before, gate_before = snapshot(db), coverage(db)
    assert gate_before and gate_before[0]["reason"] == "stale"
    try:
        rc = cli.main()
        return rc, before, snapshot(db), gate_before, coverage(db), events
    finally:
        sqlitedb.close_all()


@pytest.mark.parametrize("fault", ["http500", "wrong_secid", "wrong_isin",
    "missing_secid", "empty_isin", "missing_boards", "short_board",
    "missing_board_secid", "empty_board_secid", "wrong_board_secid",
    "duplicate_column", "bad_date", "suffix_date", "null_date", "active_other",
    "json_error", "network_error"])
@pytest.mark.parametrize("prior", [None, "2026-09-30"])
def test_invalid_metadata_empty_window_preserves_state(tmp_path, monkeypatch, fault, prior):
    body, status = metadata(), 200
    if fault == "http500": status = 500
    if fault == "wrong_secid": body["description"]["data"][0][2] = "SBER"
    if fault == "wrong_isin": body["description"]["data"][1][2] = "US00206R1023"
    if fault == "missing_secid": body["description"]["data"].pop(0)
    if fault == "empty_isin": body["description"]["data"][1][2] = ""
    if fault == "missing_boards": body.pop("boards")
    if fault == "short_board": body["boards"]["data"][0].pop()
    if fault == "missing_board_secid":
        body["boards"]["columns"].pop(0)
        for row in body["boards"]["data"]: row.pop(0)
    if fault == "empty_board_secid": body["boards"]["data"][0][0] = ""
    if fault == "wrong_board_secid": body["boards"]["data"][0][0] = "SBER"
    if fault == "duplicate_column": body["boards"]["columns"][0] = "boardid"
    if fault == "bad_date": body["boards"]["data"][0][13] = "2026-02-30"
    if fault == "suffix_date": body["boards"]["data"][0][13] += "junk"
    if fault == "null_date": body["boards"]["data"][0][13] = None
    if fault == "active_other":
        other = body["boards"]["data"][0].copy()
        other[1], other[8] = "OTHER", 1
        body["boards"]["data"].append(other)
    if fault == "json_error": body = ValueError("offline invalid JSON")
    if fault == "network_error": body = ConnectionError("offline network failure")
    rc, before, after, gate_before, gate_after, events = exercise(
        tmp_path, monkeypatch, body, status=status, prior=prior)
    assert rc == 0
    assert gate_after == gate_before
    assert after == before
    assert "history" not in events


def test_valid_empty_window_is_idempotent(tmp_path, monkeypatch):
    rc, before, after, _, gate, events = exercise(tmp_path, monkeypatch, metadata())
    assert rc == 0 and "history" not in events and gate == []
    con = sqlite3.connect(str(tmp_path / "state.db"))
    try:
        assert con.execute("SELECT listed_till FROM instruments").fetchone()[0] == "2026-09-10"
    finally:
        con.close()
    assert after["bars"] == before["bars"]
    assert after["moex_no_trade_evidence"] == before["moex_no_trade_evidence"]
    # Re-load same CLI rather than re-seeding the DB.
    spec = importlib.util.spec_from_file_location("verified_delisting_cli_repeat",
        Path(__file__).resolve().parents[1] / "scripts/backfill_no_trade_evidence.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "date", FrozenDate)
    monkeypatch.setattr(cli, "_moex_session", backfill._get_moex_session)
    assert cli.main() == 0
    assert snapshot(tmp_path / "state.db") == after


@pytest.mark.parametrize("history", ["partial", "http500", "error", "malformed",
                                     "wrong_secid", "wrong_board"])
def test_valid_metadata_survives_degraded_history(tmp_path, monkeypatch, history):
    rc, before, after, _, _, events = exercise(tmp_path, monkeypatch, metadata(),
        bar="2026-08-31", history=history)
    assert rc == 0 and "history" in events
    if history == "partial":
        assert events.count("history") == 1
    con = sqlite3.connect(str(tmp_path / "state.db"))
    try:
        assert con.execute("SELECT listed_till FROM instruments").fetchone()[0] == "2026-09-10"
        assert con.execute("SELECT expected_bars FROM instruments").fetchone()[0] == 1
    finally:
        con.close()
    assert after["bars"] == before["bars"]
    assert after["moex_no_trade_evidence"] == before["moex_no_trade_evidence"]


@pytest.mark.parametrize("control", ["foreign", "future", "dry"])
def test_retained_guards(tmp_path, monkeypatch, control):
    body = metadata()
    if control == "future": body["boards"]["data"][0][13] = "2026-10-01"
    rc, before, after, gate_before, gate_after, _ = exercise(
        tmp_path, monkeypatch, body,
        bar="2026-09-11" if control == "foreign" else "2026-09-10",
        dry=control == "dry")
    assert rc == 0 and before == after and gate_before == gate_after

@pytest.mark.parametrize("fault", ["missing", "empty", "mismatch", "other_row"])
@pytest.mark.parametrize("bar", ["2026-09-10", "2026-08-31"])
@pytest.mark.parametrize("prior", [None, "2026-09-30"])
def test_board_identity_rejection_precedes_window_and_history(
        tmp_path, monkeypatch, fault, bar, prior):
    body = metadata()  # Description SECID remains GAZP in every case.
    if fault == "missing":
        body["boards"]["columns"].pop(0)
        for row in body["boards"]["data"]: row.pop(0)
    elif fault == "other_row":
        other = body["boards"]["data"][0].copy()
        other[0], other[1] = "SBER", "OTHER"
        body["boards"]["data"].append(other)
    else:
        body["boards"]["data"][0][0] = "" if fault == "empty" else "SBER"
    rc, before, after, gate_before, gate_after, events = exercise(
        tmp_path, monkeypatch, body, bar=bar, prior=prior)
    assert rc == 0 and before == after and gate_before == gate_after
    assert "history" not in events


@pytest.mark.parametrize("activity", [0, False, "0"], ids=["int0", "false", "str0"])
def test_exact_inactive_activity_accepts_empty_window(tmp_path, monkeypatch, activity):
    body = metadata()
    body["boards"]["data"][0][8] = activity
    rc, before, after, _, gate_after, events = exercise(tmp_path, monkeypatch, body)
    assert rc == 0 and gate_after == [] and "history" not in events
    con = sqlite3.connect(str(tmp_path / "state.db"))
    try:
        assert con.execute("SELECT listed_till FROM instruments").fetchone()[0] == "2026-09-10"
    finally:
        con.close()
    assert after["bars"] == before["bars"]
    assert after["moex_no_trade_evidence"] == before["moex_no_trade_evidence"]


@pytest.mark.parametrize("activity", [1, True, "1", 0.0, 1.0, "", None,
                                       " 0", "0 ", "false", "true", "unknown", 2])
@pytest.mark.parametrize("layout", ["nonprimary_only", "inactive_primary_plus_other"])
@pytest.mark.parametrize("bar", ["2026-09-10", "2026-08-31"])
def test_inactive_candidate_activity_rejection_preserves_state(
        tmp_path, monkeypatch, activity, layout, bar):
    body = metadata()
    if layout == "inactive_primary_plus_other":
        body["boards"]["data"].append(body["boards"]["data"][0].copy())
    # No active primary board: the actual _get_meta_moex must return None.
    body["boards"]["data"][-1][1] = "OTHER"
    body["boards"]["data"][-1][8] = activity
    rc, before, after, gate_before, gate_after, events = exercise(
        tmp_path, monkeypatch, body, bar=bar, prior="2026-09-30",
        expect_delisting=False)
    assert rc == 0 and before == after and gate_before == gate_after
    assert "history" not in events and "listed-till-update" not in events


@pytest.mark.parametrize("activity", [1, True, 1.0], ids=["int1", "true", "float1"])
@pytest.mark.parametrize("bar", ["2026-09-10", "2026-08-31"])
@pytest.mark.parametrize("prior", [None, "2026-09-30"])
def test_active_primary_preserves_delisting_but_allows_history(
        tmp_path, monkeypatch, activity, bar, prior):
    body = metadata()
    body["boards"]["data"][0][8] = activity
    rc, before, after, gate_before, gate_after, events = exercise(
        tmp_path, monkeypatch, body, bar=bar, prior=prior,
        history="partial", expect_delisting=False)
    assert rc == 0 and before == after and gate_before == gate_after
    assert events.count("history") == 1
    assert "listed-till-update" not in events


def test_partial_cursor_uses_real_year_parser(monkeypatch):
    calls = []
    body = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID", "OPEN", "HIGH",
                        "LOW", "CLOSE", "VOLUME", "NUMTRADES", "VALUE"],
            "data": [["2026-09-01", "GAZP", "TQBR", None, None,
                      None, None, 0, 0, 0]],
        },
        "history.cursor": {"columns": ["INDEX", "TOTAL", "PAGESIZE"],
                           "data": [[0, 2, 2]]},
    }
    def get(url, params=None, timeout=None):
        calls.append(url)
        assert len(calls) == 1
        return SimpleNamespace(status_code=200, json=lambda: copy.deepcopy(body))
    monkeypatch.setattr(backfill, "requests", SimpleNamespace(get=get))
    rows, outcome = backfill._fetch_year_moex_outcome(
        "shares", "TQBR", "GAZP", 2026, last_trading_day=date(2026, 10, 1))
    assert outcome == "partial" and len(rows) == 1 and len(calls) == 1
    assert rows[0]["_secid"] == "GAZP" and rows[0]["_boardid"] == "TQBR"

@pytest.mark.parametrize("local_isin", [None, ""])
@pytest.mark.parametrize("bar", ["2026-09-10", "2026-08-31"])
@pytest.mark.parametrize("prior", [None, "2026-09-30"])
def test_missing_local_identity_rejects_delisting(tmp_path, monkeypatch, local_isin, bar, prior):
    rc, before, after, gate_before, gate_after, events = exercise(
        tmp_path, monkeypatch, metadata(), local_isin=local_isin, bar=bar, prior=prior)
    assert rc == 0 and before == after and gate_before == gate_after
    assert "history" not in events


@pytest.mark.parametrize("status,secid", [(500, "GAZP"), (200, "SBER")])
def test_rejection_precedes_first_history(tmp_path, monkeypatch, status, secid):
    body = metadata()
    body["description"]["data"][0][2] = secid
    rc, before, after, _, _, events = exercise(
        tmp_path, monkeypatch, body, status=status, bar="2026-08-31")
    assert rc == 0 and after == before and "history" not in events


def test_rejection_preserves_existing_evidence(tmp_path, monkeypatch):
    def plant(cli, db):
        con = sqlite3.connect(str(db))
        try:
            con.execute("INSERT INTO moex_no_trade_evidence "
                "(figi,session_date,board,isin,observed_at,expires_at) "
                "VALUES (?,'2026-09-09','TQBR',?,'2026-09-30','2027-09-30')",
                (FIGI, ISIN))
            con.commit()
        finally:
            con.close()
    rc, before, after, _, _, _ = exercise(tmp_path, monkeypatch, metadata(),
        status=500, prior="2026-09-30", customize=plant)
    assert rc == 0 and before == after
    assert len(after["moex_no_trade_evidence"]) == 1


def test_commit_busy_rolls_back_while_locked_and_closes(tmp_path, monkeypatch, capsys):
    from contextlib import contextmanager
    marks = []
    held = False
    def customize(cli, db):
        real_lock = cli.writer_lock
        @contextmanager
        def tracked_lock(db_path, **kw):
            nonlocal held
            with real_lock(db_path, **kw):
                held = True
                try:
                    yield
                finally:
                    marks.append("unlock")
                    held = False
        monkeypatch.setattr(cli, "writer_lock", tracked_lock)
        class Connection:
            def __init__(self, raw): self.raw = raw
            @property
            def row_factory(self): return self.raw.row_factory
            @row_factory.setter
            def row_factory(self, value): self.raw.row_factory = value
            def execute(self, *args): return self.raw.execute(*args)
            def commit(self):
                assert held and self.raw.in_transaction
                exc = sqlite3.OperationalError("database is locked")
                exc.sqlite_errorcode = sqlite3.SQLITE_BUSY
                raise exc
            def rollback(self):
                assert held
                self.raw.rollback()
                assert not self.raw.in_transaction
                marks.append("rollback")
            def close(self):
                self.raw.close()
                marks.append("close")
        monkeypatch.setattr(cli, "sqlite3", SimpleNamespace(
            Row=sqlite3.Row,
            connect=lambda *a, **kw: Connection(sqlite3.connect(*a, **kw))))
    rc, before, after, _, _, _ = exercise(tmp_path, monkeypatch, metadata(),
        prior="2026-09-30", customize=customize)
    assert rc == 75 and after == before
    assert marks == ["rollback", "unlock", "close"]
    assert sum(line.startswith("DEFER writer-lock-busy")
               for line in capsys.readouterr().out.splitlines()) == 1


@pytest.mark.parametrize("block", ["description", "boards"])
@pytest.mark.parametrize("fault", ["null", "list", "string", "columns_null",
    "columns_string", "missing_required", "duplicate_column", "empty_rows",
    "rows_string", "row_null", "long_row"])
@pytest.mark.parametrize("prior", [None, "2026-09-30"])
def test_malformed_blocks_preserve_state(tmp_path, monkeypatch, block, fault, prior):
    body = metadata()
    if fault in ("null", "list", "string"):
        body[block] = {"null": None, "list": [], "string": "bad"}[fault]
    elif fault == "columns_null": body[block]["columns"] = None
    elif fault == "columns_string": body[block]["columns"] = "name"
    elif fault == "missing_required":
        body[block]["columns"].pop(0)
        for row in body[block]["data"]: row.pop(0)
    elif fault == "duplicate_column":
        body[block]["columns"][1] = body[block]["columns"][0]
    elif fault == "empty_rows": body[block]["data"] = []
    elif fault == "rows_string": body[block]["data"] = "bad"
    elif fault == "row_null": body[block]["data"][0] = None
    elif fault == "long_row": body[block]["data"][0].append("extra")
    rc, before, after, gate_before, gate_after, events = exercise(
        tmp_path, monkeypatch, body, prior=prior)
    assert rc == 0 and before == after and gate_before == gate_after
    assert "history" not in events and "listed-till-update" not in events


@pytest.mark.parametrize("fault", ["duplicate_secid", "duplicate_isin", "missing_isin",
    "whitespace_isin", "empty_board", "second_bad_date", "short_description"])
def test_remaining_identity_and_board_shapes(tmp_path, monkeypatch, fault):
    body = metadata()
    if fault.startswith("duplicate_"):
        body["description"]["data"].append(body["description"]["data"][fault == "duplicate_isin"].copy())
    elif fault == "missing_isin": body["description"]["data"].pop(1)
    elif fault == "whitespace_isin": body["description"]["data"][1][2] = " "
    elif fault == "empty_board": body["boards"]["data"][0][1] = " "
    elif fault == "short_description": body["description"]["data"][0].pop()
    elif fault == "second_bad_date":
        body["boards"]["data"].append(body["boards"]["data"][0].copy())
        body["boards"]["data"][-1][1] = "OTHER"
        body["boards"]["data"][-1][13] = "2026-02-30"
    rc, before, after, gate_before, gate_after, events = exercise(tmp_path, monkeypatch, body)
    assert rc == 0 and before == after and gate_before == gate_after
    assert "history" not in events


def test_latest_of_two_inactive_boards(tmp_path, monkeypatch):
    body = metadata()
    other = body["boards"]["data"][0].copy()
    other[1], other[13] = "OTHER", "2026-09-09"
    body["boards"]["data"].append(other)
    rc, before, after, _, gate_after, events = exercise(tmp_path, monkeypatch, body)
    assert rc == 0 and gate_after == [] and "history" not in events
    assert after["bars"] == before["bars"]
    assert after["moex_no_trade_evidence"] == before["moex_no_trade_evidence"]


@pytest.mark.parametrize("prior", [None, "2026-09-30"])
@pytest.mark.parametrize("bar", ["2026-09-10", "2026-08-31"])
def test_wrong_isin_rejection_precedes_first_history(tmp_path, monkeypatch, prior, bar):
    body = metadata()
    body["description"]["data"][1][2] = "US00206R1023"
    rc, before, after, gate_before, gate_after, events = exercise(
        tmp_path, monkeypatch, body, prior=prior, bar=bar)
    assert rc == 0 and before == after and gate_before == gate_after
    assert "history" not in events


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_baseexception_preserved_and_owned_connection_closed(tmp_path, monkeypatch, rollback_fails):
    from contextlib import contextmanager
    marks = []
    held = False
    original = BaseException("original commit failure")
    def customize(cli, db):
        real_lock = cli.writer_lock
        @contextmanager
        def lock(db_path, **kw):
            nonlocal held
            with real_lock(db_path, **kw):
                held = True
                try: yield
                finally:
                    marks.append("unlock")
                    held = False
        monkeypatch.setattr(cli, "writer_lock", lock)
        class Connection:
            def __init__(self, raw): self.raw = raw
            @property
            def row_factory(self): return self.raw.row_factory
            @row_factory.setter
            def row_factory(self, value): self.raw.row_factory = value
            def execute(self, *a): return self.raw.execute(*a)
            def commit(self):
                assert held and self.raw.in_transaction
                raise original
            def rollback(self):
                assert held
                marks.append("rollback")
                if rollback_fails: raise RuntimeError("rollback failure")
                self.raw.rollback()
            def close(self):
                self.raw.close()
                marks.append("close")
        monkeypatch.setattr(cli, "sqlite3", SimpleNamespace(Row=sqlite3.Row,
            connect=lambda *a, **kw: Connection(sqlite3.connect(*a, **kw))))
    with pytest.raises(BaseException) as caught:
        exercise(tmp_path, monkeypatch, metadata(), prior="2026-09-30", customize=customize)
    assert caught.value is original
    assert marks == ["rollback", "unlock", "close"]
    assert snapshot(tmp_path / "state.db")["instruments"][0][-1] == "2026-09-30"


def test_inactive_verified_identity_reused_and_evidence_busy_retains_date(tmp_path, monkeypatch, capsys):
    from contextlib import contextmanager
    from algotrader_api.ingestion import writer_lock as wl
    real_lock = wl.writer_lock
    phases = []
    def customize(cli, db):
        @contextmanager
        def lock(db_path, **kw):
            phases.append(kw["phase"])
            if kw["phase"] == "evidence":
                raise wl.WriterLockBusy(role=kw["role"], phase=kw["phase"],
                    database_path=str(db_path), lock_path=str(db_path)+".writer.lock",
                    timeout_seconds=0, reason="test-forced-busy")
            with real_lock(db_path, **kw): yield
        monkeypatch.setattr(wl, "writer_lock", lock)
        monkeypatch.setattr(cli, "writer_lock", lock)
        def forbidden(ticker):
            pytest.fail("inactive identity must not be re-certified by active issuer probe")
        monkeypatch.setattr(no_trade_evidence, "fetch_issuer_identity", forbidden)
    rc, before, after, _, _, events = exercise(tmp_path, monkeypatch, metadata(),
        bar="2026-08-31", customize=customize)
    assert rc == 75 and phases == ["listed-till", "evidence"]
    assert after["instruments"][0][-1] == "2026-09-10"
    assert after["bars"] == before["bars"]
    assert after["moex_no_trade_evidence"] == before["moex_no_trade_evidence"]
    assert events.count("history") == 1
    assert sum(line.startswith("DEFER writer-lock-busy")
               for line in capsys.readouterr().out.splitlines()) == 1


def test_dry_run_closes_owned_connection_without_any_writer_lock(tmp_path, monkeypatch):
    marks = []
    def customize(cli, db):
        def forbidden(*a, **kw): pytest.fail("dry-run acquired writer lock")
        monkeypatch.setattr(cli, "writer_lock", forbidden)
        from algotrader_api.ingestion import writer_lock as wl
        monkeypatch.setattr(wl, "writer_lock", forbidden)
        class Connection:
            def __init__(self, raw): self.raw = raw
            @property
            def row_factory(self): return self.raw.row_factory
            @row_factory.setter
            def row_factory(self, value): self.raw.row_factory = value
            def execute(self, *a): return self.raw.execute(*a)
            def close(self):
                self.raw.close()
                marks.append("close")
        monkeypatch.setattr(cli, "sqlite3", SimpleNamespace(Row=sqlite3.Row,
            connect=lambda *a, **kw: Connection(sqlite3.connect(*a, **kw))))
    rc, before, after, gate_before, gate_after, events = exercise(
        tmp_path, monkeypatch, metadata(), dry=True, customize=customize)
    assert rc == 0 and before == after and gate_before == gate_after
    assert marks == ["close"] and "listed-till-update" not in events


@pytest.mark.parametrize("body", [None, [], "bad"])
def test_top_level_metadata_shape_rejected(tmp_path, monkeypatch, body):
    rc, before, after, gate_before, gate_after, events = exercise(tmp_path, monkeypatch, body)
    assert rc == 0 and before == after and gate_before == gate_after
    assert "history" not in events


@pytest.mark.parametrize("block,column", [
    ("description", "name"), ("description", "value"),
    ("boards", "secid"), ("boards", "boardid"),
    ("boards", "is_traded"), ("boards", "listed_till")])
def test_each_required_column_missing_rejects(tmp_path, monkeypatch, block, column):
    body = metadata()
    index = body[block]["columns"].index(column)
    body[block]["columns"].pop(index)
    for row in body[block]["data"]: row.pop(index)
    rc, before, after, gate_before, gate_after, events = exercise(tmp_path, monkeypatch, body)
    assert rc == 0 and before == after and gate_before == gate_after
    assert "history" not in events


def test_verified_inactive_identity_records_real_evidence_without_reprobe(tmp_path, monkeypatch):
    def customize(cli, db):
        def forbidden(ticker):
            pytest.fail("inactive identity must not be re-certified by active issuer probe")
        monkeypatch.setattr(no_trade_evidence, "fetch_issuer_identity", forbidden)
    rc, before, after, _, _, events = exercise(tmp_path, monkeypatch, metadata(),
        bar="2026-08-31", customize=customize)
    assert rc == 0 and events.count("history") == 1
    assert events.count("metadata") == 2
    assert after["bars"] == before["bars"]
    con = sqlite3.connect(str(tmp_path / "state.db"))
    try:
        row = con.execute("SELECT session_date, board, isin FROM moex_no_trade_evidence").fetchall()
        assert row == [("2026-09-01", "TQBR", ISIN)]
        assert con.execute("SELECT expected_bars,listed_till FROM instruments").fetchone() == (1, "2026-09-10")
    finally:
        con.close()
