# Verified Delisting Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate delisting metadata identity, status and full board shape before any listed-till mutation, including an empty history window.

**Architecture:** Correct the existing CLI-private metadata probe and inactive-path ordering. Carry the verified upstream ISIN from that response into the existing evidence helper; preserve separate transactions and the independent verified metadata fact. No generalized metadata layer or new broker integration.

**Tech Stack:** Python >=3.11, existing requests, pytest, SQLite and project writer lock; no new dependencies.

**Spec:** `openspec/changes/verify-delisting-metadata/specs/data-quality/spec.md` and `design.md`; canonical `openspec/specs/data-quality/spec.md`, `openspec/specs/writer-coordination/spec.md`; retained historical contract `openspec/changes/persist-historical-moex-evidence/specs/data-quality/spec.md`.

## Global Constraints

- Base inspected: `ad58b28a96f15b85984a228dcdb26f34ccf515fe`; worktree `/home/hermes/worktrees/algotrader-verified-delisting`, branch `fix/verified-delisting-metadata`. Reconcile a later authorized base explicitly; preserve every existing canonical clause.
- This document commit performs no implementation, source/test edits, production reads/writes, network, push, merge or deployment.
- RED must fail the intended metadata-preservation assertion, not import, fixture, network or clock setup. Then GREEN, related regressions and full backend coverage >=95%; do not alter coverage omissions/exclusions or add xfail.
- Keep last completed MOEX business session, 0.95 completeness threshold, positive cached denominator and required universe unchanged. No denominator refresh or production cleanup.
- Rejected metadata preserves existing listed-till (NULL or non-NULL), bars, expected-bars and evidence exactly. Validated inactive metadata does not require subsequent history success or synthetic zeros.
- Keep foreign bars after date guard, latest-board selection and strict earlier-than-last-session comparison. No active board may certify delisting.
- Locks remain `no-trade-evidence/listed-till` and `no-trade-evidence/evidence`, separate and non-nested. Rollback pending failure before unlock, owned connection close, network/sleep outside locks, dry-run read-only/lock-free, busy exit 75, ordinary degradation exit 0.
- One implementation task plus release verification. Follow AGENTS.md feature-branch PR and independent review. The latest specific user/operator authorization already covers full rollout and supersedes stale cron deferral: the parent owns merge after independent reviews and CI on the exact SHA, never implementer self-merge or bypassing CI/production safeguards.

## Preflight and Evidence Wiring

Run from the exact worktree root. Interpreter is the already provisioned project venv; unset Python injection variables. Evidence namespace is `docs/superpowers/evidence/verified-delisting-metadata/`; put execution output in an ignored/restricted scratch directory and link paths from `progress.md`. Do not commit DBs or raw upstream bodies.

```bash
test "$(git branch --show-current)" = fix/verified-delisting-metadata
git status --short
git rev-parse HEAD
env -u PYTHONPATH -u PYTHONHOME /home/hermes/algotrader/apps/api/.venv/bin/python -c 'import sys; print(sys.executable)'
openspec validate verify-delisting-metadata --strict --no-interactive
openspec validate data-quality --type spec --strict --no-interactive
git diff --check
```

Read `preflight.md`'s case ledger before Task 1. The ledger distinguishes metadata trust from later history failure; do not call all 13 supplied cases invalid metadata. Read the numbered identity rulings in design and record their acceptance in `progress.md`. No production path is needed to reproduce the bug.

### Task 1: CLI metadata trust boundary, one RED/GREEN/SDD cycle

**Files:**
- Modify: `apps/api/scripts/backfill_no_trade_evidence.py:76-105,162-235,292-321` — private probe, validation ordering and verified inactive ISIN reuse only.
- Create: `apps/api/tests/test_verified_delisting_metadata.py` — real CLI entry, real migrated file-backed SQL, real coverage gate and transport-only fakes.
- Modify only if required: `apps/api/tests/test_backfill_no_trade_evidence_lock.py` — change inactive probe tuples to `(board, date, upstream_isin)` and freeze module-local date bindings; preserve existing assertions and acquired role/phase behavior.
- Update execution records: `docs/superpowers/evidence/verified-delisting-metadata/progress.md`, change `tasks.md` only as corresponding gates actually pass.

**Interfaces:** Consumes `_get_meta_moex(ticker, yesterday, *, meta_cache, meta_lock)`, `_fetch_year_moex_outcome(market, board, ticker, year, last_trading_day=None)`, actual CLI `main() -> int`, and actual `check_coverage(conn, figis, coverage_threshold=0.95) -> list[dict]`. Produces CLI-private `_probe_board_last(ticker: str) -> tuple[str, str, str] | None`, with upstream verified ISIN as third field. Keep public `record_historical_no_trade_evidence(conn, *, db_path, figi, ticker, rows, board, isin, outcome, from_d, to_d, today=None)` unchanged.

