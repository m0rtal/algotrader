# Market-Data Writer Coordination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serialize concurrent market-data SQLite mutation sections without holding a lock during network work or process lifetime.

**Architecture:** A stdlib `fcntl.flock` context manager derives one `<db>.writer.lock` namespace. Public writers acquire once and delegate to private transaction helpers; bar writes, evidence writes, `listed_till`, and expected-bars batches are coordinated while fetch/compute/sleep stays unlocked. Auxiliary CLIs report temporary contention as exit code 75.

**Tech Stack:** Python 3.11 stdlib (`fcntl`, `os`, `pathlib`, `threading`, `time`, `sqlite3`), Bash, pytest, SQLite WAL.

**Spec:** `openspec/specs/writer-coordination/spec.md`; approved design archive: `openspec/changes/archive/2026-10-03-unified-writer-coordination/`.

**Command root:** Run every command below from the repository worktree root. The API interpreter is `apps/api/.venv/bin/python` (a worktree-local symlink to the already-provisioned project venv). The implementation base before the plan commit is `d94150257a09e81e0ba1cb0f904027a6208b6411`; compare regressions against its parent implementation commit `6b9b23ed3bbfe739c638ec3fff3b24bcebefd604` with the identical command.

**Preflight (already verified once; repeat before Task 1):**

```bash
test "$(git branch --show-current)" = fix/unified-writer-coordination
openspec validate writer-coordination --type spec --strict --no-interactive
git diff --check
```

## Global Constraints

- Work only on `fix/unified-writer-coordination`; never commit or push `main`.
- TDD is mandatory: run each named RED test and confirm the expected failure before production code.
- Use the project Python with `PYTHONPATH` and `PYTHONHOME` unset.
- No new dependency.
- Default shared-lock timeout is exactly 30 seconds; tests inject shorter values.
- Use only `LOCK_EX | LOCK_NB`; never expose `LOCK_SH`.
- A lock timeout never authorizes an uncoordinated write.
- Never hold the shared lock across network fetch, calculation, sleep, UI snapshot generation, or process lifetime.
- Dry-run remains read-only for foreign bars, same-day catch-up, and no-trade evidence.
- Preserve SECID/BOARDID/ISIN identity gates and real-bar-over-evidence semantics.
- Do not add class filters or fabricate market bars.
- Tests use file-backed temporary SQLite databases for every lock assertion; never production DB or fixed production logs.
- Do not read, print, commit, or summarize secrets.
- Each task ends in one focused commit and an independent spec+quality review before the next task.

---

### Task 1: Baseline Repair and Shared Lock Primitive

**Files:**
- Create: `apps/api/src/algotrader_api/ingestion/writer_lock.py`
- Create: `apps/api/tests/test_writer_lock.py`
- Create: `apps/api/tests/test_writer_inventory.py`
- Modify: `apps/api/tests/test_worker_daily_chain.py`

**Interfaces:**
- Produces: `writer_lock_path(db_path: str | Path) -> Path`.
- Produces: `writer_lock(db_path, *, role: WriterRole, phase: WriterPhase, timeout_seconds: float = 30.0)` context manager.
- Produces: `WriterLockError`, `WriterLockBusy`, `WriterLockReentrant`, and `assert_process_creation_allowed() -> None`.
- Produces `WriterRole`/`WriterPhase` as `Literal[...]` aliases plus runtime frozensets that reject values outside roles `bar-writer|foreign-bars|same-day|no-trade-evidence|expected-bars|evidence-reconcile` and phases `bars|listed-till|evidence|expected-bars|reconcile`. Call sites continue passing these exact strings; no Enum conversion layer.
- Later tasks consume these exact symbols.

- [ ] **Step 1: Repair only the deployed-call test doubles**

Re-grep the whole file for `backfill_from_moex.side_effect`. Change the only two coroutine side effects that currently accept no arguments to:

```python
async def _backfill_from_moex(*, recent_tail_days: int):
    assert recent_tail_days == 5
    # retain the existing test body
```

Run:

```bash
env -u PYTHONPATH -u PYTHONHOME apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_worker_daily_chain.py::test_step_backfill_moex_ok \
  apps/api/tests/test_worker_daily_chain.py::test_step_backfill_moex_shrinks_on_db_via_runner -q
```

Expected: PASS; production `worker.py` unchanged.

