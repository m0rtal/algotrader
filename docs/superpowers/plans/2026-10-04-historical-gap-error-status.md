# Historical Gap Error Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Reject historical error-event false-success without changing data or valid empty results.

**Architecture:** Worker-local relevant-event observation and distinct failure aggregation. Existing real runner and gap helper remain unchanged; a returning-error historical call does not abort the helper.

**Tech Stack:** Python, pytest, SQLite, existing namespace launcher and cached read-only dependencies.

**Spec:** `openspec/changes/report-historical-gap-errors/specs/data-quality/spec.md`; design in the same change. Existing canonical data-quality/writer-lock requirements remain binding.

## Global Constraints

- Preserve `_step_gap_recovery(db_path: str) -> tuple[bool, str]`, helper/runner signatures, upstream behavior/routing, cached expected bars, universe, identity/evidence/coverage rules, shared lock ownership and metadata BUSY policy.
- Relevant errors are ticker_progress/status=error for string FIGIs in the requested historical set or exactly the non-null active trailing FIGI; no regex or hardcoded instrument mapping.
- `failed=N` counts distinct failed FIGIs across both passes. Successful zero without errors is allowed; counts and committed rows are retained. No raw error payloads in new phase details.
- Keep gap recovery noncritical; daily later phases execute and rc=1 for a failed step.
- No production DB/process/cron writes by children, no network/installs/sync, no tests against production defaults. All real-path fixtures use explicitly migrated disposable file DBs in the masked namespace.
- Local tests and deployed fixture smoke do not prove >=95% cohort readiness, <=4h freshness or seven consecutive autonomous cycles.

### Task 1: Implement and independently verify worker-local historical error accounting

**Files:** Modify `apps/api/worker.py:684-832`; create `apps/api/tests/test_worker_historical_gap_status.py`. Existing regressions in `apps/api/tests/test_worker_trailing_gap_recovery.py`, `apps/api/tests/test_gap_recovery.py` and `apps/api/tests/test_daily_reference_writer_coordination.py` remain intact. No runner/helper runtime edits.

**Interfaces:** Consumes actual `recover_gaps(db_path, runner, gaps) -> dict[str,int]`, `BarGap.figi`, real `BackfillRunner._backfill_one` and event sink. Produces the same phase tuple with truthful bool and distinct failed counter; Task 2 consumes reviewed exact SHA and gate artifacts.

- [ ] **Step 1: Add the parent real-path RED/control fixture.** Use the self-contained code below as the baseline test in the new test file. It exercises actual finder/helper/runner and observes original event emission. Pin inputs to the historical dates shown; no clock-sensitive FrozenDate SQLite subclasses. Make `client.calls > 0` additionally assert the bounded expected one call when confirming the source retry contract.

```python
import json
import sqlite3
import pytest
import worker
from algotrader_api.db import sqlite as sqlitedb
from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.ingestion.backfill import BackfillRunner
from algotrader_api.data_quality.gap_recovery import find_gaps

@pytest.mark.parametrize('fail', [False, True], ids=['healthy-empty', 'upstream-error'])
def test_actual_historical_phase_reports_transport_error(tmp_path, monkeypatch, fail):
    db = tmp_path / 'state.db'
    sqlitedb.close_all()
    sqlitedb.run_migrations(str(db), MIGRATIONS_DIR)
    sqlitedb.close_all()
    c = sqlite3.connect(db)
    c.execute("INSERT INTO instruments(figi,ticker,class,name,currency,lot_size,isin,source_updated_at,expected_bars) VALUES ('PARENT-HIST','GAZP','share','fixture','RUB',1,'RU0007661625','2026-09-07',3)")
    for day in ['2026-09-07', '2026-09-09']:
        c.execute("INSERT INTO bars(figi,ts,open,high,low,close,volume,source) VALUES ('PARENT-HIST',?,1,1,1,1,1,'tinkoff')", (day,))
    c.commit()
    before = c.execute('SELECT * FROM bars ORDER BY ts').fetchall()
    c.close()
    gaps = find_gaps(str(db))
    assert len(gaps) == 1 and gaps[0].from_.isoformat() == '2026-09-08' and gaps[0].to_.isoformat() == '2026-09-08'
    class Client:
        def __init__(self): self.calls = 0; self.closed = False
        async def get_candles(self, **kwargs):
            self.calls += 1
            assert kwargs['figi'] == 'PARENT-HIST'
            if fail: raise RuntimeError('synthetic upstream failure')
            return []
        async def aclose(self): self.closed = True
    client = Client()
    monkeypatch.setattr(worker.client_mod, 'make_client', lambda **kwargs: client)
    monkeypatch.setattr(worker, '_collect_trailing_gaps', lambda *args: [])
    events = []
    original_emit = BackfillRunner._emit
    async def capture(self, event_type, payload):
        if event_type == 'ticker_progress': events.append({'figi': payload.get('figi'), 'status': payload.get('status')})
        return await original_emit(self, event_type, payload)
    monkeypatch.setattr(BackfillRunner, '_emit', capture)
    try:
        ok, detail = worker._step_gap_recovery(str(db))
        c = sqlite3.connect(db)
        after = c.execute('SELECT * FROM bars ORDER BY ts').fetchall()
        status = c.execute("SELECT last_run_status FROM instrument_metadata WHERE figi='PARENT-HIST'").fetchone()[0]
        c.close()
        assert client.closed and client.calls > 0
        assert after == before
        assert events == [{'figi': 'PARENT-HIST', 'status': 'error' if fail else 'empty'}]
        assert status == ('error' if fail else 'skipped')
        print('ACTUAL_HISTORY_PROBE=' + json.dumps({'upstream_failed': fail, 'phase_ok': ok, 'detail': detail, 'events': events, 'metadata_status': status, 'transport_calls': client.calls, 'client_closed': client.closed, 'bars_unchanged': after == before}))
        assert ok is (not fail), 'Historical error must fail the phase; healthy empty result must remain successful'
    finally:
        sqlitedb.close_all()
```

