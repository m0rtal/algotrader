# Gap recovery failure status verification

## Candidate identity and scope

Implementation candidate: `2586e66b86e3f46aec0151c565cc2f44ffdb076a`, branch `fix/gap-recovery-failure-status`, worktree `/home/hermes/worktrees/algotrader-gap-recovery-status`.
Draft/whole-branch base: `ad58b28a96f15b85984a228dcdb26f34ccf515fe`. Approved implementation base: `04e2c4d77b4b6ef5754522d97ffbdeba3a0883a5`.
Worker/acceptance implementation: `9784527552f683feaeff734d00cd5c0066df643f`. Approved legacy fixture/assertion follow-up: `2586e66b86e3f46aec0151c565cc2f44ffdb076a`.

Task 2 is documentation only. Before edits, fresh comparison verified **581 copied tracked files** as `git show 2586e66:<path>` == worktree == isolated candidate. Worker bytes also equal the implementation commit `9784527`. The completed full gate therefore belongs to the exact implementation candidate, not merely this branch name. Source/tests were not edited or rerun in Task 2.

This record is written before its documentation commit; that commit's SHA is intentionally not guessed or self-embedded. Its parent is the exact implementation candidate above. The resulting documentation head is recorded after commit in the ignored `.superpowers/sdd/2026-10-04-gap-recovery-failure-status/task-2-report.md`. Parent must use the actual documentation SHA for publication and CI; implementation approval is not approval of a later documentation head.

Evidence consumed: this task's `task-1-report.md`, actual logs/XML/JSON and launcher under `/home/hermes/.hermes/cache/scratch/gap-task1-safe/`. These are local artifacts, not published CI attachments. Parent-supplied review is attributed separately below.

Verified SHA-256 identities:

- Worker: `05618f9228033a727dc5a239045a9038f0c892178587de6b57b05c71fe38c342`.
- Acceptance test: `772937553e8ef1ebf831b93a2d4b444ecc45a6fc336cc501824802711f38e6b2`.
- Coordination test: `b67d5482bfc2cac3eb05ded63773a7e3ceb577e3322ca53213d9c92bac7290bc`.
- Coverage configuration: `c358c1b38b2741323607ecb6b4dbf5ea78cdf14ba3cbf8245be3c13ea67447fb`.

## Assertion RED: command, exit status, failing assertions, counts

Actual commands below ran from the worktree root through the isolated launcher, not bare pytest on the production host:

```bash
/bin/bash /home/hermes/.hermes/cache/scratch/gap-task1-safe/isolated-suite.sh preflight
/bin/bash /home/hermes/.hermes/cache/scratch/gap-task1-safe/isolated-suite.sh red-final > /home/hermes/.hermes/cache/scratch/gap-task1-safe/red-final.log 2>&1
/bin/bash /home/hermes/.hermes/cache/scratch/gap-task1-safe/isolated-suite.sh legacy-red > /home/hermes/.hermes/cache/scratch/gap-task1-safe/fixround1-legacy-red.log 2>&1
```

Final acceptance RED used unchanged BASE worker bytes in the copied candidate: **rc=1, 10 failed, 5 passed, 2 warnings in 1.34s**. Failures assert truthful False for real runner failure, ordinary exceptions, deduplication and bar-writer timeout, and `failed=0` for successful controls. No setup/collection/network failure is credited as RED.

Earlier scaffold GREEN exposed **2 failed, 13 passed** with SQLite date-subclass binding. Only new fixture date methods were corrected to return ordinary `date`; final acceptance RED was then repeated against unchanged source. The first scaffold's controls are not substituted for final RED proof.

Legacy selection before approved fixes: **rc=1, 4 failed, 2 passed, 234 deselected, 2 warnings in 1.66s**; JUnit 6 cases, 4 failures, 0 errors. Two rerun assertions encountered `ProgrammingError: Error binding parameter 1: type 'FrozenWorkerDate' is not supported`; two trailing assertions required obsolete success. Only the named fixture and diagnostic assertions were updated. No skip/xfail markers changed.

## GREEN and focused regressions: commands, exit statuses, counts

