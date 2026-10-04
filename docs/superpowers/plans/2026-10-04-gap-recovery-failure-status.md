# Gap Recovery Failure Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. A delegated leaf must not redelegate. Parent owns independent preflight, task review, and full-branch review.

**Goal:** Report actual trailing failures without losing successful bars or making gap recovery critical.

**Architecture:** Keep the existing worker and runner integer API. Observe existing terminal error events only for the active trailing FIGI and union them with caught exceptions. Keep the serial, shared-loop lifecycle and existing lock/chain policies.

**Tech Stack:** Python >=3.11, stdlib asyncio/SQLite/flock, installed pytest and pytest-cov; no added dependency.

**Spec:** `openspec/changes/report-gap-recovery-failures/specs/data-quality/spec.md`; canonical `openspec/specs/data-quality/spec.md`, Autonomous Pipeline Liveness; canonical `openspec/specs/writer-coordination/spec.md`, Separate Evidence Transactions and Atomic Evidence Reconciliation Failure Cleanup.

## Global Constraints

- The ordinary summary SHALL include bounded numeric `failed=N`, counting unique failed trailing FIGIs across exceptions and events once, and SHALL retain accurate historical, trailing, and source totals using existing runner integer counts.
- Gap recovery SHALL remain best-effort: subsequent daily phases run and the cycle returns `rc=1` after failure.
- Newly introduced trailing failure diagnostics SHALL NOT copy raw exception or event error payloads.
- Preserve committed bars, valid zero-row results, one-loop owned-client cleanup, cancellation propagation, existing historical handling, and metadata lock deferral.
- No new retries, dependencies, thresholds, critical phases, routes, writer roles, lock policies, or partial-chunk outcome architecture.
- Do not change source/tests until parent preflight review approves this draft. This commit is docs-only, not implementation or release approval.
- No production state/configuration, network, secrets, push, merge, or deploy in leaf work. Parent owns existing approved rollout; do not add a new human check-in between safe authorized steps.

## Evidence and file map

Base: `ad58b28a96f15b85984a228dcdb26f34ccf515fe`, branch `fix/gap-recovery-failure-status`, worktree `/home/hermes/worktrees/algotrader-gap-recovery-status`.

Read `apps/api/worker.py:684-816,1214-1295`, actual runner `apps/api/src/algotrader_api/ingestion/backfill.py:2296-2341,2633-2678,3092-3104`, `apps/api/src/algotrader_api/data_quality/gap_recovery.py:134-169`, and existing worker/gap/coordination tests. Historical recovery does not catch ordinary per-gap exceptions; this plan must not silently broaden it.

Prior independent actual-import evidence: `/home/hermes/.hermes/cache/scratch/gap_actual_worker_results/REPORT.md` and `results.json`. Diagnostic RED: `/home/hermes/.hermes/cache/scratch/gap_actual_worker_red.log`, **6 failed, 1 passed**. Pointer only: do not copy scratch assertions or AST/function-clone tests. These results are prior evidence, not this draft's test run. Fresh-fixture migration 016 warnings are known baseline evidence, not a regression claim.

Files and responsibility:

- Modify `apps/api/worker.py:_step_gap_recovery` only: scoped observer, unique failure count, bounded trailing warning, truthful summary.
- Create `apps/api/tests/test_worker_gap_recovery_failure_status.py`: actual-import offline contracts; retain existing tests unchanged unless deterministic fixture isolation requires a reviewed adjustment.
- Modify `openspec/specs/data-quality/spec.md` only after GREEN/review: append additive requirement; do not replace canonical text with the delta.
- Maintain this change's supporting docs/task checklist; create `docs/superpowers/results/2026-10-04-gap-recovery-failure-status.md` as written release evidence.
- Plan-owned ignored workspace `.superpowers/sdd/2026-10-04-gap-recovery-failure-status/`: ledger, briefs, reports, review packages. Do not read or write sibling ledgers.

## Execution preparation