- [ ] **Step 2: Write lock-path and validation RED tests**

Tests assert:

```python
assert writer_lock_path(tmp_path / "a.db") == Path(f"{tmp_path / 'a.db'}.writer.lock")
assert writer_lock_path(tmp_path / "a.db") != writer_lock_path(tmp_path / "b.db")
```

Also assert an over-`NAME_MAX` basename and an existing symlink lock path raise `WriterLockError` before the body runs. Use `pytest.mark.skipif` only when `os.O_NOFOLLOW` is genuinely unavailable; on Linux the symlink test must execute and prove `os.open(..., O_NOFOLLOW)` rejects it.

Run `apps/api/tests/test_writer_lock.py`; expected RED because module/symbols do not exist.

- [ ] **Step 3: Write ownership RED tests**

Use file-backed temp DB paths and real subprocesses. Cover:

```python
with writer_lock(db, role="bar-writer", phase="bars", timeout_seconds=0.2):
    with pytest.raises(WriterLockReentrant):
        with writer_lock(db, role="bar-writer", phase="bars", timeout_seconds=0):
            pass
```

Add normal release, body-exception release, and a two-subprocess shared counter where measured peak occupancy equals one. Add a loser process with timeout `0.05`, assert `WriterLockBusy`, and assert its mutation sentinel remains absent. Run two simultaneous holders against two distinct DB paths and prove both acquire without waiting for the other.

Test explicit `LOCK_UN` after a forked child inherits the descriptor: parent leaves context while child remains alive, then a third process acquires successfully. `assert_process_creation_allowed()` must raise while the process-local guard is held and pass outside it. The helper does not monkeypatch `os.fork`; relevant production critical sections are statically audited in Step 5 and must call the assertion before any future process creation.

Run focused tests; expected RED for missing behavior.

- [ ] **Step 4: Implement minimal lock helper**

Required shape:

```python
@contextmanager
def writer_lock(db_path, *, role, phase, timeout_seconds=30.0):
    lock_path = writer_lock_path(db_path)
    # validate enum/path; os.open(O_CREAT|O_RDWR|O_NOFOLLOW, 0o600)
    # reject process-local nested acquisition keyed by canonical path
    # retry flock(fd, LOCK_EX|LOCK_NB) using time.monotonic()
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        # clear process-local guard even when body/cleanup raises
```

Define `WriterRole`/`WriterPhase` with `typing.Literal` and validate against immutable runtime sets before opening the lock. Raise `WriterLockReentrant` for same-process same-path nesting and `WriterLockBusy` only for bounded kernel-lock contention. `WriterLockBusy` carries bounded safe role/phase/PID/database-path/lock-path/timeout/reason/result fields for the CLI to render; sanitize control characters and cap field lengths. Do not create a background thread or log successful acquisitions.

No JSON lock-file payload, stale timeout bypass, signal, daemon, lease table, or global monkeypatch.

- [ ] **Step 5: Add fail-closed writer inventory test**

`test_writer_inventory.py` parses source with `ast` and asserts the approved in-scope paths remain represented: `replace_bars_for_figi`, `_async_backfill_impl`, `record_no_trade_evidence`, `reconcile_no_trade_evidence`, `listed_till`, `expected_bars`, and `cron_expected_bars.sh`. It must fail if executable code in `_async_backfill_impl` still calls `execute`/`executemany` with an `INSERT INTO bars` SQL constant after Task 2; comments and docstrings do not count.

For Task 1, mark only that raw-path test with `pytest.mark.xfail(strict=True, reason="Task 2 removes raw bar INSERT")`; Task 2 removes the marker. Add AST assertions that no `os.fork`, `subprocess`, or `multiprocessing` call occurs inside any `with writer_lock(...)` body. Add negative assertions that the explicit out-of-scope pipeline/heartbeat/guardian/corporate-action/dividend/maintenance/universe/startup-repair/circuit-breaker modules do not import or call `writer_lock`.

- [ ] **Step 6: Verify and commit**

```bash
env -u PYTHONPATH -u PYTHONHOME apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_writer_lock.py apps/api/tests/test_writer_inventory.py \
  apps/api/tests/test_worker_daily_chain.py -q
git diff --check
git branch --show-current
git add apps/api/src/algotrader_api/ingestion/writer_lock.py \
  apps/api/tests/test_writer_lock.py apps/api/tests/test_writer_inventory.py \
  apps/api/tests/test_worker_daily_chain.py
git commit -m "feat(ingestion): add shared writer lock primitive"
```