```bash
/bin/bash /home/hermes/.hermes/cache/scratch/gap-task1-safe/isolated-suite.sh green-final > /home/hermes/.hermes/cache/scratch/gap-task1-safe/green-final.log 2>&1
/bin/bash /home/hermes/.hermes/cache/scratch/gap-task1-safe/isolated-suite.sh core > /home/hermes/.hermes/cache/scratch/gap-task1-safe/core.log 2>&1
/bin/bash /home/hermes/.hermes/cache/scratch/gap-task1-safe/isolated-suite.sh regression > /home/hermes/.hermes/cache/scratch/gap-task1-safe/fixround1-regression.log 2>&1
/bin/bash /home/hermes/.hermes/cache/scratch/gap-task1-safe/isolated-suite.sh regression > /home/hermes/.hermes/cache/scratch/gap-task1-safe/fixround1-postcommit-regression.log 2>&1
```

- Final acceptance GREEN: **rc=0, 15 passed, 2 warnings in 1.23s**.
- Core: **rc=0, 83 passed, 2 warnings in 3.10s**; first implementation postcommit core: 83 passed in 2.77s.
- Initial extended gate: **rc=1, 4 failed, 437 passed, 2 warnings in 46.75s**. BASE comparison without new module: **rc=0, 426 passed, 2 warnings in 43.65s**.
- After approved fixture/assertion wave: **rc=0, 441 passed, 2 warnings in 44.86s**; XML 441 cases, no failures/errors/skips.
- Exact implementation candidate postcommit extended gate: **rc=0, 441 passed, 2 warnings in 43.60s**. Byte identity was verified again after that run.

Core includes acceptance, trailing, daily-chain and gap-recovery modules. Extended adds writer coordination, source routing, MOEX identity, no-trade evidence lock, SQLite evidence/defer/reconcile/rollback, writer lock and real daily steps. Actual file selections and timeout arguments are retained in the launcher and Task 1 report.

## Full backend coverage and focused worker branch evidence

```bash
/bin/bash /home/hermes/.hermes/cache/scratch/gap-task1-safe/isolated-suite.sh full > /home/hermes/.hermes/cache/scratch/gap-task1-safe/fixround1-full.log 2>&1
/bin/bash /home/hermes/.hermes/cache/scratch/gap-task1-safe/isolated-suite.sh worker-coverage > /home/hermes/.hermes/cache/scratch/gap-task1-safe/fixround1-worker-coverage.log 2>&1
python3 /home/hermes/.hermes/cache/scratch/gap-task1-safe/fixround1-metrics.py
```

Launcher uses `env -i` and `unshare --user --map-root-user --mount --net --pid --fork --mount-proc`. Production repository is masked with the copied candidate, installed venv is read-only, Hermes/worktrees and `/tmp` are masked by test-owned directories. `.env` and production data are not copied. HOME, TMPDIR, database, basetemp and coverage output are test-owned. Full/coverage modes enable only namespace-local loopback with no external route; existing sandbox integration remains disabled. Existing cached pytest-cov/coverage packages are loaded without installation or venv modification. Existing SDK route tests use synthetic tokens in isolated databases; no real external SDK request is possible.

Actual full invocation **inside namespace**, with `PY=/home/hermes/algotrader/apps/api/.venv/bin/python`, `COVERAGE_FILE=/mnt/full.coverage`, cwd the masked candidate's `apps/api`:

```bash
timeout --signal=TERM --kill-after=10s 900s "$PY" -m pytest -q \
  -p no:cacheprovider -p pytest_asyncio.plugin tests -p pytest_cov.plugin \
  --cov --cov-config=pyproject.toml --cov-report=term:skip-covered \
  --cov-report=json:/mnt/full-coverage.json \
  --basetemp=/mnt/pytest-fixround1-full --junitxml=/mnt/fixround1-full.xml
```

Actual focused invocation inside the same isolated environment, `COVERAGE_FILE=/mnt/worker-coverage.coverage`:

```bash
timeout --signal=TERM --kill-after=10s 900s "$PY" -m pytest -q \
  -p no:cacheprovider -p pytest_asyncio.plugin \
  tests/test_worker_gap_recovery_failure_status.py \
  tests/test_worker_trailing_gap_recovery.py tests/test_worker_daily_chain.py \
  -p pytest_cov.plugin --cov=worker --cov-config=pyproject.toml \
  --cov-fail-under=0 --cov-report=term-missing \
  --cov-report=json:/mnt/worker-coverage.json \
  --basetemp=/mnt/pytest-fixround1-worker-coverage \
  --junitxml=/mnt/fixround1-worker-coverage.xml
```