- [ ] Parent reads spec/design and ledger coupling rows, independently reviews this draft, and records preflight verdict before dispatching Task 1.
- [ ] Verify worktree HEAD and status. Resolve only this plan's workspace with `/home/hermes/.hermes/skills/productivity/subagent-driven-development/scripts/sdd-workspace docs/superpowers/plans/2026-10-04-gap-recovery-failure-status.md`.
- [ ] Record BASE, use fresh implementer and independent reviewer identities, and extract Task 1 with the skill's `scripts/task-brief`. Require task verdicts for spec compliance and code quality. Final review gets the full BASE..HEAD diff and every ledger ruling, including parked findings.
- [ ] Use existing offline interpreter `/home/hermes/algotrader/apps/api/.venv/bin/python` from this worktree's `apps/api`. If unavailable, use the prior cached environment `/home/hermes/.hermes/cache/scratch/gap-real-worker-verify-env/bin/python` for focused diagnostics only; do not call missing exporters/fixtures a valid RED. Cached package installation only, no dependency manifests changed. A production config/SDK factory call is a fixture defect: fix fixture, not source.

### Task 1: Truthful trailing failure status

**Files:**

- Modify: `apps/api/worker.py:684-816`.
- Create/Test: `apps/api/tests/test_worker_gap_recovery_failure_status.py`.
- Report: this plan's ignored `task-1-report.md`.
- Fix round 1 compatibility allowlist (parent ruling after independent source review): modify only `gap_env.FrozenWorkerDate.fromisoformat` and the trailing branch of `test_gap_metadata_adapters_preserve_other_error_policy` in `apps/api/tests/test_daily_reference_writer_coordination.py`. Return an ordinary `date` for SQLite binding; assert False/failed=1 without DEFER and exact FIGI/error_type diagnostics, absent raw error/message. The additive approved truthful-failure and no-raw-payload contract supersedes old trailing success/raw-error assertions. Preserve historical policy and all close/release/DB-continuity checks. If this ruling is wrong, it could conceal a fixture type defect or weaken diagnostics/ownership; fresh RED, exact diagnostic assertions and unchanged ownership checks bound that risk. No source or spec semantics changes in this round.

**Interfaces:**

- Consumes actual `_step_gap_recovery(db_path: str) -> tuple[bool, str]`, `recover_gaps(db_path, runner, gaps) -> dict[str, int]`, actual `BackfillRunner._backfill_one(*, figi, ticker=None, from_, to, source="auto") -> int`, and actual async `event_sink(BackfillEvent) -> None` with `.type` and `.payload`.
- Produces unchanged tuple and runner integer signatures. Ordinary summary adds `failed=N`; `ok` becomes false for any unique failed trailing FIGI. Metadata DEFER shape stays unchanged. No intermediate/new API parameters.

- [ ] **Step 1: Write the failing test and offline controls.** Put this complete scaffold in the new test file. The real-runner tests patch external candle I/O only and wrap actual `_emit` without replacing delivery. Boundary tests deliberately inject exceptions/events to verify deduplication and diagnostics; they do not substitute for actual-runner acceptance. Name the mutations detected: removing observer, failing to record caught exceptions, counting events, widening historical scope, marking `0` failure, dropping counters, closing early, swallowing cancellation, or changing chain policy.

