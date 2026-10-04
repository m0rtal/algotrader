# Historical gap error status — verification and release evidence

## Scope and exact source

- Baseline and currently deployed production HEAD: `2298a11413fd0eac12cf82b405b6ec09b5193389`.
- Tested source candidate: `ca12b69d36b89896be8fea2ab0545bcc031bab73` on `fix/historical-gap-error-status`.
- Runtime diff is limited to `apps/api/worker.py`; tests add the actual historical-path matrix and update one obsolete historical-error/trailing-empty expectation. Runner/helper signatures, routing, denominator, universe, identity/evidence rules and schedules are unchanged.
- Historical errors now fail the noncritical phase, with distinct failed FIGIs counted across historical and trailing passes. Healthy empty responses remain successful; committed bars, counters, subsequent attempts, client closure and metadata contention policy remain intact.

## Executed RED and GREEN evidence

The parent independently parsed the baseline JUnit artifacts and verified the archived baseline worker bytes against the deployed baseline git blob:

- `historical-probe.xml`: 2 tests, 1 intentional RED failure, no errors/skips; the upstream-error case falsely returned phase success, while the healthy-empty control passed.
- `historical-matrix.xml`: 5 tests, 2 intentional RED failures, no errors/skips; the historical-error and mixed-result phase assertions failed, with three controls passing.
- These preserved baseline reports are under the owned scratch directory `delisting-release-parent`; the implementer's earlier RED report was overwritten by its GREEN run and is not the preserved RED evidence.
- Candidate targeted suite: 20 passed. Named regression suite: 288 passed. Reports: `historical-gap-task1/resume-20261004/targeted-20261004.xml` and `regression-20261004.xml` under the Hermes scratch directory.
- Fresh isolated full warning audit exited **0**: JUnit **1,858 tests, 0 failures, 0 errors, 8 skips**; pytest **1,849 passed, 8 skipped, 1 xpassed, 47 warnings**.
- Fresh reports: `historical-gap-task1/resume-20261004/full-warnings-audit-rerun.xml` and `full-warnings-audit-rerun-coverage.json`; console output: `historical-gap-warning-audit-rerun-full.stdout.log` under the Hermes scratch directory.
- Parent verified that archived worker/new-test/legacy-test hashes match the committed source candidate. Fixtures use disposable migrated file-backed SQLite databases with production paths and network masked; no production DB is a test target.

## Measured package coverage

| Metric                                      |        Measured result |
| ------------------------------------------- | ---------------------: |
| coverage.py combined statement/branch gate  |                 97.34% |
| Executable statements / line execution      | 97.73% (4,605 / 4,712) |
| Branches                                    | 95.81% (1,142 / 1,192) |
| Derived named-function body-execution proxy |     98.32% (351 / 357) |

The function proxy counts named function records with executable statements and at least one executed line. It is derived from coverage JSON, not a native independent function-entry counter. Combined coverage is not pure line coverage, and these measurements must not be presented as four independent native coverage metrics. No exclusions or thresholds were weakened.

## Skips, XPASS and warnings

- All eight skips have the existing `sandbox tests disabled` reason; live broker sandbox acceptance was not executed by this masked gate.
- One existing non-strict XPASS is reported for the missing-SDK initialization test; it is not an additional passing sandbox probe.
- The suite is **not warning-free**. Console warnings include framework and T-Invest SDK deprecations, plus an `aiolimiter` cross-event-loop `RuntimeWarning` associated with three unchanged dividend throttling tests. Production impact and warning baseline history are not established by this run. Do not suppress the warning or call it harmless based only on its location outside the diff.
- The first audit retry failed during setup because its launcher called the original script and reused `/mnt/pytest-full`. The corrected launcher invokes itself and uses distinct temporary/report paths; the fresh successful run above is separate evidence, not a reinterpretation of the failed setup.

## Specification compatibility and review

- Strict change and canonical validators passed before canonical application.
- The additive canonical application preserves the previous canonical file as an exact byte prefix.
- The new requirement explicitly supersedes the prior trailing-only `failed=N` scope and historical-event exclusion for the ordinary phase-wide summary. Other previous requirements remain binding.
- Scoped Task 1 spec/quality review returned `VERDICT: SPEC ✅ + Approved` with no Critical/Important findings.
- The first delegated whole-branch response said Approved but its trace did not show reads of the emitter or warning log it claimed to audit; those claims were not accepted. A separate source-complete read-only review was run with explicitly pinned `gpt-6-luna` / `openai-codex` after supplying the full branch diff, current old/new canonical clause, actual worker/runner emitter, unchanged rate-limit and dividend-bridge sources, warning excerpt and release evidence. The model usage report confirms the configured model/provider; the review returned `VERDICT: SPEC ✅ + Approved`, found no Critical/Important merge blockers, and did not rerun tests. It verified the malformed-event seam is observer-focused rather than a production emission failure and judged the `aiolimiter` cross-loop warning nonblocking for this isolated worker change while requiring it remain visible; baseline history and production impact remain unproven. This does not approve deployment; exact-head CI and the parent-owned release gates remain pending.
- The final source-complete review confirmed that `BackfillEvent` is constructed at line 132 of the new regression test. The event observer is directly injected for malformed-identity controls so exceptions from a faulty observer are not swallowed by the real runner wrapper; normal runner error events traverse `_emit`, which catches sink failures. Reviewer disposition: deliberate observer-focused test seam, not a merge blocker.

## Release gates — not yet executed

- PR publication and exact-head CI: pending.
- Merge and remote readback: pending.
- Restricted online backup, integrity verification and cron snapshot: pending.
- Exact-SHA production fast-forward and scoped affected-child reload: pending.
- Deployed-code disposable fixture smoke, health/integrity/cron readback and fresh read-only canonical readiness measurement: pending.
- Change archive: pending until the required acceptance and release evidence exist.

This document does not claim production readiness. The previous production coverage snapshot, freshness evidence and seven-day observation window are not refreshed by unit tests; seven consecutive autonomous full daily cycles without manual restart/pause/backfill remain unproven. A worker reload resets the no-intervention observation window.