Full: **rc=0, 1634 passed, 8 skipped, 1 xpassed, 47 warnings in 196.09s**. Fresh Task 2 XML parsing confirms **1643 cases, 0 failures, 0 errors, 8 skips**. Non-strict XPASS is a passed XML case; actual pytest summary separately supplies XPASS=1. No identical full suite was rerun for unchanged source in Task 2.

Actual `full-coverage.json` native totals (percentages displayed to two decimals; native counts remain exact, full precision retained in JSON):

- Lines/statements: **4605/4712 = 97.73%**. Coverage.py executable lines are statement units, not a separate invented denominator.
- Branches: **1142/1192 = 95.81%**.
- Functions: **351/357 = 98.32%**. Definition: nonempty named JSON function bodies with at least one covered body line; excludes unnamed module buckets and zero-statement functions. Task 2 recomputed native counts.
- Native combined lines+branches: **97.34%**, configured **95%** gate passed. Combined coverage does not substitute for the separate metrics.
- Native exclusions: 59 executable lines from unchanged existing configuration. No new pragmas, exclusions, omissions or threshold reductions.

Focused root worker diagnostic: **rc=0, 75 passed, 2 warnings in 3.31s**. JSON verifies `_step_gap_recovery` **34/34 lines, 6/6 branches**; observer **2/2 lines, 2/2 branches**; `_run_all` **29/29 lines, 10/10 branches**. Each has **100%**, no missing lines/arcs. Entire root worker has **289/491 lines, 50/80 branches, 59.37% combined**. Diagnostic `--cov-fail-under=0` is not a lowered backend gate; root worker is outside package coverage.

## Real-runner event, partial commit, empty/idempotent, lock, cancellation proof

Actual-import acceptance wraps original `_emit` delivery and replaces only external candle I/O for real runner cases. All-chunks-failed delivers terminal `ticker_progress/error`, returns integer 0, retains metadata error, and makes worker False/failed=1. Exception and repeated-event signals union once per active trailing FIGI; unrelated/historical signals do not inflate failures.

Sibling A/C SQLite commits survive B failure and later attempts run. Historical/trailing/source returned totals are retained; boundary totals are historical 4, moex 3, tinkoff 2, total 9. Real empty response and no gaps remain success. Idempotent duplicate keeps unchanged DB rows and legacy reported integer 1, not unique-insert count.

Real flock contention distinguishes metadata False/structured `DEFER writer-lock-busy`, `result=deferred`, from ordinary bar-writer False/failed=1. No metadata writes occur on metadata deferral. Started coroutine work settles before exactly-once awaited owned-client close on the shared loop; direct `CancelledError` propagates. Actual daily chain continues guardian after gap failure and returns rc=1; migrations/universe_sync/backfill_moex remain critical. These observations do not prove detached thread settlement or SIGKILL cleanup.

## Independent task review and full-branch review verdicts

Parent supplied independent preflight approval on `04e2c4d77b4b6ef5754522d97ffbdeba3a0883a5`, independent source/task review and final whole-branch review: **`deleg_f285050d`, `SPEC ✅ + Approved`, no blockers**. Scope is the full implementation branch through `2586e66b86e3f46aec0151c565cc2f44ffdb076a`, including the approved legacy compatibility wave and ledger rulings. This leaf did not delegate or fabricate remote review. Initial implementation commit preceded independent approval; this record does not rewrite that ordering. Parent remains responsible for final documentation-head review/publication gates.

## Required CI status on exact candidate SHA

**Not verified.** Only tracked workflow is `.github/workflows/branch-name-check.yml`; externally configured required gates cannot be inferred from local files. Parent must check branch-name-check and every required external status on the exact final documentation SHA. Local GREEN, coverage and independent review are not CI. No push, merge, network status lookup or deploy was performed here.

## Strict change and canonical validation; diff check