```python
from __future__ import annotations

import asyncio
import fcntl
import importlib
import socket
import sqlite3
import sys
from collections import deque
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 9, 22)


def candle(figi, ts="2026-09-21"):
    return dict(figi=figi, ts=ts, open=10, high=10, low=10, close=10, volume=1)


class Broker:
    def __init__(self):
        self.answers = {}
        self.calls = []
        self.loops = []
        self.active = set()
        self.close_count = 0

    async def get_candles(self, **kwargs):
        task = asyncio.current_task()
        self.active.add(task)
        self.calls.append(kwargs)
        self.loops.append(asyncio.get_running_loop())
        try:
            await asyncio.sleep(0)
            value = self.answers[kwargs["figi"]].popleft()
            if isinstance(value, BaseException):
                raise value
            return value
        finally:
            self.active.remove(task)

    async def aclose(self):
        assert not self.active
        self.loops.append(asyncio.get_running_loop())
        self.close_count += 1


@pytest.fixture
def offline(tmp_path, monkeypatch):
    # Guard before actual worker import. No Settings instance or broker factory runs.
    def forbidden(*args, **kwargs):
        raise AssertionError("network/settings/SDK/secrets forbidden in offline test")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALGOTRADER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    from algotrader_api import config
    from algotrader_api.ingestion import client as client_mod
    monkeypatch.setattr(config, "get_settings", forbidden)
    monkeypatch.setattr(config, "Settings", forbidden)
    monkeypatch.setattr(client_mod, "get_broker_token", forbidden)
    monkeypatch.setattr(client_mod, "load_broker_token", forbidden)
    monkeypatch.setattr(client_mod, "make_client", forbidden)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        sys.modules.pop("worker", None)
        worker = importlib.import_module("worker")
    finally:
        sys.path.pop(0)
    from algotrader_api.ingestion import backfill
    from algotrader_api.db import sqlite as sqlitedb
    from algotrader_api.db.migrations import MIGRATIONS_DIR
    sqlitedb.close_all()
    db = str(tmp_path / "state.db")
    sqlitedb.run_migrations(db, MIGRATIONS_DIR)
    broker = Broker()
    events = []
    original_emit = backfill.BackfillRunner._emit

    async def record_emit(self, event_type, payload):
        events.append((event_type, dict(payload)))
        await original_emit(self, event_type, payload)

    monkeypatch.setattr(backfill.BackfillRunner, "_emit", record_emit)
    monkeypatch.setattr(worker, "date", FixedDate)
    monkeypatch.setattr(backfill, "date", FixedDate)
    monkeypatch.setattr(worker, "logger", MagicMock())
    monkeypatch.setattr(client_mod, "make_client", lambda **kwargs: broker)
    assert Path(worker.__file__).resolve() == Path(__file__).resolve().parents[1] / "worker.py"
    assert worker._step_gap_recovery.__code__.co_filename == worker.__file__
    env = SimpleNamespace(worker=worker, backfill=backfill, broker=broker,
                          db=db, events=events, monkeypatch=monkeypatch)
    try:
        yield env
    finally:
        sqlitedb.close_all()
        sys.modules.pop("worker", None)


def seed(env, figi, dates=("2026-09-18",), answers=([],)):
    with sqlite3.connect(env.db) as con:
        con.execute("INSERT INTO instruments(ticker,figi,class,name,currency,lot_size) "
                    "VALUES(?,?,'stock',?,'RUB',1)", (figi, figi, figi))
        con.executemany("INSERT INTO bars(figi,ts,open,high,low,close,volume) "
                        "VALUES(?,?,10,10,10,10,1)", [(figi, ts) for ts in dates])
    env.broker.answers[figi] = deque(answers)


def rows(env):
    with sqlite3.connect(env.db) as con:
        return con.execute("SELECT figi,ts FROM bars ORDER BY figi,ts").fetchall()


def assert_closed(env):
    assert env.broker.close_count == 1
    assert not env.broker.active
    assert len({id(loop) for loop in env.broker.loops}) == 1


def test_real_runner_all_chunks_failed(offline):
    e = offline
    seed(e, "A", answers=(RuntimeError("offline fetch failure"),))
    ok, detail = e.worker._step_gap_recovery(e.db)
    assert ok is False
    assert "failed=1" in detail
    assert ("ticker_progress", {"figi": "A", "status": "error", "bars_written": 0,
                                "error": "offline fetch failure"}) in e.events
    with sqlite3.connect(e.db) as con:
        assert con.execute("SELECT last_run_status FROM instrument_metadata "
                           "WHERE figi='A'").fetchone() == ("error",)
    assert_closed(e)


def test_real_partial_commit_and_continuation(offline):
    e = offline
    seed(e, "A", answers=([candle("A")],))
    seed(e, "B", answers=(RuntimeError("offline fetch failure"),))
    seed(e, "C", answers=([candle("C")],))
    ok, detail = e.worker._step_gap_recovery(e.db)
    assert ok is False
    assert "failed=1" in detail
    assert "2 bars filled" in detail
    assert "historical=0" in detail and "trailing=2" in detail
    assert "moex=0" in detail and "tinkoff=2" in detail
    assert ("A", "2026-09-21") in rows(e) and ("C", "2026-09-21") in rows(e)
    assert [call["figi"] for call in e.broker.calls] == ["A", "B", "C"]
    assert_closed(e)


def test_raised_ordinary_failures_count_unique_figis_and_continue(offline):
    e = offline
    for figi in ("A", "B", "C"):
        seed(e, figi)
    attempted = []

    async def boundary(self, *, figi, ticker, from_, to, source):
        attempted.append(figi)
        if figi in {"A", "B"}:
            raise RuntimeError("offline ordinary failure")
        return 0

    e.monkeypatch.setattr(e.backfill.BackfillRunner, "_backfill_one", boundary)
    ok, detail = e.worker._step_gap_recovery(e.db)
    assert ok is False and "failed=2" in detail
    assert attempted == ["A", "B", "C"]
    assert not detail.startswith("DEFER")
    assert_closed(e)


def test_exception_and_duplicate_events_count_once(offline):
    e = offline
    seed(e, "A")
    seed(e, "B")
    attempted = []

    async def boundary(self, *, figi, ticker, from_, to, source):
        attempted.append(figi)
        await self._emit("log", {"figi": figi, "status": "error"})
        await self._emit("ticker_progress", {"figi": "unrelated", "status": "error"})
        if figi == "A":
            for _ in range(2):
                await self._emit("ticker_progress", {"figi": figi, "status": "error"})
            raise RuntimeError("DO_NOT_COPY_RAW_PAYLOAD")
        return 0

    e.monkeypatch.setattr(e.backfill.BackfillRunner, "_backfill_one", boundary)
    ok, detail = e.worker._step_gap_recovery(e.db)
    assert ok is False and "failed=1" in detail
    assert attempted == ["A", "B"]
    assert "DO_NOT_COPY_RAW_PAYLOAD" not in detail
    failed = [call.kwargs for call in e.worker.logger.warning.call_args_list
              if call.args == ("worker.gap_recovery.fill_failed",)]
    assert failed == [{"figi": "A", "error_type": "RuntimeError"}]
    assert_closed(e)


@pytest.mark.parametrize("answers, reported", [(([],), 0), (([candle("A", "2026-09-18")],), 1)])
def test_real_zero_new_rows_not_failure(offline, answers, reported):
    e = offline
    seed(e, "A", answers=answers)
    before = rows(e)
    ok, detail = e.worker._step_gap_recovery(e.db)
    assert ok is True
    assert f"{reported} bars filled" in detail and "failed=0" in detail
    assert rows(e) == before
    assert not any(t == "ticker_progress" and p.get("status") == "error"
                   for t, p in e.events)
    assert_closed(e)


def test_no_gaps(offline):
    e = offline
    seed(e, "A", dates=("2026-09-22",), answers=())
    assert e.worker._step_gap_recovery(e.db) == (True, "gap recovery: no gaps")
    assert e.broker.calls == []
    assert_closed(e)


def test_historical_and_trailing_share_loop(offline):
    e = offline
    seed(e, "A", dates=("2026-09-16", "2026-09-18"),
         answers=([candle("A", "2026-09-17")], [candle("A")]))
    ok, detail = e.worker._step_gap_recovery(e.db)
    assert ok is True and "failed=0" in detail
    assert "2 bars filled" in detail and "historical=1 across 1 gaps" in detail
    assert "trailing=1 across 1 figis" in detail and "tinkoff=1" in detail
    assert ("A", "2026-09-17") in rows(e) and ("A", "2026-09-21") in rows(e)
    assert len(e.broker.calls) == 2
    assert_closed(e)


def test_historical_error_event_is_not_trailing_failure(offline):
    e = offline
    seed(e, "A", dates=("2026-09-16", "2026-09-18"),
         answers=(RuntimeError("offline historical failure"), []))
    ok, detail = e.worker._step_gap_recovery(e.db)
    assert ok is True and "failed=0" in detail
    assert any(t == "ticker_progress" and p.get("status") == "error" for t, p in e.events)
    assert_closed(e)


@pytest.mark.parametrize("metadata", [True, False])
def test_real_lock_contention(offline, metadata):
    e = offline
    from algotrader_api.ingestion import writer_lock as locks
    from algotrader_api.db import bars_sqlite
    seed(e, "A", answers=(RuntimeError("offline fetch failure") if metadata else [candle("A")],))

    @contextmanager
    def bounded_lock(path, **kwargs):
        kwargs["timeout_seconds"] = 0.01
        with locks.writer_lock(path, **kwargs):
            yield

    e.monkeypatch.setattr(e.backfill, "writer_lock", bounded_lock)
    e.monkeypatch.setattr(bars_sqlite, "writer_lock", bounded_lock)
    before = rows(e)
    with open(e.db + ".writer.lock", "a+b") as descriptor:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ok, detail = e.worker._step_gap_recovery(e.db)
    assert ok is False and rows(e) == before
    if metadata:
        assert detail.startswith("DEFER writer-lock-busy")
        assert "backfill-metadata" in detail and "result=deferred" in detail
        with sqlite3.connect(e.db) as con:
            assert con.execute("SELECT COUNT(*) FROM instrument_metadata").fetchone() == (0,)
    else:
        assert "failed=1" in detail and not detail.startswith("DEFER")
    assert_closed(e)


def test_direct_cancellation_propagates_after_settlement(offline):
    e = offline
    seed(e, "A", answers=(asyncio.CancelledError("offline cancellation"),))
    with pytest.raises(asyncio.CancelledError, match="offline cancellation"):
        e.worker._step_gap_recovery(e.db)
    assert_closed(e)


@pytest.mark.parametrize("phase, expected", [("gap_recovery", ["gap_recovery", "guardian"]),
                                             ("backfill_moex", ["backfill_moex"])])
def test_actual_chain_best_effort_and_critical(offline, phase, expected):
    e = offline
    seen = []
    statuses = []

    def fail(db):
        seen.append(phase)
        return False, "gap recovery: 0 bars filled (failed=1)"

    def guardian(db):
        seen.append("guardian")
        return True, "offline guardian"

    e.monkeypatch.setattr(e.worker, "get_settings",
                         lambda: SimpleNamespace(sqlite_path=e.db, log_level="INFO"))
    e.monkeypatch.setattr(e.worker, "setup_logging", lambda **kwargs: None)
    e.monkeypatch.setattr(e.worker, "heartbeat_loop", lambda *args: None)
    e.monkeypatch.setattr(e.worker, "_selected_phases", lambda subset: (phase, "guardian"))
    e.monkeypatch.setitem(e.worker._STEP_FUNCS, phase, fail)
    e.monkeypatch.setitem(e.worker._STEP_FUNCS, "guardian", guardian)
    e.monkeypatch.setattr(e.worker, "_log_chain_phase",
                         lambda db, phase, result, **kwargs: statuses.append((phase, result)))
    assert e.worker.run_daily_chain("daily") == 1
    assert seen == expected and statuses[0] == (phase, "error")
    assert e.worker._CRITICAL_PHASES == {"migrations", "universe_sync", "backfill_moex"}

```

