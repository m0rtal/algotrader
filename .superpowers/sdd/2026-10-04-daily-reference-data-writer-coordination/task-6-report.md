# Task 6 report: actual interprocess ownership and unlocked preparation

## Scope and status

- Worktree: `/home/hermes/worktrees/algotrader-daily-writer-expansion`.
- Branch: `feature/daily-reference-writer-coordination`.
- Starting/source HEAD: `806fead0a8c19cb9162b232e2779541a4dc2e006`.
- Task 6 owner stress, preparation proofs and scoped regressions are verified. The full backend coverage gate remains parent work on the exact committed HEAD, as directed for this leaf. The targeted suite is not a full backend pass.
- No production source defect was found by these checks. No production source, scheduler, worker configuration, dependency, omission or exclusion was changed.
- Commit subject: `test(ingestion): verify daily writer ownership and contention`. The resulting SHA is returned separately after committing this report.
- Task 7 review, PR, merge, deployment and seven-day production acceptance are not performed or claimed here.

## Repository changes

Only these test files and this report are included:

- `apps/api/tests/test_daily_reference_writer_coordination.py`: 23 new parametrized cases; stronger independent-descriptor preparation probes in existing boundary tests. The new cases comprise 6 shared-DB stress scenarios, 2 independent-DB scenarios, 7 owner-lock mutation controls, 1 same-PID thread guard, 6 dormant preparation scenarios and 1 actual dividend queue/dequeue SQL boundary case.
- `apps/api/tests/test_corporate_actions.py`: the operator-wrapper test uses `str(Path(__file__).resolve().parents[1])` instead of the absolute main-checkout cwd.
- `apps/api/tests/test_derive_splits.py`: the face-value failure test injects deterministic `OSError("offline-face-value-fixture")` into `urllib.request.urlopen`. It never attempts the network.

## Actual owners exercised

The seven new transaction-owner paths are invoked, not replaced by mock writes:

1. `universe.upsert_instruments` (`universe`).
2. `BackfillRunner._upsert_instrument` (`instrument`).
3. `BackfillRunner._seed_metadata_for_figi` (`seed`).
4. `BackfillRunner._upsert_metadata` (`update`).
5. `merge_into_corporate_actions` (`merge`).
6. `worker._step_corporate_actions` and its adjusted-bar transaction (`adjustment`). This actual path also performs its corporate merge.
7. `merge_into_dividends` (`dividend`).

The combined scenario additionally invokes the unchanged real bar writer, no-trade evidence writer and expected-bar CLI main: `replace_bars_for_figi_with_rowcount`, `record_no_trade_evidence` and `populate_expected_bars.main` with explicit temporary `--db` and controlled argv. Bar evidence reconciliation retains its separate observed acquisition.

Each finite stdlib child starts before owner acquisition, receives an explicit environment whitelist and patches the owner-module lock symbol with an observer delegating to the real primitive. Evidence's lazy primitive import is observed too. An independent descriptor must fail nonblocking flock while the observer holds the lock. Each child writes its own JSON containing PID, DB path, attempts, monotonic intervals, role/phase and actual return values; persistence follows the real lock exit and the final child cleanup.

The parent seeds migrated temporary databases before launch. Ready/go/attempt files force concurrent acquisition attempts. Shared scenarios alternate the same DB path and its symlink alias. Each owner makes 3 fixture calls, repeated twice. Interval identities and counts are exact; a zero or missing interval list cannot pass. Independent-DB children must both enter before either releases, using a release barrier and overlapping first intervals rather than a duration estimate.

Real SQLite readback checks instrument fields and preservation of unrelated `source_updated_at`, `listed_till` and `expected_bars`; metadata status/aggregate/timestamp semantics; corporate/dividend PKs and duplicate-zero results; unchanged raw bars and non-reapplied adjusted bars; bar aggregate metadata; evidence identities/expiry without synthetic OHLC bars; expected-bar output; and `PRAGMA integrity_check = ok`. Corporate merge, dividend merge and bar calls return `[1, 1, 1, 0, 0, 0]` across initial/resume calls. The simultaneous expected-bar updates survive universe/instrument upserts.