Reviewer passes only if spec compliance ✅ and task quality approved; no Critical/Important findings.

---

### Task 2: Common and Raw Bar Writers

**Files:**
- Modify: `apps/api/src/algotrader_api/db/bars_sqlite.py`
- Modify: `apps/api/src/algotrader_api/ingestion/backfill.py`
- Modify: `apps/api/tests/test_bars_sqlite.py`
- Modify: `apps/api/tests/test_backfill_bonds_to_depth.py`
- Modify: `apps/api/tests/test_writer_inventory.py`

**Interfaces:**
- Consumes Task 1 `writer_lock` API.
- Produces private `_replace_bars_for_figi_tx(conn, figi, rows, *, replace, source) -> int`, called only while lock is held.
- Preserves public `replace_bars_for_figi(...)` signature and return value.
- Binding for Task 3: public bar lock must be released before calling public `reconcile_no_trade_evidence(..., db_path=sqlite_path)`.

- [ ] **Step 1: RED — fail closed and rollback release**

Hold `<db>.writer.lock` in a second process, call `replace_bars_for_figi` with an injected short lock timeout through a test-only supported keyword or monkeypatched module default, assert `WriterLockBusy`, zero new bars, and unchanged metadata. Force SQL failure after `BEGIN IMMEDIATE`, then assert rollback and later lock acquisition.

- [ ] **Step 2: RED — lock boundary**

Assert candle normalization runs before acquisition and `maybe_refresh` runs after release. At both points a second process must acquire the lock immediately.

- [ ] **Step 3: GREEN — split public/private bar transaction**

Normalize rows first. Public function acquires `role="bar-writer", phase="bars"`, then private helper performs `BEGIN IMMEDIATE`, delete/insert, metadata update, commit/rollback. Retain reconciliation and `maybe_refresh` in the same public function but execute both only after the `with writer_lock(...)` block has exited. Reconciliation contention is deferred without undoing the committed bar; snapshot refresh retains its cache-only failure isolation.

Do not wrap caller loops or network requests.

- [ ] **Step 4: RED/GREEN — remove raw bond INSERT**

In `_async_backfill_impl`, collect missing candles for one FIGI and call:

```python
added = replace_bars_for_figi(
    sqlite_path,
    figi,
    missing_candles,
    replace=False,
    source="tinkoff",
)
```

Resolve `sqlite_path` explicitly from settings when production creates the connection; when a connection is injected, obtain its file-backed main database path from `PRAGMA database_list` and fail loudly for `:memory:` on this production writer path. Existing bond-depth fixtures are already file-backed; add a targeted `:memory:` rejection test. No raw `INSERT INTO bars` remains in `_async_backfill_impl`.

Add a per-FIGI fail-closed test: FIGI A times out while another process holds the lock and leaves bars/metadata unchanged; after release FIGI B acquires and commits. Retest the existing SECID/BOARDID/ISIN mismatch suite to prove coordination does not weaken identity rejection.

- [ ] **Step 5: Verify and commit**

```bash
env -u PYTHONPATH -u PYTHONHOME apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_bars_sqlite.py apps/api/tests/test_backfill_bonds_to_depth.py \
  apps/api/tests/test_backfill.py apps/api/tests/test_writer_inventory.py \
  apps/api/tests/test_moex_all_paths_identity.py -q
git diff --check
git branch --show-current
git add apps/api/src/algotrader_api/db/bars_sqlite.py \
  apps/api/src/algotrader_api/ingestion/backfill.py \
  apps/api/tests/test_bars_sqlite.py apps/api/tests/test_backfill_bonds_to_depth.py \
  apps/api/tests/test_writer_inventory.py
git commit -m "feat(ingestion): coordinate bar write sections"
```

Reviewer verifies per-FIGI release, raw-path removal, identity behavior unchanged, and reconciliation runs outside the bar lock.

---

### Task 3: Evidence Record, Reconciliation, and Historical Evidence CLI