- [ ] **Step 2: Run RED on unchanged source.** Run from worktree `apps/api`:

```bash
mkdir -p /home/hermes/.hermes/cache/scratch/gap-recovery-failure-status
unset PYTHONPATH PYTHONHOME
export PYTHONDONTWRITEBYTECODE=1
export TMPDIR=/home/hermes/.hermes/cache/scratch/gap-recovery-failure-status
/home/hermes/algotrader/apps/api/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_worker_gap_recovery_failure_status.py --basetemp="$TMPDIR/pytest-red" > "$TMPDIR/red.log" 2>&1
```

Expected RED: real all-chunks-failed, real partial success, dedup/exception, and real bar-writer contention fail on `ok is False` (old code returns `True`). Successful zero-row/shared-loop/historical-event controls also lack `failed=0`. No import/setup/network error counts as RED. Capture actual failures/counts rather than claiming the earlier diagnostic count for this new scaffold. Check finite sequences have no unexpected extra call: deque exhaustion is a fixture failure, not acceptable source RED.

- [ ] **Step 3: Implement the minimal change only after assertion RED.** Inside the existing `_step_gap_recovery`, before runner construction, add local state and pass this sink instead of `_async_noop_sink`. Existing event shape is not a future API:

```python
failed_trailing_figis: set[str] = set()
active_trailing_figi: str | None = None

async def observe_trailing_error(event) -> None:
    if (active_trailing_figi is not None
            and event.type == "ticker_progress"
            and event.payload.get("status") == "error"
            and event.payload.get("figi") == active_trailing_figi):
        failed_trailing_figis.add(active_trailing_figi)

runner = BackfillRunner(client=client, db_path=db_path,
                        event_sink=observe_trailing_error)
```