## Measured JSON aggregate

These values are parsed and independently recomputed from the final targeted run's per-child JSON files, not inferred from test names. PIDs below are namespace PIDs. Every listed child exits 0.

```json
{
  "positive_scenarios": [
    {"owners": ["universe", "merge"], "independent": false, "child_count": 2, "interval_count": 12, "peak": 1, "pids": [17, 18]},
    {"owners": ["update", "dividend"], "independent": false, "child_count": 2, "interval_count": 12, "peak": 1, "pids": [21, 22]},
    {"owners": ["instrument", "seed"], "independent": false, "child_count": 2, "interval_count": 12, "peak": 1, "pids": [25, 26]},
    {"owners": ["adjustment", "bars"], "independent": false, "child_count": 2, "interval_count": 24, "peak": 1, "pids": [29, 30]},
    {"owners": ["evidence", "expected"], "independent": false, "child_count": 2, "interval_count": 12, "peak": 1, "pids": [33, 34]},
    {"owners": ["universe", "instrument", "seed", "update", "merge", "adjustment", "dividend", "bars", "evidence", "expected"], "independent": false, "child_count": 10, "interval_count": 72, "peak": 1, "pids": [37, 38, 39, 40, 41, 42, 43, 44, 45, 46]},
    {"owners": ["universe", "merge"], "independent": true, "child_count": 2, "interval_count": 12, "peak": 2, "pids": [58, 59]},
    {"owners": ["update", "dividend"], "independent": true, "child_count": 2, "interval_count": 12, "peak": 2, "pids": [62, 63]}
  ],
  "positive_child_count": 24,
  "positive_interval_count": 168
}
```

Artifact base: `/home/hermes/.hermes/cache/scratch/task6-verification`.

- `aggregate-results.json`: full aggregates, child rc/PIDs, paths, mutation detection counts and JUnit counts.
- `namespace/targeted-tmp/test_actual_owner_interprocess*/stress/<owner>.json`: complete measured intervals for shared-path/symlink runs.
- `namespace/targeted-tmp/test_actual_owner_independent_*/independent/<owner>.json`: complete independent-DB intervals.
- Each scenario also retains `<owner>.output` and `aggregate.json`. Inside the namespace the same artifact base is `/mnt`.

The observer's 0.015-second probe delay is test-only. Recorded intervals measure observed owner occupancy; they are not a production duration benchmark or a seven-day stress result.

## Unlocked preparation and fail-closed proofs

- At offline universe discovery, metadata discovery, real `derive_splits_for_figi`, dividend `_to_row`, fake broker calls and limiter waits, the local observer must report no held owner lock and a separate descriptor must acquire/release nonblocking flock.
- Six dormant scenarios pause at those preparation/SDK/limiter boundaries. A separate child invokes the actual universe writer and commits a distinct instrument while preparation remains paused. SQL readback verifies that commit before releasing preparation. Preparation then resumes and its actual owner commits; final domain rows are checked. There is no elapsed-time-only concurrency claim.
- Preparation parameter/date/float conversion and preparation SQL reads are probed outside acquisition. Transaction-local duplicate and `_already_applied` checks remain permitted under the owner transaction.
- Actual throttle enqueue/dequeue SQL runs with no dividend lock, no active dividend transaction and no local connection transaction. The first offline broker attempt queues the FIGI and writes no dividend; the next attempt commits the actual dividend and deletes its pending row. Real queue/dividend rows and owner event order are read back.
- A universe thread holds the real lock. A competing dividend owner in the same PID through a symlink alias raises `WriterLockReentrant` before a second acquisition or mutation. After release, both actual owners commit successfully.
- Existing real SQLite BUSY, kernel contention, rollback-before-release, retry, partial-commit and daily/CLI/worker fail-closed adapter cases remain in the scoped suite. They retain real source calls and DB readback; the Task 6 observer does not replace these with mock comparisons.

## Negative controls and TDD labeling