- [ ] **Step 2: Re-run RED with exact baseline source in a fresh masked archive.** Copy the validated `/home/hermes/.hermes/cache/scratch/delisting-release-parent/historical-probe.sh` and its environment topology into an OWN scratch directory; point its `A` and self-invocation to that directory, archive this worktree's candidate into `repo`, mount dependency venv read-only and mask production/Hermes/worktrees. Set the pytest target to `tests/test_worker_historical_gap_status.py`, outputs to owned scratch. Invoke `bash <owned-launcher> cli`, preserve the inner exit code. Baseline expected: error case fails at `assert ok is (not fail)`, healthy-empty passes; real event/SQL assertions precede the failing phase assertion. No `uv sync` or installs.

- [ ] **Step 3: Implement only the worker observer/accounting.** Register a historical set before awaiting recovery. The following local pattern shows the intended guards; integrate with the existing collector/exception branches and return without duplicating the recovery loop:

```python
historical_figis: set[str] = set()
failed_figis: set[str] = set()
active_trailing_figi: str | None = None

async def observe_recovery_error(event) -> None:
    figi = event.payload.get("figi")
    if (event.type == "ticker_progress"
            and event.payload.get("status") == "error"
            and isinstance(figi, str)
            and (figi in historical_figis
                 or (active_trailing_figi is not None
                     and figi == active_trailing_figi))):
        failed_figis.add(figi)
# Existing runner construction uses this event sink.
# After the existing gaps = find_gaps(db_path):
historical_figis.update(g.figi for g in gaps)
# Existing caught non-deferred trailing exception adds figi to failed_figis.
# Existing final bool is not failed_figis; failed counter is len(failed_figis).
```

- [ ] **Step 4: Add bounded acceptance matrix using the real fixture topology.** Extend the seed/transport mapping, not the runner return, to exercise a failed historical FIGI followed by a healthy committed one; assert phase false, failed=1, SQL successful bar present, returned actual count and client closed. For cross-pass dedupe, select the same historical FIGI as a bounded trailing tuple `(figi, date(2026,9,10), date(2026,9,10), False)` through the trailing-selection boundary and return transport error in both calls; assert failed=1 and two actual transport calls. Wrap original `_emit` to deliver an unrelated FIGI error and non-string identity before the genuine healthy empty event; assert phase true, failed=0. Use existing metadata-BUSY and daily-chain test patterns to assert structured defer, closure, later phase execution and rc=1. Every fake sequence has an explicit finite call bound; no infinite pagination or global module monkeypatch pollution. Successful candle fixture must match the existing SDK shape in current runner tests; inspect that fixture before using it.

