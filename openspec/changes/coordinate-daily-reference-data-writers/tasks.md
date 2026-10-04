# Tasks

This is an unimplemented proposal. The documentation branch may be committed locally after validation. Implementation, publication, canonical update/archive, deployment, and production access require separate approval.

## 1. Specification and baseline

- [ ] 1.1 Review and approve `proposal.md`, `design.md`, complete MODIFIED delta, and linked implementation plan.
- [ ] 1.2 Repeat strict delta/canonical validation and record baseline targeted and isolated full-suite results against `951dee8`.
- [ ] 1.3 Verify all regression subprocesses use the candidate worktree, temporary data/log files, and denied outbound network; report unsafe tests rather than executing them on production paths.

## 2. Primitive identities and explicit inventory

- [ ] 2.1 Add exact new role/phase tests and preserve same-process non-reentrancy/thread guard, canonical path, timeout, symlink and cleanup proofs; run RED, extend only Literal/frozenset values, run GREEN.
- [ ] 2.2 Keep original inventory; introduce seven exact role/phase-qualified owner entries plus existing rowcount bar owner, qualified-method resolution and union scan for no process creation.
- [ ] 2.3 Replace only universe/dividend blanket exclusions with explicit no-acquisition function proofs; retain legacy importer, guardian, pipeline, maintenance and orchestration negatives; add SQLite helper/migration negatives.

## 3. Universe and metadata ownership

- [ ] 3.1 Write fail-closed/transaction-order tests for universe and the three BackfillRunner metadata owners before code.
- [ ] 3.2 Coordinate universe per current row transaction on a function-owned connection; keep filters, PK behavior, return count, local fields and per-row retry semantics.
- [ ] 3.3 Coordinate only `_upsert_instrument`, `_seed_metadata_for_figi`, `_upsert_metadata`; keep preparation unlocked and logging/breaker/control flow unmodified.
- [ ] 3.4 Verify real SQLite BUSY rollback-before-unlock, no mutation on flock timeout, interruption/commit failure cleanup, and metadata semantic regressions; focused commit and independent review.

## 4. Corporate and dividend ownership

- [ ] 4.1 Write RED transaction/duplicate/revision tests for both common merge owners; normalize outside and add BEGIN/DML/commit/rollback under existing lock without changing batch semantics.
- [ ] 4.2 Write RED worker adjustment boundary tests; derive/merge outside the adjustment lock, preselect/parse events unlocked, use existing `apply_forward_split` borrower inside one worker-owned transaction.
- [ ] 4.3 Keep `apply_all_pending` borrower compatibility and chronological/forward/idempotency regression tests; test failure leaves derivation/real bars intact and next normal attempt resumes.
- [ ] 4.4 Prove broker fetch, `_to_row`, limiter, throttle queue/dequeue and derivation never acquire/hold this lock; focused commit and independent review.

## 5. Deferral and concurrency gates

- [ ] 5.1 Use existing numeric SQLite BUSY classification and shared formatter for new owners; preserve other exceptions, existing timeout and base evidence/listed-till behavior.
- [ ] 5.2 Test daily `(False, bounded diagnostic)` outcomes and failed-chain status, direct existing CLI exit 75, no uncoordinated fallback, no checkpoint/dequeue after failed pending write.
- [ ] 5.3 Measure peak critical-section occupancy exactly one with actual first-side universe/metadata and derived-side corporate/dividend writers in independent processes; test independent databases and same-process contender-thread rejection separately.
- [ ] 5.4 Run targeted new tests, complete writer/evidence/identity/dry-run/bonds/daily regressions, isolated full suite and unchanged backend `fail_under = 95`; record actual counts and coverage, not predictions.

## 6. Review, PR and controlled production acceptance

- [ ] 6.1 Obtain independent exact-SHA specification and quality review; resolve blocking findings and rerun affected/full gates.
- [ ] 6.2 Publish feature PR, verify remote head and actual checks; let authorized operator/cron own merge. Do not merge your own PR directly or commit to main.
- [ ] 6.3 Before deployment obtain operator authorization, restricted SQLite online backup with integrity check, configuration/schedule backup and exact-SHA rollback instructions. No telemetry rewrites or forced production backfill.
- [ ] 6.4 Run zero-network bounded fixture smoke: one universe/metadata row, one verified split and one validated dividend/revision batch; verify counters, release and rerun idempotency.
- [ ] 6.5 Observe actual scheduled complete first/derived cycle and a subsequent natural retry/resume cycle; record deferrals, freshness, failed cohorts and ML-ready percentage.
- [ ] 6.6 Report capability success separately from standing goal (ML-ready coverage at least 95%, freshness at most 4 hours, observed autonomous daily retry/resume). Never report all SQLite writes/BUSY eliminated; apply/archive only after approved implementation and evidence.