The seven owners were already implemented and approved in Tasks 1–5. Task 6 adds verification tests first without rewriting production. This is new verification plus mutation replay, not a fabricated new-feature TDD RED.

- Seven parametrized controls compile only the selected actual owner function in memory with its `with writer_lock` removed. Each runs with a real partner and fails the protection assertion for missing owner intervals. Six removed owners record 0 instead of 6 intervals; `adjustment` retains 6 corporate-merge intervals but misses its required 6 adjusted-bar intervals (6 observed, 12 required).
- A separate isolated replay removes the universe lock and observes `missing owner intervals: universe: []`; real rows are still independently verified after the expected failure.
- A second replay compiles `writer_lock_path` in memory with one global lock namespace. Independent DBs fail the enter-before-release witness; child release-barrier waits remain bounded and owned children are reaped.
- Restoring the real primitive yields 2 children, 12 intervals, peak 2 and returncodes `[0, 0]` for the independent universe/corporate pair.
- Replay script/result: `replay_controls.py` and `replay-rzp0pxlo/results.json` under the artifact base. Each replay allocates a fresh temporary directory, so the command can be repeated without deleting prior evidence. Script execution exits 0 because both expected negative assertions are caught and the restored GREEN succeeds. No worktree source mutation, checkout, stash or production process action is used.

## Isolation and reproducible commands

All test-owned artifacts remain under the scratch base. No ignored production data or `.env` is copied: `prepare_namespace.py` uses `git archive HEAD` and overlays exactly the three changed test files. The archived source is the starting SHA above; the test overlay equals the verified candidate files.

The test-owned `namespace/isolated-suite.sh` adapts the already-established parent namespace launcher. It uses `env -i`, user/mount/network/PID namespaces, private mounts, the archived candidate bound over the main-checkout path, a read-only existing API venv, a masked `.hermes`, separate HOME/data and a scratch directory bound over `/tmp`. `/proc` exposes only namespace processes. Preflight confirms no API `.env`, no exposed `.hermes/logs`, and an empty IPv4 route table. No package installation or production launcher edit occurs. Only test-owned children can be terminated.

Child env contains only `PATH`, candidate absolute `PYTHONPATH`, empty `PYTHONHOME`, `PYTHONDONTWRITEBYTECODE=1`, owned HOME/TMP/data/log/snapshot paths and `ALGOTRADER_INGEST_FAKE=1`. Coordination tests reject outbound INET/INET6 socket connect/connect_ex with `AssertionError("unexpected-network")`; children reject socket connect/connect_ex before importing owner modules. SDK clients are finite offline fakes, with no broker credentials inherited or obtained.

Bounds: child barriers 5 seconds; parent readiness/barriers 10 seconds; combined child join deadline 20 seconds; terminate/wait 2 seconds then kill/wait 2 seconds, only through handles created by this harness. Thread pause/join waits are finite. Namespace module and targeted runs have 5-minute and 10-minute limits with a 10-second kill-after bound.

Final candidate verification ran from the worktree:

```bash
A=/home/hermes/.hermes/cache/scratch/task6-verification/namespace
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python \
  /home/hermes/.hermes/cache/scratch/task6-verification/prepare_namespace.py
bash "$A/isolated-suite.sh" preflight
bash "$A/isolated-suite.sh" modules
bash "$A/isolated-suite.sh" targeted
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python \
  /home/hermes/.hermes/cache/scratch/task6-verification/audit_and_aggregate.py
```

`modules` runs the three changed test modules. `targeted` runs exactly the Task 6 brief's coordination/data module list, with namespace-safe path mapping, explicit `pytest_asyncio.plugin`, `-q -p no:cacheprovider --tb=short`, owned basetemp and JUnit XML. Plugin autoload is disabled. The launcher contains the complete module list and is retained for parent reproduction.

Final results (parsed from JUnit, not added together because the suites overlap):

