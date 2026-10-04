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
- [ ] 3.3 Coordinate only `_upsert_instrument`, `_seed_metadata_for_figi`, `_upsert_metadata`; keep preparation unlocked and logging/breaker policies unmodified. Only task 5.2's metadata-role error propagation may change runner control flow.
- [ ] 3.4 Verify real SQLite BUSY rollback-before-unlock, no mutation on flock timeout, interruption/commit failure cleanup, and metadata semantic regressions. An injected rollback-failure flag alone permits active-at-close; native close always runs in `finally`, failed handles are proven closed, and healthy idle cleanup is checked separately. Focused commit and independent review.

## 4. Corporate and dividend ownership

- [ ] 4.1 Write RED transaction/duplicate/revision tests for both common merge owners; normalize outside and add BEGIN/DML/commit/rollback under existing lock without changing batch semantics.
- [ ] 4.2 Write RED worker adjustment boundary tests; derive/merge outside the adjustment lock, preselect/parse events unlocked, use existing `apply_forward_split` borrower inside one worker-owned transaction.
- [ ] 4.3 Keep `apply_all_pending` borrower compatibility and chronological/forward/idempotency regression tests; test failure leaves derivation/real bars intact and next normal attempt resumes.
- [ ] 4.4 Prove broker fetch, `_to_row`, limiter, throttle queue/dequeue and derivation never acquire/hold this lock; focused commit and independent review.

## 5. Deferral and concurrency gates

- [ ] 5.1 Use existing numeric SQLite BUSY classification and shared formatter for new owners; preserve other exceptions, existing timeout and base evidence/listed-till behavior.
- [ ] 5.2 Test daily `(False, bounded diagnostic)` outcomes and failed-chain status, direct existing CLI exit 75, no uncoordinated fallback, no checkpoint/dequeue after failed pending write. Through real `BackfillRunner.run` discovery/per-ticker paths, `run_worker(mode)` with `mode` parameterized over `scheduled`/`manual`, and historical/trailing `_step_gap_recovery`, propagate only metadata-role `WriterLockBusy`; require final `done.status=error`, IDLE, pipeline `err`/rc=2 or failed gap outcome, client closure and retained committed rows. Retain and verify real trailing `to=to_`/historical `to=gap.to_` signatures before metadata RED; preserve other error policies.
- [ ] 5.3 Measure peak critical-section occupancy exactly one with actual first-side universe/metadata and derived-side corporate/dividend writers in independent processes; test independent databases and same-process contender-thread rejection separately.
- [ ] 5.4 Run targeted new tests, complete writer/evidence/identity/dry-run/bonds/daily regressions, isolated full suite and unchanged backend `fail_under = 95`; record actual counts and coverage, not predictions.

## 6. Review, PR and controlled production acceptance

- [ ] 6.1 Obtain independent exact-SHA specification and quality review; resolve blocking findings and rerun affected/full gates.
- [ ] 6.2 Publish feature PR, verify remote head and actual checks; let authorized operator/cron own merge. Do not merge your own PR directly or commit to main.
- [ ] 6.3 Before deployment obtain operator authorization, restricted SQLite online backup with integrity check, configuration/schedule backup and exact-SHA rollback instructions. No telemetry rewrites or forced production backfill.
- [ ] 6.4 Run zero-network bounded fixture smoke: one universe/metadata row, one verified split and one FIGI fetched through the unchanged mapper at `revision_n=1`. Separately call real `merge_into_dividends` with explicit `DividendRow(revision_n=2)` for that same event; verify both stored revisions, exact counters, release and rerun idempotency. Do not change fetch revision mapping.
- [ ] 6.5 Observe one full actual scheduled daily cycle containing all first/derived phases, then seven consecutive days of complete autonomous daily cycles with natural retry/resume and no manual restart, pause or forced backfill. Record per-phase completion/deferrals, committed rows, per-cohort freshness, failed FIGIs/cohorts and ML-ready ready/total counts each day. Missing days/manual intervention require a new qualifying window; two observations are not proof.
- [ ] 6.6 Report capability success separately from the hard standing goal: all 6.5 evidence plus ML-ready coverage at least 95% and freshness at most 4 hours for every required cohort, using the complete canonical FIGI population and unchanged positive cached `expected_bars` denominator/session rules. Unknown/nonpositive denominator or missing evidence never counts as ready or disappears from the population; a passing global average alone is insufficient. Never report all SQLite writes/BUSY eliminated or this expansion necessary for the parent-reported now-passing smoke; apply/archive only after approved implementation and evidence, preserving current main's appended canonical requirement when reconciled.