In `_run_all`, declare `nonlocal active_trailing_figi`. In each trailing attempt set the active FIGI, keep metadata lock re-raise, aggregate caught failures, and reset scope in `finally`:

```python
active_trailing_figi = figi
try:
    n = await runner._backfill_one(
        figi=figi, ticker=ticker, from_=from_, to=to_, source=source,
    )
except Exception as exc:
    if isinstance(exc, WriterLockBusy) and exc.role == "backfill-metadata":
        raise
    failed_trailing_figis.add(figi)
    logger.warning("worker.gap_recovery.fill_failed",
                   figi=figi, error_type=type(exc).__name__)
    continue
finally:
    active_trailing_figi = None
trailing_total += int(n or 0)
trailing_by_source_inner[source] += int(n or 0)
```

Leave historical call, single `asyncio.run`, awaited `aclose`, no-gap return, and outer metadata handler unchanged. Replace ordinary final return only:

```python
return not failed_trailing_figis, (
    f"gap recovery: {total_added} bars filled "
    f"(historical={hist_added} across {len(gaps)} gaps, "
    f"trailing={trailing_added} across {len(trailing)} figis "
    f"[moex={trailing_by_source.get('moex', 0)}, "
    f"tinkoff={trailing_by_source.get('tinkoff', 0)}], "
    f"failed={len(failed_trailing_figis)})"
)
```

