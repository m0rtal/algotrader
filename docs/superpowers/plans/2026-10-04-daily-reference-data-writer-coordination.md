# Daily Reference-Data Writer Coordination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend existing market-data coordination to seven explicit daily reference-data transaction owners without coordinating telemetry.

**Architecture:** Reuse `ingestion.writer_lock.writer_lock`, canonical `<db>.writer.lock`, its fail-closed process guard, and existing numeric BUSY classifier. The current data transaction owner acquires once; borrowers never acquire, commit, roll back or close. Preparation remains outside; current per-row/list atomicity remains unchanged.

**Tech Stack:** Python >=3.11 stdlib (`sqlite3`, `fcntl`, `threading`, `pathlib`, `contextlib`), existing pytest/pytest-cov, SQLite WAL; no added dependency.

**Spec:** `openspec/changes/coordinate-daily-reference-data-writers/specs/writer-coordination/spec.md`; read `proposal.md` and `design.md` in the same change. Canonical unchanged baseline: `openspec/specs/writer-coordination/spec.md`. Separate existing BUSY delta: `openspec/changes/sqlite-evidence-busy-defer/specs/writer-coordination/spec.md`.

## Global Constraints

- This file is a proposed implementation plan, not authorization to execute it. Approve the specification first.
- Documentation base is `951dee8`. Implement in a new feature branch/worktree, not this documentation branch or main. Never publish/merge/deploy without the required authorization and independent review.
- Additional owners are exactly `upsert_instruments`, `BackfillRunner._upsert_instrument`, `BackfillRunner._seed_metadata_for_figi`, `BackfillRunner._upsert_metadata`, `merge_into_corporate_actions`, worker `_step_corporate_actions` adjustment, `merge_into_dividends`.
- Default flock timeout stays exactly 30 seconds. SQLite wait values stay unchanged. Test wrappers inject short timeouts; no production environment knob or mutable-default workaround is added.
- Do not change the flock algorithm, canonical-path checks, same-process active-path/thread guard, descriptor lifecycle or process-creation prohibition.
- BEGIN, mutating statements, commit and attempted rollback execute while the owner holds flock. Acquisition failure starts no transaction. Catch `BaseException` around the owned transaction and preserve its primary failure if rollback fails.
- Prepare/filter/normalize rows, select candidates, derive splits, fetch, rate-limit, compute timestamps and sleep outside. Transaction-local duplicate/idempotency checks and SQL mutation arithmetic remain inside.
- Use dedicated connections for new universe transactions and existing owned connections for metadata/merge/adjustment. Never close or clean up someone else's active borrowed transaction.
- Keep universe per-row commits, merge complete-list commits and adjustment all-selected-events commit. No new bulk-upsert API, chunking policy, split algorithm or metadata semantic repair.
- Do not lock `get_connection`, `execute`, `execute_returning_id`, telemetry, pipeline rows, heartbeats, ingestion logs, guardian bookkeeping, dividend throttle queue, circuit breakers, migrations or startup repair.
- Do not wrap discovery, fetch, run, phases, loops or worker lifetimes with a locking decorator/context.
- Preserve existing identity gates, real-bar-over-evidence, auxiliary dry-run, actual row counts, retry/resume and narrow BUSY behavior. No class/denominator exclusions.
- Backend `fail_under = 95` remains unchanged. No new omission, pragma, xfail or reduced selector to manufacture a pass.
- Standing production goal remains separately measured: ML-ready coverage at least 95%, freshness at most 4 hours, observed autonomous daily retry/resume.

## Command root and evidence

Commands run from the candidate worktree root. Use the existing interpreter `/home/hermes/algotrader/apps/api/.venv/bin/python`; imports must resolve to the candidate `apps/api/src`, not its main checkout. Do not install dependencies or create a venv for this bounded change.

Documentation baseline actually run: canonical strict validation passed; `test_writer_inventory.py` returned **26 passed, 2 warnings** (Starlette/httpx and anyio deprecation), with cache and bytecode disabled. No runtime expansion or coverage claim was measured.

```bash
env OPENSPEC_TELEMETRY=0 DO_NOT_TRACK=1 OPENSPEC_NO_UPDATE_CHECK=1 \
  openspec validate coordinate-daily-reference-data-writers --type change --strict --no-interactive
env OPENSPEC_TELEMETRY=0 DO_NOT_TRACK=1 OPENSPEC_NO_UPDATE_CHECK=1 \
  openspec validate writer-coordination --type spec --strict --no-interactive
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_writer_inventory.py -q -p no:cacheprovider
```

## File map

Future production modifications, all bounded to owners/adapters:

- `apps/api/src/algotrader_api/ingestion/writer_lock.py:39-71`: Literal/runtime role and phase inventory only.
- `apps/api/src/algotrader_api/ingestion/universe.py:63-124`: owner of each existing instrument-row transaction; remove the use of auto-committing `execute_returning_id` from this path only.
- `apps/api/src/algotrader_api/ingestion/backfill.py:2841-2940`: three explicit row metadata owners; no broad runner refactor.
- `apps/api/src/algotrader_api/scripts_import/import_corporate_actions_common.py:32-70,124-160`: two merge transaction owners.
- `apps/api/worker.py:440-454,807-855`: corporate adjustment owner and daily contention adapters only.
- `apps/api/scripts/derive_splits.py:60-64`: existing direct derivation CLI temporary-failure adapter.
- `apps/api/src/algotrader_api/scripts_import/import_dividends_tinkoff.py:273-292`: existing dividend CLI temporary-failure adapter; fetch and queue functions remain non-owning.

