# Tasks

## 1. Implement and verify role-isolated supervision
- [ ] Write a complete implementation plan from this delta and verified source.
- [ ] Add bounded RED tests for same-DB/different-role PID paths, unlink isolation, own-heartbeat freshness, stale-own/fresh-sibling, startup grace, old PID history, invalid target/PPID/reused identity, DB/timestamp errors and descriptor cleanup.
- [ ] Add candidate-relative executable-order startup tests and clean first/derived launch contracts.
- [ ] Implement the smallest stdlib-only role isolation, safe signal path and boot correction.
- [ ] Run targeted tests and meaningful regressions with actual exit/JUnit evidence; retain warnings.
- [ ] Obtain independent spec/quality review, then whole-branch review.

## 2. Release by parent only
- [ ] Publish exact reviewed head; required exact-head CI passes before merge.
- [ ] Restricted online backup/integrity and unchanged scheduler snapshot before rollout.
- [ ] Activate supervisors only through a separately verified safe transition; never stop healthy writers for testing.
- [ ] Read back exact process ownership, role PID files, health, deployed smoke and unchanged cron.
- [ ] Merge canonical requirement without replacing existing requirements; archive only after verified release evidence.