- [ ] **Step 1: Add the runnable regression scaffold below to the new test file.** Fixtures are bounded to one FIGI, one year, one history row. No production defaults, no cross-test imports, no shared stdlib mutation. Both metadata call paths use the real parsers with a fake session. The year-history parser uses its actual `requests.get` module binding, replaced locally rather than modifying shared requests.

```python
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
    assert after == before
    assert gate_after == gate_before
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
```

- [ ] **Step 2: Run RED before changing the CLI.**

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest apps/api/tests/test_verified_delisting_metadata.py -k invalid_metadata_empty_window -v -p no:cacheprovider
```

Expected semantic failures include HTTP500/wrong SECID/ISIN/suffix date making `after != before`; malformed row/date may expose unchecked parser exceptions. Network/error controls may already pass. Record actual collected/pass/fail counts, traceback assertion and exit code; do not invent a total of failures. The exact adversarial NULL case must show before `stale`, after unexpected ready on the unfixed base. Repeat HTTP500/wrong-identity with bar `2026-08-31` to prove rejection precedes first history. Add those as separate parameterized real-CLI tests using the same helper; a history observer must never be reached for invalid metadata.

- [ ] **Step 3: Complete retained edge/transaction tests before GREEN.** Extend the same fixture with local ISIN parameter and existing evidence setup; assert invalid metadata preserves non-empty evidence including observed/expiry fields and local ISIN NULL/empty cannot certify delisting. Add malformed description/boards container cases (`None`, list, string), missing/duplicate required columns/identity rows, long rows, the exact accepted/rejected activity forms below, empty board id and second-board invalid date. Add two valid inactive boards to verify the latest end wins and no history is requested when equal to last bar. Use the actual metadata payload through the fake session, not stubbed probe tuples for trust tests.

For the CLI-private inactive candidate probe, inactive forms are exactly integer `0` (bool excluded from that integer branch), boolean `False`, and exact string `"0"`. Active exact integer `1`, boolean `True`, and exact string `"1"` prevent delisting; every other form is invalid/unknown, including `0.0`, `1.0`, `""`, `None`, `" 0"`, `"0 "`, `"false"`, `"true"`, `"unknown"`, and integer `2`. Never use general truthiness or integer coercion in that probe. Put the rejection matrix on a non-primary `OTHER` board, alone or alongside inactive TQBR, so actual `_get_meta_moex` returns None and the strict probe is reached.

Preserve existing active-primary routing: actual `_get_meta_moex` uses `is_traded == 1`, accepting `1`, `True` and `1.0` on TQBR, but not string `"1"`. Test those three accepted controls separately with both bar dates and prior NULL/non-NULL listed-till. Ordinary history is permitted and required by these controls; partial transport isolates unchanged SQL/evidence. Require no delisting UPDATE, not no history. The first-history observer declares `expect_delisting=False` for active controls and `True` for validated inactive metadata, checks SQL state and traced UPDATE before recording history, and uses actual metadata/year parsers with transport-only fakes. Do not change the shared active parser to force fictional rejection.

The partial-history transport returns one valid zero-trade row and cursor columns `INDEX`, `TOTAL`, `PAGESIZE`, data `[[0, 2, 2]]`. The actual year parser must retain that one row, return `partial`, and make exactly one HTTP call. Never monkeypatch `_fetch_year_moex_outcome`; its real parser is the acceptance boundary.

The following additional tests use the scaffold's `customize` hook and real SQLite; add them to the same file before GREEN.

```python
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
def test_missing_local_identity_rejects_delisting(tmp_path, monkeypatch, local_isin):
    rc, before, after, gate_before, gate_after, events = exercise(
        tmp_path, monkeypatch, metadata(), local_isin=local_isin)
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
```

For contention, retain and run existing `test_cli_busy_exits_75_and_emits_one_defer_line`, `test_cli_dry_run_does_not_acquire_writer_lock`, `test_cli_listed_till_uses_separate_lock_from_evidence` and `test_cli_partial_outcome_exits_zero_without_writing_evidence` in `test_backfill_no_trade_evidence_lock.py`. Update their private tuple fixtures and FrozenDate module bindings only. Add commit-failure injection through a CLI-local `sqlite3` proxy (never shared `sqlite3.connect`): forward execute/commit/close to a real file-backed connection, make commit raise numeric SQLITE_BUSY after UPDATE, and observe real SQL restoring the prior date. Track rollback while the real flock is still held and `close` after CLI exit; assert one exit-75 deferral. Inject direct BaseException and rollback failure separately to prove original failure is preserved and release/close attempted. Force only evidence lock busy after successful listed-till commit; assert 75 with verified date retained and no evidence change. Bound lock waits; never create child processes under a held lock.

- [ ] **Step 4: Implement the minimal CLI change.** Change private probe to return `(board, date, upstream_isin)`. Validate response status before JSON, exact container/list/column/row shapes, unique `name/value` and board columns, one exact `SECID`/`ISIN`, non-empty identifiers, each board row's non-empty exact `secid == ticker`, all inactive activity forms (`type(value) is int and value == 0`, `value is False`, or `type(value) is str and value == "0"`; never truthiness/int coercion) and strict calendar dates (parse then require `parsed.isoformat() == raw`). Catch transport/JSON/shape errors as unknown, not an unchecked parser exception. Check upstream/local non-empty matching ISIN before UPDATE or history, retain foreign/future guards, and carry the same verified ISIN into evidence for the inactive path. Do not use `fetch_issuer_identity` to re-certify an inactive board or move delisting behind the history outcome/zero-row gate. Keep existing UPDATE transaction rollback/close/lock code intact. The design provides the private interface; no additional abstraction is required.

At the CLI boundary, catch ordinary parser/transport failures from `_get_meta_moex` as unknown and let the strict inactive probe reject them; the current active helper can raise on null/list boards before the inactive probe is reached. Do not change that shared helper globally.

```python
try:
    meta = _get_meta_moex(
        ticker, last_session, meta_cache=meta_cache, meta_lock=meta_lock,
    )