**Files:**
- Modify: `apps/api/src/algotrader_api/ingestion/no_trade_evidence.py`
- Modify: `apps/api/src/algotrader_api/db/bars_sqlite.py`
- Modify: `apps/api/src/algotrader_api/ingestion/backfill.py`
- Modify: `apps/api/scripts/backfill_no_trade_evidence.py`
- Modify: `apps/api/tests/test_moex_no_trade_evidence.py`
- Create: `apps/api/tests/test_backfill_no_trade_evidence_lock.py`

**Interfaces:**
- Public `record_no_trade_evidence(conn, *, db_path: str, figi, rows, board, isin, now=None) -> int` acquires once.
- Private `_record_no_trade_evidence_tx(conn, *, figi, rows, board, isin, now=None) -> int` mutates without commit or reacquire.
- Public `reconcile_no_trade_evidence(conn, *, db_path: str) -> int` acquires once.
- Private `_reconcile_no_trade_evidence_tx(conn) -> int` mutates without commit or reacquire.
- All production callers pass explicit `db_path`; lock tests are file-backed.

- [ ] **Step 1: RED — evidence public/private transaction contract**

Assert public record acquires exactly once, private helper never reacquires, commit occurs before release, and error rolls back. Before refactoring, pin the existing return and semantic contract: skip rows that already have real bars, preserve recent-vs-historical expiry branching, preserve ON CONFLICT refresh behavior, return the number of accepted parameter rows, and return reconciliation `DELETE` rowcount. Migrate existing public-wrapper tests from their `:memory:` helper to file-backed `tmp_path` databases and pass `db_path` explicitly; private SQL-helper tests may remain in-memory because they do not assert interprocess locking.

- [ ] **Step 2: RED — post-bar reconciliation is separate**

Assert bar commit and bar lock release occur before public reconciliation acquisition. While reconciliation lock is held elsewhere, write a real bar; assert the bar remains committed, evidence row remains temporarily, and deferral is observable without raising back through the successful bar write.

- [ ] **Step 3: GREEN — split and wire evidence helpers**

Move SQL into private helpers. Public wrappers acquire `no-trade-evidence/evidence` or `evidence-reconcile/reconcile`, begin transaction as required, call private helper, commit/rollback, release. `bars_sqlite` calls public reconcile only after bar lock release. Update every production caller explicitly: `ingestion/backfill.py` passes `self.db_path`, `db/bars_sqlite.py` passes its `sqlite_path`, and `scripts/backfill_no_trade_evidence.py` passes its parsed `db_path`. No optional or inferred production DB-path shim.

- [ ] **Step 4: RED/GREEN — historical evidence sections**

In `backfill_no_trade_evidence.py`, guard each `listed_till` UPDATE with its own `no-trade-evidence/listed-till` lock and transaction. Evidence persistence uses its public wrapper separately. MOEX fetch and `sleep` must occur while another process can acquire the lock.

Catch `WriterLockBusy` at CLI boundary, emit one bounded `DEFER writer-lock-busy ...` line, return 75, and never perform the pending mutation.

- [ ] **Step 5: Verify and commit**

```bash
env -u PYTHONPATH -u PYTHONHOME apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_moex_no_trade_evidence.py \
  apps/api/tests/test_backfill_no_trade_evidence_lock.py \
  apps/api/tests/test_bars_sqlite.py apps/api/tests/test_moex_all_paths_identity.py -q
git diff --check
git branch --show-current
git add apps/api/src/algotrader_api/ingestion/no_trade_evidence.py \
  apps/api/src/algotrader_api/db/bars_sqlite.py \
  apps/api/src/algotrader_api/ingestion/backfill.py \
  apps/api/scripts/backfill_no_trade_evidence.py \
  apps/api/tests/test_moex_no_trade_evidence.py \
  apps/api/tests/test_backfill_no_trade_evidence_lock.py
git commit -m "feat(ingestion): coordinate evidence write sections"
```

Reviewer verifies real-bar-wins, non-nested acquisition, explicit DB path, and no network/sleep under lock.

---

### Task 4: Atomic Expected-Bars Batch and Wrapper Deferral

**Files:**
- Modify: `apps/api/scripts/populate_expected_bars.py`
- Modify: `apps/api/scripts/cron_expected_bars.sh`
- Modify: `apps/api/tests/test_cron_expected_bars.py`
- Create: `apps/api/tests/test_populate_expected_bars_lock.py`