No source refactor, new outcome class, retry, metadata query, or whole-phase rollback. If close-failure/cancellation precedence needs changing, record separate evidence and a narrow ruling rather than quietly widening this change.

- [ ] **Step 4: Run GREEN and regressions.** Repeat RED command using `pytest-green` basetemp and `green.log`. Then run:

```bash
/home/hermes/algotrader/apps/api/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_worker_gap_recovery_failure_status.py tests/test_worker_trailing_gap_recovery.py tests/test_worker_daily_chain.py tests/test_gap_recovery.py --basetemp="$TMPDIR/pytest-regression" > "$TMPDIR/regression.log" 2>&1
```

Verify all assertions, actual event delivery, committed rows, real flock outcomes, direct cancellation, exact critical set, and single-loop close. For MOEX source-counter parity retain existing routing tests and add this deterministic boundary case in the same new test file (no new source API):

```python
def test_historical_and_two_source_counters(offline):
    e = offline
    seed(e, "A", dates=("2026-09-21",))
    seed(e, "S", dates=("2026-09-10", "2026-09-14"))
    calls = []

    async def boundary(self, *, figi, ticker, from_, to, source="auto"):
        calls.append((figi, source))
        if source == "auto":
            return 4
        return 3 if source == "moex" else 2

    e.monkeypatch.setattr(e.backfill.BackfillRunner, "_backfill_one", boundary)
    ok, detail = e.worker._step_gap_recovery(e.db)
    assert ok is True and "failed=0" in detail
    assert "9 bars filled" in detail and "historical=4 across 1 gaps" in detail
    assert "trailing=5 across 2 figis" in detail
    assert "moex=3" in detail and "tinkoff=2" in detail
    assert calls == [("S", "auto"), ("A", "tinkoff"), ("S", "moex")]
    assert_closed(e)
```

If SQLite iteration order differs, assert the historical call is first and trailing calls as a set; do not change routing code to satisfy test ordering. Keep existing identity and evidence-defer tests in full regression. Record baseline migration warnings and their exact origin separately, never suppress all warnings to get a clean report.

- [ ] **Step 5: Self-review, independent task review, then commit.** Check only this step's observer observes active trailing FIGI; ordinary failure data does not include raw payload; no work or valid empty/idempotent remains success; committed rows remain; metadata DEFER preserves `result=deferred` and is not chain rc 75. Put RED/GREEN/regression command, exit status, counts, warnings, touched paths, and unresolved gates in the report. Parent supplies brief/report/diff to an independent reviewer and requires exact `VERDICT: SPEC ✅ + Approved` or `VERDICT: SPEC ❌ + Needs Fixes`. Fix reviewed defects via implementer and scoped re-review. Commit only approved exact paths:

```bash
git add apps/api/worker.py apps/api/tests/test_worker_gap_recovery_failure_status.py
git commit -m "fix: report trailing gap recovery failures"
```

### Task 2: Release verification record (docs-only)

**Files:**

- Modify: `openspec/specs/data-quality/spec.md`, additive requirement only after reviewed GREEN.
- Modify: `openspec/changes/report-gap-recovery-failures/tasks.md` and plan checkboxes only for verified work.
- Create: `docs/superpowers/results/2026-10-04-gap-recovery-failure-status.md`.

**Interfaces:**

- Consumes Task 1 commit, actual RED/GREEN/regression reports, task review verdict, and canonical data-quality.
- Produces a written evidence record and strict-valid specs, not a new runtime behavior. Parent's approved rollout remains separate; no prod or network action by this leaf.