- [ ] **Step 5: Run GREEN and regressions.** Execute the owned isolated launcher against the new test file plus the three existing regression files listed in Files. Account for every failure/error/skip and actual exit status. Then archive the exact candidate anew and run the validated isolated `full` mode: `pytest -q -p no:cacheprovider -p pytest_asyncio.plugin -p pytest_cov.plugin tests --cov=algotrader_api --cov-branch --cov-report=json:<owned>/full-coverage.json --cov-fail-under=95 --basetemp=<owned>/pytest-full --junitxml=<owned>/full.xml`. Package combined/line/branch metrics must remain >=95%; report measured metrics separately, not inferred four-metric claims. Do not change exclusions/thresholds. Preserve API interpreter/dependency topology from the validated launcher.

- [ ] **Step 6: Self-review, scoped commit and independent gates.** Parent verifies only worker/new tests changed beyond approved docs. Run credential scanner/staged formatting explicitly from worktree; production-hardcoded memory-refresh hooks cannot safely run there. Commit on the feature branch only after truthful results, with `fix: report historical gap error events`. Parent supplies exact-range review package and brief/report to a read-only spec/quality reviewer; require `VERDICT: SPEC ✅ + Approved`, then broad whole-branch review. No PR/merge/deploy by implementer.

## Complete supplemental acceptance fixture for Task 1 Step 4

Append the following to the new test file. It uses the quotation/time/is_complete SDK shape already exercised in `apps/api/tests/test_backfill.py:171-179`. All dates are explicit historical dates; the dedupe test overrides only trailing selection and forces its `stale=False` Tinkoff path. No real clock is used to choose its window. Each call is recorded and bounded, real gap discovery is asserted before invoking the worker, and the caller creates a fresh migrated DB per test (there is no reused previous-test DB).

```python
from datetime import date
from types import SimpleNamespace


def run_matrix(tmp_path, monkeypatch, figis, *, failed=frozenset(),
               successful=frozenset(), trailing=False, unrelated=False):
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
    assert {g.figi for g in gaps} == set(figis)
    assert len(gaps) == len(figis)
    assert all(g.from_ == g.to_ == date(2026, 9, 8) for g in gaps)
    if len(figis) > 1:
        # Explicit fixture premise: verify real finder order, not a mocked gap list.
        assert gaps[0].figi == "AAA-FAIL"
    class Client:
        def __init__(self): self.calls = []; self.closed = False
        async def get_candles(self, **kw):
            self.calls.append((kw["figi"], kw["date_from"], kw["date_to"]))
            assert len(self.calls) <= len(figis) + int(trailing)
            allowed = {date(2026, 9, 8)} | ({date(2026, 9, 10)} if trailing else set())
            assert kw["date_from"] == kw["date_to"] and kw["date_from"] in allowed
            assert kw["interval"] == "CANDLE_INTERVAL_DAY"
            if kw["figi"] in failed: raise RuntimeError("synthetic upstream failure")
            if kw["figi"] in successful:
                q = lambda: SimpleNamespace(units=1, nano=0)
                return [SimpleNamespace(time=SimpleNamespace(year=2026,month=9,day=8),
                    open=q(), high=q(), low=q(), close=q(), volume=1, is_complete=True)]
            return []
        async def aclose(self): self.closed = True
    client = Client()
    monkeypatch.setattr(worker.client_mod, "make_client", lambda **kw: client)
    selected = [(figis[0], date(2026,9,10), date(2026,9,10), False)] if trailing else []
    monkeypatch.setattr(worker, "_collect_trailing_gaps", lambda *args: selected)
    if unrelated:
        original_emit = BackfillRunner._emit
        async def emit(self, kind, payload):
            if kind == "ticker_progress":
                for other in ("UNREQUESTED", None, 1, []):
                    await original_emit(self, kind, {"figi":other,"status":"error"})
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
        return result, original_rows, rows, states, client.calls
    finally:
        sqlitedb.close_all()


def test_historical_failure_preserves_committed_success_and_continues(tmp_path, monkeypatch):
    (ok, detail), before, after, states, calls = run_matrix(
        tmp_path, monkeypatch, ["AAA-FAIL", "BBB-GOOD", "CCC-EMPTY"],
        failed={"AAA-FAIL"}, successful={"BBB-GOOD"})
    assert ok is False and "failed=1" in detail
    assert "historical=1 across 3 gaps" in detail
    assert [c[0] for c in calls] == ["AAA-FAIL", "BBB-GOOD", "CCC-EMPTY"]
    assert len(after) == len(before) + 1
    assert all(row in after for row in before)
    added = [row for row in after if row not in before]
    assert added == [("BBB-GOOD", "2026-09-08", 1.0, 1.0, 1.0, 1.0, 1, "tinkoff")]
    assert states == {"AAA-FAIL":"error", "BBB-GOOD":"ok", "CCC-EMPTY":"skipped"}


def test_same_figi_historical_and_trailing_failure_counts_once(tmp_path, monkeypatch):
    (ok, detail), before, after, states, calls = run_matrix(
        tmp_path, monkeypatch, ["AAA-FAIL"], failed={"AAA-FAIL"}, trailing=True)
    assert ok is False and "failed=1" in detail
    assert before == after and states == {"AAA-FAIL":"error"}
    assert calls == [("AAA-FAIL",date(2026,9,8),date(2026,9,8)),
                     ("AAA-FAIL",date(2026,9,10),date(2026,9,10))]


def test_unrequested_or_nonstring_error_events_are_not_attributed(tmp_path, monkeypatch):
    (ok, detail), before, after, states, calls = run_matrix(
        tmp_path, monkeypatch, ["AAA-EMPTY"], unrelated=True)
    assert ok is True and "failed=0" in detail
    assert before == after and states == {"AAA-EMPTY":"skipped"}
    assert calls == [("AAA-EMPTY",date(2026,9,8),date(2026,9,8))]
```