**Interfaces:**
- Consumes Task 1 lock API.
- Python CLI returns 75 only for `WriterLockBusy`; existing validation failures retain current codes.
- Shell wrapper maps child 75 to log `DEFER writer-lock-busy` and wrapper exit 0 without SQLite-busy retry.

- [ ] **Step 1: RED — atomic direct invocation**

Run direct script against a file-backed test DB while another process holds the shared lock. Assert rc=75 and all `expected_bars` values unchanged. Release lock and assert one successful invocation updates all rows.

Inject failure after the first UPDATE and assert the explicit `BEGIN IMMEDIATE` batch rolls back every row.

- [ ] **Step 2: RED — compute outside, mutate inside**

Instrument the lock helper and monkeypatch `expected_business_days` with a side effect that records ordered events; assert all reads/calculations finish before the acquisition event and every UPDATE occurs before release.

- [ ] **Step 3: GREEN — one coordinated batch**

Accumulate `(expected, figi)` pairs first. Then:

```python
with writer_lock(db_path, role="expected-bars", phase="expected-bars"):
    con.execute("BEGIN IMMEDIATE")
    try:
        con.executemany(
            "UPDATE instruments SET expected_bars = ? WHERE figi = ?",
            updates,
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
```

No `--dry-run` is added.

- [ ] **Step 4: RED/GREEN — wrapper rc=75**

Extend the Python integration harness so a stub writer exits 75. Capture the child rc explicitly, branch on rc=75 before inspecting output, log `DEFER writer-lock-busy`, and exit 0. Only a non-75 child failure whose output contains `database is locked` may enter the existing three-attempt retry loop; the third SQLite-busy failure and every other non-75 failure remain rc=1.

Preserve `${DB}.expected-bars.lock` only as nonblocking wrapper-local duplicate-invocation protection. It is not the shared spec lock; the Python writer always acquires `${DB}.writer.lock` after the wrapper obtains its local lock. No in-scope writer ever acquires these in reverse order. Add a test proving the two paths are distinct and that direct Python invocation is still protected by `${DB}.writer.lock`.

- [ ] **Step 5: Verify and commit**

```bash
bash -n apps/api/scripts/cron_expected_bars.sh
env -u PYTHONPATH -u PYTHONHOME apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_cron_expected_bars.py \
  apps/api/tests/test_populate_expected_bars_lock.py -q
git diff --check
git branch --show-current
git add apps/api/scripts/populate_expected_bars.py \
  apps/api/scripts/cron_expected_bars.sh \
  apps/api/tests/test_cron_expected_bars.py \
  apps/api/tests/test_populate_expected_bars_lock.py
git commit -m "feat(ingestion): coordinate expected bars refresh"
```

Reviewer verifies direct invocation is protected, batch atomicity, and shell status mapping.

---

### Task 5: Auxiliary CLI Deferrals and Existing Dry-Runs

**Files:**
- Modify: `apps/api/scripts/backfill_foreign_bars.py`
- Modify: `apps/api/scripts/catchup_same_day.py`
- Modify: `apps/api/scripts/backfill_no_trade_evidence.py`
- Modify: relevant existing tests under `apps/api/tests/`
- Create: `apps/api/tests/test_aux_writer_lock_outcomes.py`

**Interfaces:**
- Consumes `WriterLockBusy` from Tasks 1–4.
- Each CLI emits one bounded deferral line and returns 75 when a pending mutation cannot acquire the shared lock.
- Existing `--dry-run` modes never acquire lock and remain rc=0 if their fetch/evaluation succeeds.

- [ ] **Step 1: RED — rc=75 translation**

Use one parametrized harness for foreign-bars, same-day, and no-trade-evidence CLIs. For each case, use a file-backed temporary DB and a separate lock holder; drive one candidate through fetch/evaluation to the first pending mutation. Assert rc=75, required diagnostics fields, and unchanged counters.

- [ ] **Step 2: RED — dry-run remains lock-free**

Hold shared lock for longer than the injected lock timeout and invoke each existing `--dry-run`. Assert it does not wait for the lock, returns its existing success result, and leaves bars/evidence/`listed_till` unchanged.

- [ ] **Step 3: GREEN — catch only coordination timeout at CLI boundary**

Translate `WriterLockBusy` to rc=75. Do not catch `WriterLockError` as a deferral; invalid lock configuration remains a real failure. Do not add fallback writes or suppress identity/fetch failures.