- [ ] **Step 1: Run full backend gate and focused worker coverage.** Use the same safe environment and scratch directory. From worktree `apps/api`:

```bash
export COVERAGE_FILE="$TMPDIR/backend.coverage"
unset RUN_SANDBOX_INTEGRATION
/home/hermes/algotrader/apps/api/.venv/bin/python -m pytest -q -p no:cacheprovider tests --cov=algotrader_api --cov-report=term-missing --cov-fail-under=95 --basetemp="$TMPDIR/pytest-full" > "$TMPDIR/full.log" 2>&1
export COVERAGE_FILE="$TMPDIR/worker.coverage"
/home/hermes/algotrader/apps/api/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_worker_gap_recovery_failure_status.py tests/test_worker_trailing_gap_recovery.py tests/test_worker_daily_chain.py --cov=worker --cov-report=term-missing --cov-fail-under=0 --basetemp="$TMPDIR/pytest-worker" > "$TMPDIR/worker-coverage.log" 2>&1
```

Set `COVERAGE_FILE="$TMPDIR/backend.coverage"` before full command and `COVERAGE_FILE="$TMPDIR/worker.coverage"` before focused command. `worker.py` is outside package coverage; focused coverage is diagnostic, not a lowered 95% backend gate. Verify changed observer/error/summary lines and branches are exercised. Do not add pragmas, alter omissions, or reduce thresholds. Full-suite fixtures must remain scratch-owned; inspect legacy sandbox gates/fixtures before running and block any production/SDK access. Existing sandbox integration remains disabled. Any full-suite blocker stays explicitly unresolved, not PASS.

- [ ] **Step 2: Apply reviewed additive delta and strict-validate separately.** Append the `### Requirement: Truthful Best-Effort Trailing Gap Recovery` block and its scenarios to canonical `## Requirements`; preserve all other canonical sections. Do not overwrite with delta or archive before reviewed implementation completion. From worktree root run both commands:

```bash
openspec validate report-gap-recovery-failures --strict
openspec validate data-quality --type spec --strict
git diff --check
```

- [ ] **Step 3: Independent full-branch review and CI.** Parent provides whole BASE..HEAD diff, test outputs, and this plan's ledger to a reviewer independent of implementation. Resolve load-bearing findings; record all rulings and costs if wrong. Parent checks every required CI status on the exact candidate SHA, including branch-name-check and externally configured gates; this repository currently contains only that workflow. A local test run is not CI. No network/push/merge by this leaf. Missing CI is outstanding, never inferred green.

- [ ] **Step 4: Write release evidence and docs commit.** Use the following record structure, populated with actual outputs and exact SHAs; unchecked gates remain explicitly not verified:

```markdown
# Gap recovery failure status verification

## Candidate identity and scope

## Assertion RED: command, exit status, failing assertions, counts

## GREEN and focused regressions: commands, exit statuses, counts

## Full backend coverage and focused worker branch evidence

## Real-runner event, partial commit, empty/idempotent, lock, cancellation proof

## Independent task review and full-branch review verdicts

## Required CI status on exact candidate SHA

## Strict change and canonical validation; diff check

## Warnings, unresolved gates, rulings and cost if wrong

## Rollout ownership and limit of claims
```

State that truthful cycle failure does not prove freshness, coverage readiness increase, seven-day reliability, partial-chunk completeness, detached thread settlement, or SIGKILL cleanup. Do not mark deployment/rollout success without parent's written actual proof. Commit exact docs/spec paths after evidence exists. Archive only when approved complete; docs drafting alone does not complete implementation.

## Draft self-review

All eight delta scenarios map to Task 1's scaffold or counter case; Task 2 checks broad regression, coverage, canonical validity, CI, and independent review. No runner parameter is added. Wiring is `run_daily_chain -> _step_gap_recovery -> recover_gaps/_backfill_one -> _emit -> scoped observer`; integer return, event shape, tuple status, metadata DEFER, and chain rc are unchanged interfaces. Existing `_write_bars` returns candles passed in, not unique insert count; the idempotent fixture therefore expects reported count 1 and unchanged database rows. This plan preserves that API and does not claim unique-insert accounting. Source and tests have not been edited or executed in this docs-only drafting task. Parent independent preflight remains required.