These are planned acceptance tests, not new executed GREEN evidence. If the existing bars schema adds columns, use its explicit eight-field SELECT in the snapshot/added-row assertion; never remove the SQL content assertion. Metadata-BUSY/noncritical daily continuation remain separate unchanged-policy regression gates named in Step 4/5.

### Task 2: Parent-owned canonical evidence and production release

**Files:** Additive `openspec/specs/data-quality/spec.md`, this change's tasks, and `docs/superpowers/results/2026-10-04-historical-gap-error-status.md`. No further feature scope.

**Interfaces:** Consumes Task 1 exact reviewed SHA plus actual RED/GREEN/full JUnit/coverage; produces exact PR/CI/merged/deployed SHAs, restricted backup/cron records and actual deployed fixture smoke/readback.

- [ ] **Step 1: Strict preflight and additive apply only after Task 1 gates.** `openspec validate report-historical-gap-errors --strict --no-interactive`; `openspec validate data-quality --type spec --strict --no-interactive`; `git diff --check`. Add only the new requirement/scenarios under canonical Requirements, preserve all previous headers and clauses and compare normalized old canonical prefix/suffix. No archive before actual implementation acceptance.
- [ ] **Step 2: Publish exact feature PR and verify checks.** `git push -u origin fix/historical-gap-error-status`; `gh pr create --repo m0rtal/algotrader --base main --head fix/historical-gap-error-status --title "fix: report historical gap error events" --body-file <verified-parent-release-body>`; read exact head and `gh pr checks`. Branch-name CI is not a substitute for the local full backend gate. Verify no duplicate PR first. Parent owns authorized merge after independent reviews and exact-head checks, with `--match-head-commit` and remote merge readback; retain branch unless cleanup explicitly authorized.
- [ ] **Step 3: Backup-first exact-SHA rollout.** Parent creates online SQLite backup mode0600/integrity ok, snapshots/reads back unchanged cron and records rollback prior SHA. Verify merged tree matches reviewed source. Fast-forward production only, retain untracked config. This change modifies worker.py, so inspect exact argv/cwd/ppid and reload only affected child PIDs under existing supervisors; never stop healthy unrelated writers or enable QA/developer cron. Read back target SHA/new children/health/integrity.
- [ ] **Step 4: Re-run real fixtures on exact deployed archive and read back.** Require error case phase false/failed1, empty phase true/failed0, mixed committed counts, cross-pass dedupe, unrelated-event controls and closure in a network-disabled disposable migrated fixture. Read canonical production readiness with mode=ro/query_only/single snapshot; no refresh/recovery from the diagnostic. Report unchanged denominator, cohorts and unknown freshness/cycle gates.
- [ ] **Step 5: Record only actual gates.** Local coverage != production readiness. Seven full autonomous daily cycles without manual restart/pause/backfill remain unproven; a worker reload resets any no-intervention observation window. Update this change's checked tasks only from verified outcomes. Archive only this accepted change with already-applied canonical spec using `openspec archive report-historical-gap-errors --yes --skip-specs`; never replace/archive unrelated open deltas.