except Exception:
    meta = None
```

Reference minimal probe replacement (code belongs in the CLI only during execution):

```python
def _probe_board_last(ticker: str) -> tuple[str, str, str] | None:
    url = f"https://iss.moex.com/iss/securities/{urllib.parse.quote(ticker)}.json"
    try:
        response = _moex_session().get(url, timeout=(5, 30))
        if response.status_code != 200:
            return None
        data = response.json()
        if not isinstance(data, dict):
            return None
        parsed = {}
        for key, required in (
            ("description", {"name", "value"}),
            ("boards", {"secid", "boardid", "is_traded", "listed_till"}),
        ):
            block = data.get(key)
            if not isinstance(block, dict):
                return None
            cols, rows = block.get("columns"), block.get("data")
            if (not isinstance(cols, list) or not cols
                    or any(not isinstance(c, str) or not c for c in cols)
                    or len(set(cols)) != len(cols) or not required.issubset(cols)
                    or not isinstance(rows, list) or not rows):
                return None
            if any(not isinstance(row, list) or len(row) != len(cols) for row in rows):
                return None
            parsed[key] = [dict(zip(cols, row)) for row in rows]
        identity = {}
        for row in parsed["description"]:
            if row["name"] in ("SECID", "ISIN"):
                if row["name"] in identity:
                    return None
                identity[row["name"]] = row["value"]
        isin = identity.get("ISIN")
        if (identity.get("SECID") != ticker or not isinstance(isin, str)
                or not isin.strip()):
            return None
        candidates = []
        for row in parsed["boards"]:
            board, end, traded = row["boardid"], row["listed_till"], row["is_traded"]
            inactive = ((type(traded) is int and traded == 0)
                        or traded is False
                        or (type(traded) is str and traded == "0"))
            if (not isinstance(row["secid"], str) or not row["secid"]
                    or row["secid"] != ticker
                    or not isinstance(board, str) or not board.strip()
                    or not inactive
                    or not isinstance(end, str)
                    or date.fromisoformat(end).isoformat() != end):
                return None
            candidates.append((board, end, isin.strip()))
        return max(candidates, key=lambda candidate: candidate[1])
    except Exception:
        return None
```

In `main`, after a non-None probe and before any date/window mutation, unpack and compare:

```python
board, lt_iso, upstream_isin = probe
local_isin = str(r["isin"] or "").strip()
if not local_isin or upstream_isin != local_isin:
    figis_nometa += 1
    print(f"  [{i}/{len(todo)}] {ticker}: "
          f"moex_historical_evidence_rejected figi={figi} "
          "reason=identity_mismatch rows=0")
    continue
```

Keep the existing date/foreign-bar checks and transaction after this block. Before evidence, perform the old primary-board issuer probe only for the active `meta is not None` path; inactive `meta is None` already has verified upstream ISIN. Keep the final equality guard and pass that upstream value unchanged. On `probe is None`, keep existing no-meta handling and add one bounded rejection diagnostic without dumping response data. Do not introduce a new outcome enum, CLI argument or lock.

- [ ] **Step 5: Run GREEN and related regressions.**

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_verified_delisting_metadata.py \
  apps/api/tests/test_backfill_no_trade_evidence_lock.py \
  apps/api/tests/test_historical_moex_final_fixes.py \
  apps/api/tests/test_moex_no_trade_evidence.py \
  apps/api/tests/test_moex_all_paths_identity.py \
  apps/api/tests/test_backfill_metadata_poison.py \
  apps/api/tests/test_backfill_coverage.py \
  apps/api/tests/test_writer_lock.py -q -p no:cacheprovider
```

