# Historical gap error status — verification and release evidence

## Scope and exact source

- Verified pre-fix production baseline: `2298a11413fd0eac12cf82b405b6ec09b5193389`. After the scoped PR #191 rollout, the production checkout fast-forwarded to merge commit `989afa83e5bfb78da778294a971c3b3edc677b9c`.
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
- The first delegated whole-branch response said Approved but its trace did not show reads of the emitter or warning log it claimed to audit; those claims were not accepted. A separate source-complete read-only review was run with explicitly pinned `gpt-6-luna` / `openai-codex` after supplying the full branch diff, current old/new canonical clause, actual worker/runner emitter, unchanged rate-limit and dividend-bridge sources, warning excerpt and release evidence. The model usage report confirms the configured model/provider; the review returned `VERDICT: SPEC ✅ + Approved`, found no Critical/Important merge blockers, and did not rerun tests. It verified the malformed-event seam is observer-focused rather than a production emission failure and judged the `aiolimiter` cross-loop warning nonblocking for this isolated worker change while requiring it remain visible; baseline history and production impact remain unproven. This review predates the release. PR #191, merge, exact production SHA and post-deployment evidence are recorded below.
- The final source-complete review confirmed that `BackfillEvent` is constructed at line 132 of the new regression test. The event observer is directly injected for malformed-identity controls so exceptions from a faulty observer are not swallowed by the real runner wrapper; normal runner error events traverse `_emit`, which catches sink failures. Reviewer disposition: deliberate observer-focused test seam, not a merge blocker.

## PR #191 production release and bounded smoke

- PR #191: https://github.com/m0rtal/algotrader/pull/191. Reviewed source head `0dd34e8e7fc8701b54cf3b75b79c96a1243e3c59`; GitHub `check` passed for that exact head. The PR was merged using the matching-head guard; GitHub readback reports merge commit `989afa83e5bfb78da778294a971c3b3edc677b9c`. The production checkout fast-forwarded from the verified pre-fix baseline to exactly this merge commit.
- Before rollout, `state.db` was copied with SQLite's online-backup API to `apps/api/data/backups/state-pr191-before-rollout-20261004.db`. Full `PRAGMA integrity_check` returned `ok`; backup mode is `0600` and its parent backup directory is `0700`.
- The 20-entry user crontab was snapshotted privately to `apps/api/data/backups/cron-pr191-before-rollout-20261004.snapshot` (mode `0600`). Exact byte readback after rollout matched; no schedules or supervisors were edited.
- Only the two verified `worker.py` children were terminated sequentially under their original `algotrader-supervisor.sh` parents. Both supervisors relaunched the children on the new production tree. Their final process identities were read back, each had the production `state.db` open, the worker PID file matched its last restarted child, and the API remained HTTP 200. No API process was restarted.
- Post-deploy smoke ran all five cases in `test_worker_historical_gap_status.py` and the legacy historical-error/trailing-empty case: **6 passed** in a fresh network-disabled namespace using disposable temp databases. Two existing Starlette/httpx/anyio deprecation warnings appeared; no production database was a test target.
- Immediately after rollout, a separate SQLite `mode=ro` / `PRAGMA query_only=ON` call to the pure `check_coverage(threshold=0.95)` gate measured instrument-count readiness without `build_features`, recovery, migration, or denominator/cache writes. It returned:

| Cohort | Ready | Universe | Coverage |
| ------ | ----: | -------: | -------: |
| Global | 3,585 |    3,854 |   93.02% |
| Bonds  | 1,595 |    1,661 |   96.03% |
| Shares | 1,773 |    1,921 |   92.30% |
| ETFs   |   217 |      272 |   79.78% |

- The 269 failing instruments split into 138 `incomplete`, 111 `both`, and 20 `stale`. Instrument IDs were not included in this report. Coverage remains below 95% globally, for shares, and for ETFs; causes are not inferred from the reason buckets.
- In a post-rollout, single SQLite read snapshot, the worker heartbeat was `2026-10-04 21:47:01`; the maximum `bars.ts` session date was `2026-10-03`. The heartbeat is a liveness signal and a session date is not a four-hour ingestion timestamp, so freshness **≤4 hours is not verified**.
- No seven-day uninterrupted natural-cycle observation was performed. These deployment-time worker restarts reset any prior no-intervention window; seven qualifying daily cycles **remain unverified**.
- The preexisting supervisors share a PID file keyed only by the DB path. During the sequential reload, its value followed the most recently restarted child; the final readback matched the last child. No supervisor code or schedule was changed in this PR. Independent per-supervisor PID-file isolation remains a distinct operational issue; do not infer seven-day watchdog resilience from this bounded reload.

This historical-gap status change is implemented, independently reviewed, merged, and deployed. It does **not** make the broader MOEX/ML pipeline production-ready: the global/share/ETF coverage gates, four-hour freshness evidence, and seven-day uninterrupted-cycle acceptance remain open.