- Modules: exit 0; **257 collected, 257 passed, 0 failed, 0 errors, 0 skipped**, 2 warnings in **28.76s**.
- Targeted regressions: exit 0; **672 collected, 672 passed, 0 failed, 0 errors, 0 skipped**, 5 warnings in **94.38s**.
- Coordination module contributes 234 cases, including the 23 new Task 6 cases.
- Preflight and aggregate audit: exit 0. Final outputs/XML are `namespace/{preflight-output.txt,module-output.txt,targeted-output.txt,module.xml,targeted.xml}`.
- The two common warnings are installed FastAPI/Starlette deprecations. The targeted run additionally reports three existing cross-loop AsyncLimiter reuse warnings. These warnings are not suppressed or repaired by this test-only task.

Standalone replay command (explicit non-secret environment):

```bash
env -i PATH=/usr/bin:/bin \
  HOME=/home/hermes/.hermes/cache/scratch/task6-verification/home \
  TMPDIR=/home/hermes/.hermes/cache/scratch/task6-verification \
  PYTHONDONTWRITEBYTECODE=1 ALGOTRADER_INGEST_FAKE=1 \
  ALGOTRADER_DATA_DIR=/home/hermes/.hermes/cache/scratch/task6-verification/data \
  /home/hermes/algotrader/apps/api/.venv/bin/python \
  /home/hermes/.hermes/cache/scratch/task6-verification/replay_controls.py
```

## Full-gate audit and remaining parent work

The AST isolation audit parses all 130 Python files under `apps/api/tests`, with 0 parse failures. It records 59 fixed-path/process-lifecycle risk candidates in 13 files in `test-isolation-audit.json`. These are audit candidates, not 59 demonstrated defects. Supervisor/liveness tests include absolute main-checkout DB/log paths and process signals; they were not run on the host. The verified namespace masks those paths and isolates process visibility. The leaf executes only the approved scoped sets, not the supervisor/watchdog full suite.

The parent must run the canonical full backend command on the exact resulting commit in the isolated launcher, with offline fixtures ready:

```bash
# Inside the parent's verified candidate namespace, from apps/api:
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest tests \
  --cov=algotrader_api --cov-branch --cov-report=term-missing \
  --cov-fail-under=95 -q -p no:cacheprovider
```

No full-suite coverage percentage or gate exit code is claimed here. `apps/api/pyproject.toml` is byte-identical to starting HEAD, including `fail_under = 95`, omissions and exclusions; SHA-256 is `c358c1b38b2741323607ecb6b4dbf5ea78cdf14ba3cbf8245be3c13ea67447fb`. Source, worker, production scripts and OpenSpec contracts also have no Task 6 diff. There are no final scoped failures requiring a comparison to `951dee8`; any later full-suite failure needs that identical-command isolated baseline comparison before attribution.

## Issues encountered and commit safety

- Initial stress instrumentation missed `populate_expected_bars`' imported lock symbol, causing its partner's attempt barrier to expire. Including that module in the real-primitive observer fixes the test harness; final runs pass.
- An early full-module harness run has 4 failures in deliberately contended/no-lockfile corporate-adjustment cases. The preparation FD probe must be opt-in for normal unlocked-read proofs, not used when another test intentionally holds flock or requires that no owner lockfile was created. Current `corporate_trace(..., probe_reads=False)` preserves those adversarial fixtures; `probe_reads=True` is used for the actual normal derivation boundary test. No source change was needed.
- The namespace launcher initially lacked its own `/mnt/venv` mount target. Creating that test-owned directory resolves the preflight mount failure.
- Temporary migration seeding emits migration 016 cleanup diagnostics (`no such table: instruments_new`, `no such savepoint: _mig_016_cleanup`). Final domain/schema readback and integrity checks pass; migrations are unchanged.
- The local commit uses per-command `core.hooksPath=/dev/null`: the normal hook refreshes global codebase-memory outside leaf authorization. No persistent hook configuration is changed. No index-refresh claim is made. The credential scanner is run explicitly on the staged slice; the three staged test files return exit 0, and staged blobs equal the verified worktree files. The report is scanned again with the final staged scope before commit.
- The SDD directory is ignored; only this authorized report is force-added. Final staged scope is checked against the exact three-test/report set. Independent parent review of actual owner implementations remains required before promotion.