Require no new failure, no skipped new acceptance case, actual first-history observer and SQL idempotency. Compare any baseline failure to `ad58b28` with the identical command on an isolated authorized worktree. Syntax compilation of this document is not RED/GREEN execution. Fix fixture mistakes without lowering input validation.

- [ ] **Step 6: Isolate and run full backend coverage.** Audit fixed main-checkout cwd, data/log paths and online tests first. Block outbound network and use isolated temporary data/log directories through existing test configuration. If safe isolation cannot be established, record blocker rather than touch production. From worktree `apps/api`:

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest tests --cov=algotrader_api --cov-branch --cov-report=term-missing --cov-fail-under=95 -q -p no:cacheprovider
```

Record exact counts/exit/coverage and unchanged `pyproject.toml` exclusions. Targeted pass is not a full-suite pass. No coverage workaround, dependency install, denom refresh or unrelated source edit.

- [ ] **Step 7: Commit and independent SDD review.**

```bash
git diff --check
git add apps/api/scripts/backfill_no_trade_evidence.py apps/api/tests/test_verified_delisting_metadata.py
# Stage existing lock-fixture file only if its interface adaptation was needed.
git commit -m "fix(ingestion): verify delisting metadata before listed-till mutation"
```

Parent dispatches independent spec and quality review of exact SHA after implementation. Reviewer must trace successful metadata validation before mutation/history, negative SQL preservation including empty window, positive idempotency, genuine metadata retained after degraded history, no evidence inflation, foreign/future guards and rollback-before-unlock. Address blockers in the same task and re-run gates. No delegation occurs during this docs-only drafting pass.

### Task 2: Release verification in the same authorized parent flow

**Files:** Execution evidence/progress and change tasks only; no additional feature scope.

**Interfaces:** Consumes independently reviewed implementation SHA and actual RED/GREEN/full-suite reports; produces verified PR/CI, parent/operator merged SHA, WAL-aware backup, exact-SHA deployment and readback evidence.

- [ ] **Step 1: Repeat preflight strict commands and diff check.** Review new scenario mapping and canonical files for unchanged clauses. Do not apply/archive before actual approved implementation completion.
- [ ] **Step 2: Publish PR and verify remote target/CI.**

```bash
git push -u origin fix/verified-delisting-metadata
gh pr create --base main --head fix/verified-delisting-metadata --title "fix: verify delisting metadata before mutation" --body-file openspec/changes/verify-delisting-metadata/proposal.md
gh pr view --json number,url,headRefOid,reviewDecision,statusCheckRollup
gh pr checks --watch
```

Read back exact head SHA, actual check conclusions and independent approval. Local coverage remains mandatory even if CI only checks branch names. The latest specific user/operator authorization supersedes stale cron deferral. The parent, not the implementer, owns merge after independent spec/quality reviews and successful CI on the exact head SHA; then read back `mergeCommit`. No CI or production safeguard is bypassed.

- [ ] **Step 3: Backup then deploy approved merged SHA.** In the existing authorized operator deployment flow, record prior/deployed SHAs and schedule/config backup paths. Create restricted SQLite online backup using `sqlite3.Connection.backup`; verify backup `PRAGMA integrity_check` equals `ok` before process/code changes. Preserve untracked operator config and existing scheduler topology. Prepare rollback to prior code SHA; DB restore is operator-controlled and WAL-aware, never copy only the main SQLite file over a running DB. No migration or cleanup is needed. This plan deliberately does not invent a deployment script: repository contains supervisor scripts, not a verified generic deploy command; parent must use its already established deployment procedure and read back the exact target.
- [ ] **Step 4: Bounded smoke and final readback.** Execute the same offline one-FIGI migrated fixture on deployed code twice, with explicit fixture DB and transport fakes, never the production-default full CLI. Require invalid metadata exact preservation and stale gate; valid metadata idempotency; metadata-valid/history-degraded date retained with no evidence/bar/expected mutation. Read back deployed SHA, smoke SQL/counters, integrity and service health through the existing operator flow. Link actual artifacts and timestamps from progress; report any remaining blocker.
- [ ] **Step 5: Keep capability acceptance separate from standing production goal.** Successful bounded smoke does not establish seven consecutive days of autonomous daily cycles or per-cohort readiness >=95%/freshness <=4 hours. Preserve those standing gates from the existing daily-reference plan; do not claim completion from this metadata fix. Apply/archive only this change after implementation acceptance, preserving all unrelated canonical clauses and existing unarchived deltas.
