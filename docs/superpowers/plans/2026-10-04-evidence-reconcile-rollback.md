# Evidence Reconciliation Rollback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. This is an already delegated leaf task; do not redelegate.

**Goal:** Restore existing reconciliation commit atomicity and numeric SQLite BUSY deferral without leaving a borrowed cached connection in a transaction after unlock.

**Architecture:** Keep the existing wrapper, private DELETE helper, shared flock, and post-commit bar hook. Protect DELETE plus commit together; roll back before unlock and reuse PR #186's numeric BUSY classifier.

**Tech Stack:** Python, stdlib SQLite/flock, installed pytest; no new dependencies.

**Spec:** `openspec/changes/evidence-reconcile-rollback/specs/writer-coordination/spec.md`; canonical `openspec/specs/writer-coordination/spec.md`.

## Global Constraints

- Role `evidence-reconcile`, phase `reconcile`, reason `sqlite-busy`.
- Preserve borrowed connections, exact non-BUSY exceptions, already-committed bars and metadata, current timeouts, and caller best-effort policy.
- Only transaction work holds the lock. No network, sleeps, new retries, production state, cron, or secrets.
- Intentional canonical exclusions remain unchanged, including universe and corporate actions.
- The actual production trigger is not proven; do not claim incident causality.
- Use `env -u PYTHONPATH -u PYTHONHOME /home/hermes/algotrader/apps/api/.venv/bin/python` from this worktree's `apps/api`; pytest's `pythonpath = ["src"]` selects worktree source. Keep runtime fixtures, test databases, and JUnit artifacts in the owned scratch directory.

### Task 1: Reconciliation failure cleanup

**Files:**

- Modify: `apps/api/src/algotrader_api/ingestion/no_trade_evidence.py`, only the reconciliation wrapper and its contract docstring.
- Create: `apps/api/tests/test_sqlite_reconcile_commit_rollback.py`.
- Modify: canonical writer-coordination spec after GREEN; this change's docs and task checklist.
- Create: `.superpowers/sdd/evidence-reconcile-rollback/task-1-report.md` (local review evidence).

**Interfaces:**

- Consumes unchanged `reconcile_no_trade_evidence(conn, *, db_path: str) -> int`, `is_sqlite_busy(exc) -> bool`, and keyword-only `WriterLockBusy` constructor.
- Produces the same removed-row count on success; qualifying BUSY errors chain into `WriterLockBusy` after rollback; other failures re-raise unchanged.

- [x] Strict-validate the delta before tests: `openspec validate evidence-reconcile-rollback --strict`.
- [x] Add a commit-failure proxy around the real cached temporary DB connection. Execute real DELETE and inject only commit errors 5, 517, 14, 6, missing code, and `BaseException`. Probe actual kernel flock in rollback and record transaction state immediately before unlock. Seed one conflicting and one independent evidence session; assert independent readback is unchanged, committed bars survive, and later reconciliation/reuse succeeds. Add actual SQL contention from a second SQLite connection and cover both public bar entrypoints with a failure on the second commit (after the successful bar commit).

```python
# Load-bearing checks after a failed reconciliation commit:
assert not cached_conn.in_transaction
assert cached_conn.execute("SELECT 1").fetchone()[0] == 1
# Another connection still sees both evidence rows and the committed real bar.
# After disarming the commit fault, reconciliation removes exactly one row.
```

- [x] Run the new test file on unchanged source and capture expected RED: missing rollback on commit failure and missing numeric BUSY conversion. Fixture or import errors are not acceptable RED.
- [x] Implement only the wrapper change:

```python
try:
    removed = _reconcile_no_trade_evidence_tx(conn)
    conn.commit()
except BaseException as exc:
    try:
        conn.rollback()
    except BaseException:
        pass  # Preserve the original failure, matching PR #186.
    if is_sqlite_busy(exc):
        raise WriterLockBusy(
            role="evidence-reconcile", phase="reconcile",
            database_path=db_path,
            lock_path=str(writer_lock_path(db_path)),
            timeout_seconds=_EVIDENCE_LOCK_TIMEOUT_SECONDS,
            reason="sqlite-busy",
        ) from exc
    raise
```

- [x] Verify GREEN for the new test file. Run existing evidence BUSY, evidence record/reconcile lock, bar writer and post-commit deferral suites, plus historical evidence completion/final-fix and bounded CLI smoke tests. Record exact counts, warnings, and failures without claiming a full namespace or coverage gate.
- [x] Apply this clarified requirement to canonical writer coordination and strict-validate delta plus canonical spec. Review exact diff and run local credential scanning; do not invoke a hook that writes the production codebase-memory index.
- [ ] Commit exact source/tests/spec/docs on `fix/evidence-reconcile-rollback`. Record SHA, RED/GREEN outputs, safe scope, and outstanding parent review/full regression/PR/deploy in the task report.