Task 2 ran from worktree root with telemetry disabled (`OPENSPEC_TELEMETRY=0 DO_NOT_TRACK=1`):

```bash
openspec validate report-gap-recovery-failures --strict
openspec validate data-quality --type spec --strict
git diff --check
```

OpenSpec **1.13.0**: both commands **rc=0**, `Change 'report-gap-recovery-failures' is valid` and `Specification 'data-quality' is valid`; whitespace check rc=0. Canonical informational long-requirement notices are requirements **1, 10, 15**; 1/10 existed, 15 is the new additive requirement, not an error.

Canonical original **21317 bytes**, SHA-256 `b5d70005f9c11236e7fdab91e0ec4aa14d611388a5990efb128bb3e2b623be0c`. Fresh assertion verifies every original byte remains the prefix and the suffix is exactly one newline plus the reviewed requirement/scenarios block from the change delta. Exactly **eight** new scenarios. No other canonical capability was modified, deleted or archived.

## Warnings, unresolved gates, rulings and cost if wrong

Eight existing skips: `sandbox tests disabled` in `tests/ingestion/test_backfill_sandbox.py` and `test_real_sandbox.py`. They are unexecuted integrations, not passes. Existing non-strict XPASS: `tests/ingestion/test_real_client.py::test_real_client_init_raises_on_missing_sdk`, whose monkeypatch does not invalidate the already-imported SDK cache. Prior full gate has the same eight skips, XPASS and 47 warnings; no new suppression/xfail/skip was introduced.

Warnings include existing SDK deprecations, AsyncLimiter loop-reuse RuntimeWarning, and:

- ``StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.``
- `DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.`

Fresh baseline migration logs include `migration.statement_failed` for migration 016 statement 6 `no such table: instruments_new` and statement 7 `no such savepoint: _mig_016_cleanup`. These are captured logs, not extra pytest warnings, and are not attributed to this unchanged migration path. Nothing was suppressed.

All plan-owned rulings and costs:

1. Use scoped trailing exception/event union rather than new dependency/outcome architecture. Actual terminal event covers this defect; partial-source/chunk completeness remains unproven. Cost if wrong: a separately scoped outcome/accounting change is still needed; truthful failure must not claim complete source recovery.
2. Preserve legacy returned candle counts, including positive idempotent counts. `_write_bars` counts input candles, not unique inserts. Cost if wrong: separate accounting correction may be needed; dashboards must not interpret these totals as unique inserted bars.
3. Preserve metadata False/structured DEFER and daily rc=1, not a new exit-75 tuple. Cost if wrong: standalone temporary-failure entrypoints require separate review; existing process exit-75 contracts remain untouched.
4. Parent-approved narrow compatibility exception: only `gap_env.FrozenWorkerDate.fromisoformat` and trailing branches of `test_gap_metadata_adapters_preserve_other_error_policy` changed. Ordinary `date` fixes SQLite binding; new truthful-failure/no-raw-payload contract supersedes obsolete trailing-success/raw-message assertions. Exact FIGI/error_type, no raw error/message, historical policy, client close/release and DB-continuity assertions remain. Cost if wrong: fixture defects could be concealed or diagnostic/ownership detection weakened. Repeated assertion RED, exact diagnostics and retained ownership checks bound that risk. No source correction or spec semantics rewrite was made.

Safe commit checks use the existing staged credential scanner, whitespace, four-file allowlist and installed Markdown formatter. Original production-index hook is not invoked: it targets `/home/hermes/algotrader` and can trigger forbidden production MCP indexing. Only per-command hooks override is used; global hooks/configuration unchanged. **Production index is NOT updated.** Actual commit/check outputs and documentation SHA are retained in Task 2 report.

## Rollout ownership and limit of claims

Local implementation and documentation evidence only. Truthful cycle failure does **not** prove freshness, coverage-readiness increase, seven-day reliability, all-source or partial-chunk completeness, detached-thread settlement, or SIGKILL cleanup. Existing sandbox integrations were not run.

Parent owns exact-SHA CI, final release gate, publication/push, merge and approved production rollout. Deployment success requires actual written proof. No production state/configuration, secrets, installs, global hooks, push/merge/deploy or archive changed by Task 2. Archive remains pending until verified publication and approved completion.