- [ ] **Step 4: Secret-safe diagnostic test**

Assert the structured deferral contains only approved role/phase/PID/db/lock/timeout/reason/result fields and does not contain test sentinel values placed in token/password/URL-userinfo inputs.

- [ ] **Step 5: Verify and commit**

```bash
env -u PYTHONPATH -u PYTHONHOME apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_aux_writer_lock_outcomes.py \
  apps/api/tests/test_catchup_same_day.py \
  apps/api/tests/test_moex_no_trade_evidence.py -q
git diff --check
git branch --show-current
git add apps/api/scripts/backfill_foreign_bars.py \
  apps/api/scripts/catchup_same_day.py \
  apps/api/scripts/backfill_no_trade_evidence.py apps/api/tests
git commit -m "feat(ingestion): defer contended auxiliary writers"
```

Reviewer verifies rc=75 only represents temporary lock contention and dry-runs do not acquire the lock.

---

### Task 6: Whole-Branch Verification and Publication

**Files:**
- Modify only files required by review findings; no new feature scope.
- Update implementation checkboxes in the archived `tasks.md` only after evidence exists.

**Interfaces:**
- Consumes all prior tasks.
- Produces exact branch SHA, test evidence, PR, reviewed merge commit, backup-first production deployment, and live smoke evidence.

- [ ] **Step 1: Run structural and focused gates**

```bash
openspec validate writer-coordination --type spec --strict --no-interactive
bash -n apps/api/scripts/cron_expected_bars.sh
git diff --check
env -u PYTHONPATH -u PYTHONHOME apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_writer_lock.py \
  apps/api/tests/test_writer_inventory.py \
  apps/api/tests/test_bars_sqlite.py \
  apps/api/tests/test_backfill_bonds_to_depth.py \
  apps/api/tests/test_moex_no_trade_evidence.py \
  apps/api/tests/test_backfill_no_trade_evidence_lock.py \
  apps/api/tests/test_cron_expected_bars.py \
  apps/api/tests/test_populate_expected_bars_lock.py \
  apps/api/tests/test_aux_writer_lock_outcomes.py -q
```

- [ ] **Step 2: Run safe regression scope**

Run with clean Python environment:

```bash
env -u PYTHONPATH -u PYTHONHOME apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_moex_all_paths_identity.py \
  apps/api/tests/test_catchup_same_day.py \
  apps/api/tests/test_moex_recent_tail_fixes.py \
  apps/api/tests/test_backfill_moex_runner.py \
  apps/api/tests/test_backfill_coverage.py \
  apps/api/tests/test_backfill_source_routing.py \
  apps/api/tests/test_issue64_backfill_coverage_bump.py \
  apps/api/tests/test_cron_liveness_check.py \
  apps/api/tests/test_sqlite_busy_timeout.py \
  apps/api/tests/test_worker_daily_chain.py -q
```

Record exact pass/fail counts. Compare any failure against the recorded base using the identical command.

- [ ] **Step 3: Final independent review**

Generate review package from merge-base to HEAD. Reviewer must inspect concurrency, transaction boundaries, no-network-under-lock, evidence semantics, exit codes, secret hygiene, and spec coverage. One fix wave maximum, then scoped re-review.

- [ ] **Step 4: Publish reviewed PR**

Verify branch and clean tracked state, push `fix/unified-writer-coordination`, open PR, verify remote head equals local SHA and actual test checks pass. Merge under standing authorization only after exact-SHA independent approval.

- [ ] **Step 5: Backup-first deployment and live smoke**

On clean merged `main`: create restricted online SQLite backup with `sqlite3.Connection.backup()`, verify `PRAGMA integrity_check`, back up/read back crontab, and deploy only reviewed merge commit. Do not touch preserved untracked operational files or enable developer/QA code cron.

Exercise actual scheduled market-data entrypoints. Verify configured supervisor topology, fresh heartbeat, explicit result/deferral records, no `database is locked` storm, sane row counts, `PRAGMA quick_check=ok`, and multiple real cron intervals without manual coordination.

- [ ] **Step 6: Measure standing goal separately**

Run the existing ML gate using the project venv. Report the measured percentage, failed cohorts, and freshness age. Writer coordination is complete only as a capability; the standing goal remains open until ML coverage is at least 95%, freshness is at most 4 hours, and daily autonomous cycles are observed without manual intervention.