Future tests:

- Extend `apps/api/tests/test_writer_lock.py`, `test_writer_lock_diagnostics.py`, `test_writer_inventory.py`.
- Create only one new test module: `apps/api/tests/test_daily_reference_writer_coordination.py` for real new-owner ordering, timeout/BUSY/cleanup, offline boundaries, CLI/daily outcomes and interprocess stress.
- Extend existing `tests/ingestion/test_universe.py`, `tests/test_derive_splits.py`, `tests/test_dividends_fetcher.py`, `tests/test_forward_adjustment.py`, `tests/test_worker_daily_chain.py` where existing fixtures fit the behavior. Keep legacy borrower tests intact.
- For regression isolation only, replace the main-checkout cwd in `tests/test_corporate_actions.py:105-119` with its own worktree `Path(__file__).resolve().parents[1]`; deny network in the existing face-value failure test using monkeypatch before full-suite execution. Do not fix unrelated production behavior.

No modification is planned to `db/sqlite.py`, `data_quality/forward_adjustment.py`, scheduler scripts, schema, configuration or coverage rules. If a real finding requires one, stop and revise scope.

---

### Task 1: Exact diagnostic identities and honest inventory transition

**Files:** Modify `ingestion/writer_lock.py:39-71`, `tests/test_writer_lock.py`, `tests/test_writer_lock_diagnostics.py`, `tests/test_writer_inventory.py`.

**Interfaces:** Consumes unchanged `writer_lock(db_path, *, role, phase, timeout_seconds=30.0)`, `WriterLockReentrant`, `WriterRole`, `WriterPhase`, `VALID_ROLES`, `VALID_PHASES`. Produces additional valid role/phase literals only; no new lock primitive.

- [ ] **Step 1: Add role/phase RED tests.** Append to existing lock tests:

```python
from typing import get_args
from algotrader_api.ingestion import writer_lock as lock_module

NEW_PAIRS = [
    ("universe-sync", "instruments"),
    ("backfill-metadata", "instruments"),
    ("backfill-metadata", "metadata"),
    ("corporate-actions", "corporate-actions"),
    ("corporate-actions", "adjusted-bars"),
    ("dividends", "dividends"),
]

@pytest.mark.parametrize("role,phase", NEW_PAIRS)
def test_daily_role_phase_is_valid(tmp_path, role, phase):
    assert role in get_args(lock_module.WriterRole)
    assert role in lock_module.VALID_ROLES
    assert phase in get_args(lock_module.WriterPhase)
    assert phase in lock_module.VALID_PHASES
    with lock_module.writer_lock(tmp_path / "state.db", role=role,
                                 phase=phase, timeout_seconds=0.05):
        pass
```

Run `test_writer_lock.py -k daily_role_phase -q`; expected RED: new literal absent. Extend both Literals and both frozensets with the exact values above. Rerun GREEN and all existing diagnostics tests; old valid and invalid strings retain their behavior.

- [ ] **Step 2: Pin existing thread guard, without changing its semantics.** Add a contender-thread test in the existing lock test file. It must collect exactly one `WriterLockReentrant`, never execute the contender body, and join the thread while the outer owner remains held:

```python
def test_daily_role_does_not_bypass_same_process_thread_guard(tmp_path):
    db = tmp_path / "thread.db"
    outcomes = []

    def contender():
        try:
            with lock_module.writer_lock(db, role="dividends",
                                         phase="dividends", timeout_seconds=0.05):
                outcomes.append("entered")
        except lock_module.WriterLockReentrant:
            outcomes.append("rejected")

    with lock_module.writer_lock(db, role="universe-sync", phase="instruments"):
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert outcomes == ["rejected"]
    with lock_module.writer_lock(db, role="dividends", phase="dividends"):
        pass
```

This is an existing-invariant regression; do not pretend it needs a primitive fix or change it into same-process queuing.

- [ ] **Step 3: Preserve original inventory and add an explicit parallel owner inventory.** Keep `IN_SCOPE_FUNCTIONS` and its special script markers. Define this exact new mapping, with paths relative to existing `_SRC_ROOT` and `_REPO_ROOT`:

```python
DAILY_TRANSACTION_OWNERS = {
    "universe-sync/instruments:upsert_instruments": _INGEST / "universe.py",
    "backfill-metadata/instruments:BackfillRunner._upsert_instrument": _INGEST / "backfill.py",
    "backfill-metadata/metadata:BackfillRunner._seed_metadata_for_figi": _INGEST / "backfill.py",
    "backfill-metadata/metadata:BackfillRunner._upsert_metadata": _INGEST / "backfill.py",
    "corporate-actions/corporate-actions:merge_into_corporate_actions": _SRC_ROOT / "scripts_import" / "import_corporate_actions_common.py",
    "corporate-actions/adjusted-bars:_step_corporate_actions": _REPO_ROOT / "apps" / "api" / "worker.py",
    "dividends/dividends:merge_into_dividends": _SRC_ROOT / "scripts_import" / "import_corporate_actions_common.py",
}
SUPPLEMENTAL_BAR_OWNERS = {
    "bar-writer/bars:replace_bars_for_figi_with_rowcount": _SRC_ROOT / "db" / "bars_sqlite.py",
}

def _qualified_function(tree, qualified_name):
    parts = qualified_name.split(".")
    body = tree.body
    for class_name in parts[:-1]:
        classes = [n for n in body if isinstance(n, ast.ClassDef)
                   and n.name == class_name]
        assert len(classes) == 1, qualified_name
        body = classes[0].body
    functions = [n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == parts[-1]]
    assert len(functions) == 1, qualified_name
    return functions[0]

@pytest.mark.parametrize("identity,path", list(DAILY_TRANSACTION_OWNERS.items()),
                         ids=list(DAILY_TRANSACTION_OWNERS))
def test_daily_owner_symbol_present(identity, path):
    assert path.exists()
    _qualified_function(_parse(path), identity.split(":", 1)[1])
```

