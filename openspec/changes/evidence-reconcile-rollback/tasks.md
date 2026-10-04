## 1. Narrow contract and plan

- [x] Read canonical writer-coordination requirements and intentional exclusions; record the source-confirmed defect without attributing a production trigger.
- [x] Strict-validate this delta before adding tests or changing source.

## 2. TDD rollback and BUSY deferral

- [x] Add file-backed real-DELETE commit-failure tests for numeric BUSY 5/517, other SQLite errors, and `BaseException`; verify RED against base `951dee8`.
- [x] Verify rollback while actual flock is held, transaction closure before unlock, borrowed cached connection reuse, and unchanged committed evidence/bar state after failure.
- [x] Exercise real SQL BUSY, original-error preservation if rollback fails, and both public bar writer hooks without losing committed bars.
- [x] Move commit into the existing protected section; catch `BaseException` and reuse numeric BUSY conversion with `evidence-reconcile` / `reconcile` metadata.

## 3. Verification and handoff

- [x] Run reconcile/evidence/bar-writer tests and bounded historical CLI smoke against temporary databases using sanitized API Python. Narrow regression: 281 passed, 2 deselected; smoke: 3 passed. Each run reports 2 existing deprecation warnings, 0 failures/errors/skips. The two deselected legacy tests execute production-checkout scripts and require parent isolation, not removal or a skip added to source.
- [x] Apply the clarified requirement to the canonical writer-coordination spec and strict-validate both spec and delta.
- [ ] Review the exact diff, run local safety checks, commit only source/tests/spec/docs, and write `.superpowers/sdd/evidence-reconcile-rollback/task-1-report.md` with counts and SHA.
- [ ] Parent performs independent review, full namespace regression, PR/merge, and safe deployment; this leaf does not perform those operations.