The qualified resolver must not compare prefixed inventory keys directly to `ast.FunctionDef.name`; that would give false missing-symbol failures. Supplemental bar owner uses the same resolver.

- [ ] **Step 4: Replace only approved negatives; retain stronger proofs.** Remove exactly universe and dividend-fetcher paths from whole-module `OUT_OF_SCOPE_MODULES`. Retain the six other old entries; add `db/sqlite.py` and `db/migrations_runner.py`. Remove silent `if p.exists()` filtering and assert that each path exists. Add explicit no-direct-acquisition function inventory from design, including discovery, fetch `_run`, queue/dequeue, heartbeat/log/pipeline helpers, breaker writes and non-owning forward-adjustment helpers. Catching/importing exception/formatter is allowed in adapters; acquiring is not.

Scan source AST calls, including `writer_lock(...)` and module/alias-qualified acquisition calls. For each production module resolve lock import aliases once and reject acquisition calls/decorators in excluded bodies. For borrowers additionally reject new `commit`, `rollback` or `close` calls. Build the process-creation scan from the deduplicated union of original, supplemental and daily owner files. Keep the existing raw-bar and marker tests unchanged. A transitive call to an approved owner is allowed; wrapping its whole caller is not.

Do not temporarily xfail or delete the new positive boundary tests. Introduce each owner's runtime assertion in its own task so each commit is executable and honest.

- [ ] **Step 5: Run primitive/inventory tests, check diff and commit.**

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_writer_lock.py apps/api/tests/test_writer_lock_diagnostics.py \
  apps/api/tests/test_writer_inventory.py -q -p no:cacheprovider
git diff --check
git add apps/api/src/algotrader_api/ingestion/writer_lock.py \
  apps/api/tests/test_writer_lock.py apps/api/tests/test_writer_lock_diagnostics.py \
  apps/api/tests/test_writer_inventory.py
git commit -m "test(ingestion): define daily writer scope and identities"
```

Independent review: exact seven-owner mapping; all old values/invariants retained; exclusions explicit; no global helper lock.

---

### Task 2: Universe and BackfillRunner metadata transactions

**Files:** Modify `ingestion/universe.py:63-124`, `ingestion/backfill.py:2841-2940`; create/extend `tests/test_daily_reference_writer_coordination.py`; extend `tests/ingestion/test_universe.py` as needed.

**Interfaces:** Preserve `upsert_instruments(db_path: str, rows: list[dict]) -> int`; all three current BackfillRunner method signatures and `None` returns. Consume existing `writer_lock`, `is_sqlite_busy`, `writer_lock_path`, `WriterLockBusy`. No new public API.

- [ ] **Step 1: Build test-owned tracing and real owner calls before code.** Use existing `fresh_db` file-backed fixture. Seed one instrument `FCOORD` with required ticker/class/name/currency/lot fields and one raw bar `2024-05-15` with close 50; seed corporate split only for adjustment tests. Use `BackfillRunner(client=object(), db_path=fresh_db, event_sink=offline_sink)`; define `async def offline_sink(event): return None` in the test.

Implement this test-only observer; never move it to production:

```python
from contextlib import contextmanager
import sqlite3
from algotrader_api.ingestion import writer_lock as locks

@contextmanager
def observed_lock(db_path, *, role, phase, state, events, **kwargs):
    with locks.writer_lock(db_path, role=role, phase=phase, timeout_seconds=0.05):
        assert not state["held"]
        state["held"] = True
        events.append(("acquire", role, phase))
        try:
            yield
        finally:
            events.append(("release", role, phase))
            state["held"] = False

def connection_factory(real_connect, state, events, connections, failure=None):
    class Tracked(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            operation = sql.lstrip().split(None, 1)[0].upper()
            if operation in {"BEGIN", "INSERT", "UPDATE", "DELETE", "REPLACE"}:
                assert state["held"], sql
                events.append((operation,))
            return super().execute(sql, parameters)

        def commit(self):
            assert state["held"]
            events.append(("commit",))
            if failure is not None:
                raise failure
            return super().commit()

        def rollback(self):
            assert state["held"]
            events.append(("rollback",))
            return super().rollback()

        def close(self):
            assert not state["held"]
            assert not self.in_transaction
            events.append(("close",))
            return super().close()

    def connect(database, *args, **kwargs):
        kwargs["factory"] = Tracked
        connection = real_connect(database, *args, **kwargs)
        connections.append(connection)
        return connection
    return connect
```

Save `real_connect = sqlite3.connect` before monkeypatching; migration/seed/snapshot operations use that saved function, not the Tracked factory. Patch owner modules' `writer_lock` with `monkeypatch.setattr(..., raising=False)` to bind `state/events`; patch their `sqlite3.connect` only after seeding. Patching the imported sqlite3 module is process-wide for that test, so never call fixture setup after patching. For universe ensure `sqlitedb.close_all()` happens before patching and cleanup only after undo; its new connection must be dedicated, not the cached fixture handle. Use a separate test for an already-active cached transaction, not the successful ordering case.

Add four explicit cases: universe update; runner instrument UPSERT; runner seed metadata; runner metadata update. Assert the real persisted fields and that events contain exactly one acquisition, BEGIN, mutation, commit, release and owned close in that order. In the separate cached-transaction test, insert an uncommitted fixture marker on the cached handle. It reserves SQLite, so the dedicated universe transaction must defer with numeric BUSY, not succeed. Assert the cached handle remains in_transaction and sees its own marker, while an independent connection does not see it. Universe must not commit or roll back the marker. Roll back the cached handle in test cleanup, then retry universe successfully. This catches accidental continued use of `execute_returning_id` on the shared handle.

Run these cases with `-k 'universe or metadata'`; expected RED is missing owner acquisition/order, not an import/fixture error. Existing baseline methods have no lock.

- [ ] **Step 2: Add timeout and exception tests for all four owners.** Hold the real kernel lock through a separate open-file description with `fcntl.flock(LOCK_EX | LOCK_NB)` before invocation. Inject a 0.05-second lock via observer. Assert `WriterLockBusy.role` and `.phase` equal the exact identity, no pending mutation, no BEGIN, and unchanged full row snapshots. Release and retry; assert actual persisted fields.

For owned transaction failure use `failure=sqlite3.IntegrityError("fixture-commit-failure")` in Tracked. Assert mutation occurred, rollback precedes release, persisted state equals before, and original exception object survives. Repeat with a custom `class Interrupted(BaseException): pass`; no partial state or held lock remains. Inject rollback failure separately and assert cleanup still releases flock and preserves the primary error; a rollback-failing connection may not be reusable, so never falsely require its successful reuse.

- [ ] **Step 3: Implement universe ownership without new batching.** At `universe.py:97-123`, keep filtering and row-count meaning. Prepare each parameter tuple before acquisition. Replace only this path's auto-committing helper with `conn = sqlite3.connect(db_path, timeout=30.0)` owned by the function, closed in outer finally. Execute `conn.execute("PRAGMA foreign_keys=ON")` before row transactions to preserve the old cached helper's constraint setting. Preserve the current 30000-millisecond SQLite busy timeout and existing database journal mode; do not add journal-mode changes. Retain per-row transactions; the slicing value 100 remains only an iteration detail. Copy the existing UPSERT text exactly into `sql` and the tuple at lines 112-121 into `params` before the lock. The mutation block is:

```python
with writer_lock(db_path, role="universe-sync", phase="instruments"):
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(sql, params)
        conn.commit()
    except BaseException as exc:
        try:
            conn.rollback()
        except BaseException:
            pass
        if is_sqlite_busy(exc):
            raise WriterLockBusy(
                role="universe-sync", phase="instruments",
                database_path=str(writer_lock_path(db_path))[:-len(".writer.lock")],
                lock_path=str(writer_lock_path(db_path)),
                timeout_seconds=30.0, reason="sqlite-busy",
            ) from exc
        raise
inserted += 1
```

Define `sql` and `params` from this exact existing statement before the per-row context:

```python
sql = (
    "INSERT INTO instruments "
    "(ticker, figi, class, name, currency, lot_size, isin, sector) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(figi) DO UPDATE SET "
    "ticker=excluded.ticker, class=excluded.class, "
    "name=excluded.name, currency=excluded.currency, "
    "lot_size=excluded.lot_size, isin=excluded.isin, "
    "sector=excluded.sector"
)
params = (
    r["ticker"], r["figi"], r["class"], r["name"], r["currency"],
    r["lot_size"], r.get("isin"), r.get("sector"),
)
```

No field policy is borrowed from the different BackfillRunner SQL. Correct the misleading 100-row transaction docstring only. Do not alter `db/sqlite.py`.

- [ ] **Step 4: Implement each bounded BackfillRunner owner.** Keep validation/defaults in `_upsert_instrument:2842-2855`, timestamp construction in `_upsert_metadata:2913`, and all SQL/parameter values unchanged. Move parameter tuple construction outside the lock. At each existing `con.execute`/`con.commit` block, acquire `role="backfill-metadata"` with phase `instruments` for instrument UPSERT and `metadata` for seed/upsert. Put BEGIN inside the try and retain owned close outside. For each of the three helpers use this exact failure block inside its acquired context:

```python
except BaseException as exc:
    try:
        con.rollback()
    except BaseException:
        pass
    if is_sqlite_busy(exc):
        raise WriterLockBusy(
            role="backfill-metadata", phase=phase,
            database_path=str(writer_lock_path(self.db_path))[:-len(".writer.lock")],
            lock_path=str(writer_lock_path(self.db_path)),
            timeout_seconds=30.0, reason="sqlite-busy",
        ) from exc
    raise
```

Bind `phase = "instruments"` or `phase = "metadata"` in that helper before acquisition. Do not wrap `_discover_universe`, `_backfill_one`, `_write_bars`, `_log`, breaker helpers or emitted events. Do not consolidate the three methods into a generic transaction framework.

- [ ] **Step 5: GREEN, semantic regressions, commit and review.** Run new four-owner cases, universe discovery/filter/derived-field preservation tests, `test_universe_sync_chain.py`, `test_backfill_metadata_poison.py`, `test_backfill_run_coverage.py`, and existing inventory. Convert only tests that now really invoke an owner with `:memory:` into file-backed fixtures; do not create a lock for a fictitious in-memory production path or add a compatibility fallback.

Commit changed owner files and explicit test files only: `feat(ingestion): coordinate universe and backfill metadata`. Reviewer verifies owned connection isolation, current per-row atomicity, no metadata semantic change, no cached borrower close and rollback-before-release.

---

### Task 3: Corporate/dividend merge transaction owners

**Files:** Modify `scripts_import/import_corporate_actions_common.py:32-70,124-160`; extend the new coordination test module, `test_derive_splits.py`, `test_dividends_fetcher.py`.

**Interfaces:** Preserve `CorporateActionRow`, `DividendRow`, `merge_into_corporate_actions(db_path, rows) -> int`, `merge_into_dividends(db_path, rows) -> int`. No helper commit or public signature change.

- [ ] **Step 1: RED for both real merge owners.** Reuse the tracing factory with a fresh real file DB. Corporate input: `CorporateActionRow("FCOORD", "split", date(2024, 5, 15), 2.0, 0.0, source="derived:fixture")`. Dividend input: `DividendRow(figi="FCOORD", ex_date="2024-06-15", period_year=2024, amount_per_share=10.0, retrieved_at="2024-09-14T12:00:00")`. Snapshot complete rows before/after, not just function return values.

For each require exactly one correct owner acquisition around BEGIN, duplicate checks and all insert/commit/rollback operations. A repeated identical row returns 0 and leaves the stored row unchanged. Dividend `revision_n=1` plus `revision_n=2` creates both rows. Corporate same-PK input with a different factor remains skipped, not overwritten. Empty list returns 0 with no connection/lock acquisition. Two-row commit failure rolls back both rows. Add same real kernel contention and numeric SQLite BUSY tests before changing code.

Run `-k 'corporate_merge or dividend_merge'`; expected RED: new merge owners do not acquire. No mocked merge return proves the transaction.

- [ ] **Step 2: GREEN corporate merge.** Normalize each ex_date and assemble existing seven-field INSERT parameters outside flock. Keep duplicate PK checks inside the transaction because unlocked checks race. Preserve source duplicate-skip behavior at lines 45-51. Open/close the function-owned connection outside; begin, full list loop and commit inside one acquisition with exact identity `corporate-actions/corporate-actions`. Put the transaction's BEGIN inside try. Use this failure block before leaving flock:

```python
except BaseException as exc:
    try:
        conn.rollback()
    except BaseException:
        pass
    if is_sqlite_busy(exc):
        raise WriterLockBusy(
            role="corporate-actions", phase="corporate-actions",
            database_path=str(writer_lock_path(db_path))[:-len(".writer.lock")],
            lock_path=str(writer_lock_path(db_path)),
            timeout_seconds=30.0, reason="sqlite-busy",
        ) from exc
    raise
```

Do not convert current SELECT/skip/INSERT semantics into replacement semantics because the old docstring says “replacing”. Keep `written` as actual insert count.

- [ ] **Step 3: GREEN dividend merge.** Construct all current 22-field parameter tuples before flock. Under `dividends/dividends`, BEGIN then the current PK check and INSERT loop then commit. This complete row-list transaction remains independent from queue/dequeue and corporate merge. Use this exact error block under flock:

```python
except BaseException as exc:
    try:
        conn.rollback()
    except BaseException:
        pass
    if is_sqlite_busy(exc):
        raise WriterLockBusy(
            role="dividends", phase="dividends",
            database_path=str(writer_lock_path(db_path))[:-len(".writer.lock")],
            lock_path=str(writer_lock_path(db_path)),
            timeout_seconds=30.0, reason="sqlite-busy",
        ) from exc
    raise
```

No changing revision numbering, dates, monetary conversion, schema or conflict policy. No queue writer import/acquisition. The owned connection closes only after release.

- [ ] **Step 4: GREEN regressions and commit.** Run both owner cases, offline derivation idempotency tests and all dividend mapping/merge/throttle tests. Keep the existing scripts_import coverage omission unchanged, while testing these owners directly. Commit `feat(ingestion): coordinate reference data merges`; independent review checks full-list rollback, exact row counts, preparation boundary and no queue lock.

---

### Task 4: Worker-owned corporate adjustment, not a whole-phase lock

**Files:** Modify `apps/api/worker.py:807-828`; extend coordination module, `tests/test_forward_adjustment.py` and `tests/test_worker_daily_chain.py` if needed. `data_quality/forward_adjustment.py` stays unchanged.

**Interfaces:** Consume existing `derive_splits.run_derivation(db_path) -> int` and `apply_forward_split(conn, figi, ex_date, factor) -> int`. Preserve `_step_corporate_actions(db_path) -> tuple[bool, str]` and legacy `apply_all_pending(conn)` borrower behavior. No invented preparation API.

- [ ] **Step 1: RED for the real daily adjustment owner.** Use fixture bars/action above. For transaction ordering tests only, monkeypatch `derive_splits.run_derivation` to return 0 so traced writes represent adjustment alone. For end-to-end boundary tests keep derivation real and use two discontinuous bars with equal volume. Assert merge release precedes adjustment acquisition, and real `bars_adjusted` values, not mock calls, show the adjustment.

Instrument preparation SELECT/date/float events and prove they precede adjustment acquisition. `apply_forward_split` and `_already_applied` remain unmocked; assert exactly one outer adjustment acquisition, no helper reacquisition, one BEGIN, one commit, and open worker connection during borrower calls. Inject SQL/commit/interruption failure and verify rollback precedes release. Derivation already committed stays committed; real bars remain intact.

Existing `apply_all_pending` tests continue to run on borrower connections with their existing chronological/forward/idempotency behavior. Do not require borrower commits or close. New test fails because current worker owns no flock and has no explicit rollback path.

- [ ] **Step 2: GREEN by replacing the worker adjustment block only.** Keep derive import/call before owned connection and lock. Replace `apply_all_pending(conn)` with preparation from the existing helper's exact SELECT, then use the existing borrower:

```python
from algotrader_api.data_quality.forward_adjustment import apply_forward_split
from algotrader_api.ingestion.writer_lock import (
    WriterLockBusy, format_busy_defer, is_sqlite_busy, writer_lock, writer_lock_path,
)

written = derive_splits.run_derivation(db_path)
conn = sqlite3.connect(db_path)
try:
    selected = conn.execute(
        "SELECT figi, ex_date, factor FROM corporate_actions "
        "WHERE action_type = 'split' ORDER BY ex_date"
    ).fetchall()
    prepared = [
        (figi, date.fromisoformat(ex_date) if isinstance(ex_date, str) else ex_date,
         float(factor))
        for figi, ex_date, factor in selected
    ]
    adjusted = 0
    with writer_lock(db_path, role="corporate-actions", phase="adjusted-bars"):
        try:
            conn.execute("BEGIN IMMEDIATE")
            for figi, ex_date, factor in prepared:
                adjusted += apply_forward_split(conn, figi, ex_date, factor)
            conn.commit()
        except BaseException as exc:
            try:
                conn.rollback()
            except BaseException:
                pass
            if is_sqlite_busy(exc):
                raise WriterLockBusy(
                    role="corporate-actions", phase="adjusted-bars",
                    database_path=str(writer_lock_path(db_path))[:-len(".writer.lock")],
                    lock_path=str(writer_lock_path(db_path)),
                    timeout_seconds=30.0, reason="sqlite-busy",
                ) from exc
            raise
finally:
    conn.close()
```

Bind acquisition symbols at worker module scope so test instrumentation can patch the actual symbol called; keep `derive_splits` import/call outside flock. This is a replacement inside the existing outer try/return structure, not a new top-level execution block. Keep `_already_applied` inside borrower transaction; it is a consistency check. Arithmetic SQL `UPDATE` stays locked. Leave heavy split detection/read scans outside.

If new Python-heavy adjustment calculations appear, stop and revise design rather than holding them inside this block. Do not chunk the selected events to improve lock times without a separately reviewed semantic decision.

- [ ] **Step 3: GREEN regressions, commit and review.** Run actual worker adjustment cases, `test_forward_adjustment.py`, offline `test_derive_splits.py`, daily chain tests and inventory. Commit `feat(worker): coordinate corporate adjustment transaction`. Reviewer checks no lock over derivation, no nested acquisition, borrowed-helper lifetime, adjustment atomicity and retained idempotency semantics.

---

### Task 5: Numeric BUSY and fail-closed daily/CLI outcomes

**Files:** Modify worker step adapters at `440-454,807-855`; existing derivation CLI at `scripts/derive_splits.py:60-64`; dividend package `main:273-292`; extend coordination module and diagnostic tests. Do not alter core scheduling or evidence/listed-till code.

**Interfaces:** New owners raise existing `WriterLockBusy`; formatter stays `format_busy_defer(exc) -> str`; daily adapters return `(False, detail)`; existing auxiliary CLIs return 75. Non-BUSY failures retain current handling.

- [ ] **Step 1: RED with real SQLite contention, not only mocked busy exceptions.** In each seven-owner case hold `BEGIN IMMEDIATE` on an independent saved-real connection that deliberately does **not** acquire flock. Candidate connects with short test-only SQLite timeout; candidate acquisition succeeds but BEGIN encounters real numeric SQLite BUSY. Assert rollback event under held flock, release after rollback, full row state unchanged, exact role/phase and `reason="sqlite-busy"`. Release the independent SQLite transaction and retry successfully.

For commit errors inject `sqlite3.OperationalError` carrying code 5 and extended BUSY code 517 through Tracked.commit; label these deterministic commit injections, not live commit reproductions. Repeat with codes 1, 6 and no code: no `WriterLockBusy` translation. A text-only `"database is locked"` error without numeric code must remain original. Repeat `Interrupted(BaseException)` and rollback failure. Use existing numeric classifier tests unchanged.

- [ ] **Step 2: RED daily/CLI outcomes and no false success.** Call actual `_step_universe_sync` with offline broker methods and actual upsert, actual `_step_corporate_actions` with local derivation, actual `_step_dividends` with offline fake broker. Assert `(False, detail)` when pending write fails, exact formatter fields, no raw fixture failure payload in diagnostic. Daily chain critical/best-effort semantics and final rc are tested through existing fixtures with heartbeat/log paths temporary or stubbed, not production.

For each existing CLI, invoke its `main` via controlled argv and a temporary database; replace broker construction only with the offline fake before invoking dividend CLI. Use derivation `--no-face-value`. Assert exactly one bounded line, rc=75 and no pending rows. A failed dividend merge must not call successful dequeue; earlier committed FIGI rows remain. On a repeated successful invocation assert PK semantics and natural retry reuse, not artificial “zero new rows means failure”.

- [ ] **Step 3: GREEN adapters, with no whole-step lock.** At each daily step insert before its generic exception branch:

```python
except WriterLockBusy as exc:
    return False, format_busy_defer(exc)
```

For derivation CLI wrap only its existing `run_derivation` call and for dividend package main only its existing `fetch_and_persist` call with this adapter:

```python
except WriterLockBusy as exc:
    print(format_busy_defer(exc), file=sys.stderr)
    return 75
```

For the dividend module import only exception/formatter from the lock module; never add acquisition to `fetch_and_persist`, nested `_run`, queue/dequeue or rate limiter. This import is why the old blanket module negative must be replaced, not silently deleted. Keep existing token handling and ordinary errors untouched; tests use fixtures and never request/read a real token.

- [ ] **Step 4: GREEN all new outcomes plus base BUSY regressions.**

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_daily_reference_writer_coordination.py \
  apps/api/tests/test_sqlite_evidence_busy_defer.py \
  apps/api/tests/test_writer_lock_diagnostics.py \
  apps/api/tests/test_worker_daily_chain.py -q -p no:cacheprovider
```

Commit `fix(worker): defer contended daily reference writes`. Reviewer verifies numeric-only translation, no duplicate/unbounded diagnostics, no checkpoint/dequeue after failure, and existing schedule/backoff unchanged.

---

### Task 6: Real first/derived concurrency and unlocked preparation

**Files:** Extend only the new coordination test module and existing boundary/domain tests.

**Interfaces:** Actual universe/metadata and corporate/dividend owners plus unchanged bar/evidence owners. Tests launch independent subprocesses **before** production critical sections; production never creates a process while held.

- [ ] **Step 1: Add interprocess owner stress, not just primitive stress.** Extend the established subprocess pattern in `test_writer_lock.py:316-363` into the new module. Each child patches the actual owner-module `writer_lock` symbol with an observer delegating to the real primitive. Record monotonic acquisition/release intervals to that child's unique JSON file after it exits its lock. Child A invokes actual `universe.upsert_instruments`; child B invokes actual `merge_into_corporate_actions`; run additional pair `BackfillRunner._upsert_metadata` with `merge_into_dividends`. Use identical temporary DB and unique real fixture PKs. No test uses production settings/path.

The complete child observer and peak calculation are:

```python
@contextmanager
def audited_lock(db_path, *, role, phase, **kwargs):
    with locks.writer_lock(db_path, role=role, phase=phase, timeout_seconds=5):
        started = time.monotonic()
        try:
            yield
        finally:
            intervals.append((started, time.monotonic(), role, phase))

def measured_peak(intervals):
    points = [(start, 1) for start, end, role, phase in intervals]
    points += [(end, -1) for start, end, role, phase in intervals]
    current = peak = 0
    for timestamp, delta in sorted(points):
        current += delta
        peak = max(peak, current)
    assert current == 0
    return peak
```

Seed before launching children; build explicit child env `PATH`, candidate absolute `PYTHONPATH`, empty `PYTHONHOME`, `PYTHONDONTWRITEBYTECODE=1`, temporary data/log paths. Synchronize launch with test-owned ready/go files outside owners and bounded waits. Run repeated owner calls with distinct fixture PKs; after joins require nonempty interval lists, exact expected identities/counts, both rc=0, expected real rows, and `measured_peak(all_intervals) == 1`. Use an optional test-only barrier/probe delay in the observer to force overlap attempts; never add a delay to production or claim that observer delay proves production lock duration. Clean/reap every child in finally. Independent-DB variant must prove both enter before either releases using synchronization rather than a fragile wall-clock-only bound.

Remove either owner's lock in a local mutation experiment and verify this runtime protection test fails through missing intervals or overlapping measured occupancy/transaction-order evidence. Restore and rerun. Do not “fix” the assertion to accept peak zero.

- [ ] **Step 2: Assert preparation boundaries with actual writes.** In offline discovery, `derive_splits_for_figi`, dividend `_to_row`, fake broker methods and limiter waits, assert the observer reports no held lock and a separate file descriptor can take flock nonblocking. Candidate preparation reads/normalization finish before acquisition. Keep transaction-local duplicate and `_already_applied` checks allowed inside. Assert dividend queue/dequeue mutations run unlocked and outside the dividend owner transaction.

Deny outbound sockets in the new tests with a monkeypatched `socket.socket.connect` that raises `AssertionError("unexpected-network")`. Never run `lookup_face_values` against the network to prove the failure branch; replace `urllib.request.urlopen` with a deterministic OSError fixture in its existing test. No production network, broker token or process management is required for these tests.

- [ ] **Step 3: Run unchanged coordination/data regressions.**

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_daily_reference_writer_coordination.py \
  apps/api/tests/test_writer_lock.py apps/api/tests/test_writer_lock_diagnostics.py \
  apps/api/tests/test_writer_inventory.py apps/api/tests/test_bars_sqlite.py \
  apps/api/tests/test_bars_sqlite_reconcile_defer.py \
  apps/api/tests/test_moex_no_trade_evidence.py \
  apps/api/tests/test_sqlite_evidence_busy_defer.py \
  apps/api/tests/test_backfill_no_trade_evidence_lock.py \
  apps/api/tests/test_backfill_bonds_to_depth.py \
  apps/api/tests/test_backfill_bonds_client_lifecycle.py \
  apps/api/tests/test_moex_all_paths_identity.py \
  apps/api/tests/test_aux_writer_lock_outcomes.py \
  apps/api/tests/test_populate_expected_bars_lock.py apps/api/tests/test_cron_expected_bars.py \
  apps/api/tests/test_worker_daily_chain.py apps/api/tests/test_worker_daily_real_steps.py \
  apps/api/tests/test_universe_sync_chain.py apps/api/tests/ingestion/test_universe.py \
  apps/api/tests/test_forward_adjustment.py apps/api/tests/test_derive_splits.py \
  apps/api/tests/test_dividends_fetcher.py apps/api/tests/test_dividends_throttle_queue.py \
  apps/api/tests/test_dividends_throttling.py apps/api/tests/test_backfill_metadata_poison.py \
  -q -p no:cacheprovider
```

Record exact collected/pass/fail/skip counts. Compare failures against `951dee8` with the identical command and isolated configuration. No attribution from production log/DB mtime changes.

- [ ] **Step 4: Isolate then run full backend coverage gate.** Audit all tests for absolute main-checkout cwd, fixed data/log paths, online broker/MOEX requests and background process lifecycle. Replace `test_corporate_actions.py:111` cwd with `str(Path(__file__).resolve().parents[1])`; use offline error injection for the known face-value test. If other tests cannot be safely isolated in approved test-only scope, record a blocker; do not run them against production and do not call the targeted set a full pass.

Once isolation is verified, run from `apps/api` in the candidate worktree with a temporary data/log directory and outbound-network-denial test fixture enabled:

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest tests \
  --cov=algotrader_api --cov-branch --cov-report=term-missing \
  --cov-fail-under=95 -q -p no:cacheprovider
```

Record report and exit code. `fail_under = 95`, existing omissions and exclusions stay byte-identical. Fix reachable new coverage gaps with real tests, not omissions. Document full-suite blockers/baseline failures honestly. Commit `test(ingestion): verify daily writer ownership and contention`; independent reviewer reads actual owner paths, not only observer helpers.

---

### Task 7: Approval, independent review, bounded smoke and autonomous acceptance

**Files:** Update implementation evidence in change tasks only after execution; no new production feature scope.

**Interfaces:** Consumes approved exact-SHA implementation, actual tests/coverage; produces reviewed PR and operator-approved rollout evidence. This documentation task performs none of them.

- [ ] **Step 1: Self-review and strict validation.** Check every delta scenario against reachable production call paths and a named test. No placeholder, unknown API, lost canonical scenario, weakened exclusion, nested lock or telemetry side effect. Repeat both strict commands from preflight and `git diff --check`.

- [ ] **Step 2: Independent exact-SHA review and PR.** Provide base-to-HEAD diff, seven-owner inventory, acquired-role/phase map, RED/GREEN output, measured process occupancy, remaining exclusions, full-suite counts and backend coverage. Reviewer must approve spec and quality without blocking findings. Publish only authorized feature branch; verify remote head/checks. Follow AGENTS.md: operator/cron owns merge; no manual self-merge, main commit, force push or moving another worktree.

- [ ] **Step 3: Bounded zero-network smoke before production.** On a new file-backed fixture migrated by existing test helpers, execute actual public universe/metadata owner for one instrument, local derivation plus worker adjustment for one validated split, and dividend fetch with validated offline client for one FIGI plus retrospective revision. Invoke each twice and assert actual rows/counts, PK idempotency, local field preservation, expected adjusted close, lock release, no network and no telemetry locking. Bound fixture input explicitly; do not use an unbounded full historical backfill as a smoke test.

- [ ] **Step 4: Backup-first deployment, operator owned.** Confirm authorization for production reads/writes and process changes separately. Deploy only approved merged SHA. Create a restricted SQLite online backup with `sqlite3.Connection.backup`, verify its `PRAGMA integrity_check=ok`, back up/read back existing schedule/config and record previous/candidate SHAs. Provide exact restore/rollback scope and do not alter untracked operator files, scheduler topology, heartbeat frequency or excluded telemetry transactions. Never replace a live SQLite DB by copying only its main file without WAL-aware backup.

- [ ] **Step 5: Observe actual pipeline, not a synthetic readiness report.** Observe complete scheduled `first` and `derived` work, then a subsequent natural retry/resume cycle without manual lock sequencing. Record transaction deferral fields, failed phase status, committed-row continuity, actual current-session bar freshness, cycle age, independent DB integrity result and ML-ready percentage/failed cohorts from existing readiness code. A derived cycle may overlap first; do not claim cross-process global phase ordering not enforced by the current scheduler.

- [ ] **Step 6: Decide capability and standing-goal acceptance separately.** Capability requires seven-owner protection plus all retained regressions and explicit exclusion proofs. Standing goal requires measured ML-ready coverage at least 95%, freshness at most 4 hours and observed autonomous daily retry/resume. Neither Python test coverage nor successful fixture smoke proves ML readiness. If excluded telemetry still causes BUSY, retain deferral and report attribution as a separate proposed follow-up, not an automatic global lock fix. Apply/archive this change only after approved implementation and verified results; preserve the separate evidence-BUSY delta workflow.
