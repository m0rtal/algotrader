# Task 1 implementation report

## Exact review target and scope

Implementation commit: `e9bc594917209266803a573e1ec3c8d3c8475ea0`.
Authorized execution base: `7e0f6dd84b9a76c45d9b30471a2a841df79a0cad`.
Worktree: `/home/hermes/worktrees/algotrader-verified-delisting`.
Branch: `fix/verified-delisting-metadata`.

Implemented the approved CLI-private trust boundary. `_probe_board_last` now returns `(board, listed_till, verified_upstream_isin)` only after HTTP 200, description identity, full row/column/container validation, exact board SECID, strict inactive activity and calendar-date checks. The caller compares non-empty local/upstream ISIN before mutation or history, including an empty history window. The inactive evidence path reuses that same verified identity. The shared active parser, writer transaction/rollback/close code, history parser, daily OHLC/source writer, cached denominator, coverage formula and canonical specifications are unchanged.

Changed implementation files:

- `apps/api/scripts/backfill_no_trade_evidence.py`
- `apps/api/tests/test_verified_delisting_metadata.py` (210 cases; real CLI, migrations, file-backed SQLite, coverage and metadata/year parsers)
- `apps/api/tests/test_backfill_no_trade_evidence_lock.py` (four inactive tuple fixtures and module-local frozen dates)
- `apps/api/tests/test_aux_writer_lock_outcomes.py` (two required inactive tuple adaptations only)
- `apps/api/tests/test_sqlite_evidence_busy_defer.py` (one required inactive tuple adaptation only)

## Safe execution envelope

All pytest runs used a separate candidate copy under `/home/hermes/.hermes/cache/scratch/delisting-task1-safe/repo`, including the then-uncommitted new test file. The candidate was copied from tracked worktree files plus that explicit new file, not an old git archive. Production `.env`, `data/` and `apps/api/data/` were excluded. A mount/user/PID/network namespace mapped the candidate over `/home/hermes/algotrader`, masked `/home/hermes/.hermes` and `/home/hermes/worktrees`, and redirected HOME, TMPDIR, data and SQLite paths into scratch. The existing API venv was bound read-only. Outbound networking was unavailable; only namespace-local loopback was enabled for full-suite local servers. No dependency was installed, no production state/configuration was accessed or mutated, no network/push/merge/deploy was performed.

Preflight command:

```bash
bash /home/hermes/.hermes/cache/scratch/delisting-task1-safe/isolated-suite.sh preflight
```

Result: exit 0, pytest 9.1.1, imported candidate `algotrader_api`, no `.env`, no Hermes logs, empty masked worktrees and no network routes. Initial launcher attempts failed before tests because the host command is `python3`, not `python`, and candidate mount-point directories needed creation. Correcting scratch preparation established isolation; those attempts are not RED evidence.

Exact launcher: `/home/hermes/.hermes/cache/scratch/delisting-task1-safe/isolated-suite.sh`.
SHA-256: `5488f21c334e48cb6e99d24eb75191061275c626f140035de07376d2631fca7d`.
For scoped runs it executes:

```bash
timeout --signal=TERM --kill-after=10s 900s   /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest -q   -p no:cacheprovider -p pytest_asyncio.plugin "${files[@]}" "${extra[@]}"   --basetemp="/mnt/pytest-$mode" --junitxml="/mnt/$mode.xml"
```

`PYTHONDONTWRITEBYTECODE=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `ALGOTRADER_DATA_DIR=/mnt/data` and `ALGOTRADER_SQLITE_PATH=/mnt/data/state.db` are set inside the namespace. Full runs add the existing read-only uv cache locations to PYTHONPATH to resolve already-installed dependencies and add `-p pytest_cov.plugin --cov=algotrader_api --cov-branch --cov-report=term-missing --cov-report=json:/mnt/full-coverage.json --cov-fail-under=95`. No coverage exclusion or pyproject change was made.

## RED evidence before production edits

```bash
bash /home/hermes/.hermes/cache/scratch/delisting-task1-safe/isolated-suite.sh red   > /home/hermes/.hermes/cache/scratch/delisting-task1-safe/red.log 2>&1
bash /home/hermes/.hermes/cache/scratch/delisting-task1-safe/isolated-suite.sh red-all   > /home/hermes/.hermes/cache/scratch/delisting-task1-safe/red-all.log 2>&1
```

The first run selected `invalid_metadata_empty_window`: 34 tests, 26 failed, 8 passed, 0 errors/skips, exit 1. The complete pre-edit matrix had 193 tests: 147 failed, 46 passed, 0 errors/skips, exit 1. Failures included the intended `assert after == before`, not fixture/import failures, and real unchecked malformed parser exceptions on the unfixed CLI. Transport/network controls already passed.

A later baseline replay changed only the assertion order to expose actual readiness loss directly:

```bash
git show 7e0f6dd84b9a76c45d9b30471a2a841df79a0cad:apps/api/scripts/backfill_no_trade_evidence.py   > /home/hermes/.hermes/cache/scratch/delisting-task1-safe/repo/apps/api/scripts/backfill_no_trade_evidence.py
bash /home/hermes/.hermes/cache/scratch/delisting-task1-safe/isolated-suite.sh red   > /home/hermes/.hermes/cache/scratch/delisting-task1-safe/red-final.log 2>&1
```

Again 34 tests, 26 failed, 8 passed, exit 1. The exact NULL/http500 case failed `assert gate_after == gate_before`: actual `gate_after=[]` (unexpected ready), while the asserted precondition was `gate_before[0]["reason"] == "stale"`, `max_ts=2026-09-10`, `bars_count=1`, `expected=1`. This demonstrates the empty-window vulnerability through the real coverage gate.

Additional shape and successful inactive-evidence cases were replayed against the same baseline CLI before restoring the candidate:

```bash
bash /home/hermes/.hermes/cache/scratch/delisting-task1-safe/isolated-suite.sh red-extra   > /home/hermes/.hermes/cache/scratch/delisting-task1-safe/red-extra.log 2>&1
```

10 tests: 8 failed, 2 passed, 0 errors/skips, exit 1. Missing description name/value, board SECID/activity and attempted inactive issuer re-certification produced real behavioral failures; top-level malformed containers exposed actual unchecked exceptions.

## GREEN and scoped regressions

```bash
cp apps/api/scripts/backfill_no_trade_evidence.py   /home/hermes/.hermes/cache/scratch/delisting-task1-safe/repo/apps/api/scripts/
bash /home/hermes/.hermes/cache/scratch/delisting-task1-safe/isolated-suite.sh regression   > /home/hermes/.hermes/cache/scratch/delisting-task1-safe/regression-final.log 2>&1
bash /home/hermes/.hermes/cache/scratch/delisting-task1-safe/isolated-suite.sh extended   > /home/hermes/.hermes/cache/scratch/delisting-task1-safe/extended.log 2>&1
```

`regression`: 441 passed, 0 failed/errors/skips, exit 0. This is the new file plus all seven requested existing paths: lock, historical final fixes, MOEX evidence, all-path identity, metadata poison, coverage and writer lock.

`extended`: 237 passed, 0 failed/errors/skips, exit 0. This is the new file plus auxiliary writer outcomes and SQLite evidence busy-deferral suites.

The first targeted GREEN before later additive tests was 193 passed, exit 0 (`green.log`, `green.xml`). Final acceptance includes all 210 new cases without skips. It checks preserved prior NULL/non-NULL dates, bars, cached expected-bars, existing evidence/TTL fields and stale gate; identity rejection before the first history; valid empty-window idempotency; independently committed metadata surviving six degraded-history forms; exact activity matrix and unchanged active-primary controls; two-board latest selection; missing local identity; true partial cursor `[[0,2,2]]` retaining one row with one transport call; successful real evidence without inactive issuer re-probe; rollback-before-unlock, numeric BUSY exit 75, BaseException/rollback failure preservation, evidence-only busy retaining metadata, dry-run lock-free state and owned-connection close. Every CLI metadata/history transport call probes the actual lock path with nonblocking flock to prove I/O is outside writer locks.

## Full backend gate and discovered fixture gap

```bash
bash /home/hermes/.hermes/cache/scratch/delisting-task1-safe/isolated-suite.sh full   > /home/hermes/.hermes/cache/scratch/delisting-task1-safe/full.log 2>&1
```

The initial full run had 1811 passed, 8 failed, 8 skipped, 1 xpassed, 47 warnings, exit 1; coverage already 97.34%. All eight failures came from three legacy tuple fixtures outside the seven planned paths: two auxiliary tests and six parameterized listed-till rollback tests, all `ValueError: not enough values to unpack (expected 3, got 2)`. They were not production-path failures. The spec-required three-field private interface takes precedence over outdated fixture assumptions. Only those tuples were adapted, preserving all assertions and lock/exception policies; no production behavior or validation was weakened.

The identical full command was run in a separately masked/offline authorized baseline copy from `ad58b28`:

```bash
bash /home/hermes/.hermes/cache/scratch/delisting-task1-baseline-safe/isolated-suite.sh preflight
bash /home/hermes/.hermes/cache/scratch/delisting-task1-baseline-safe/isolated-suite.sh full   > /home/hermes/.hermes/cache/scratch/delisting-task1-baseline-safe/full.log 2>&1
```

Baseline: 1619 passed, 8 skipped, 1 xpassed, 47 warnings, exit 0, coverage 97.34%. Thus the tuple failures were new fixture interface incompatibilities and were fixed, not excused as baseline failures.

Final exact candidate full run: **1829 passed, 0 failed/errors, 8 skipped, 1 xpassed, 47 warnings, exit 0**, 213.64 s. The eight skips are existing explicitly disabled broker-sandbox tests (not verified and not represented as passing). The XPASS is the existing missing-SDK import-cache test, also present on baseline. Warnings and migration 016 diagnostic messages are retained existing suite behavior; no suppression was added.

Coverage: **97.34%** combined line/branch coverage (rounded display; full precision retained in `full-coverage.json`); 4605/4712 statements, 1142/1192 branches, 107 missing lines, 50 missing branches, 59 excluded lines. `pyproject.toml` omissions/exclusions and all source modules under `src`, canonical authorities and daily worker are byte-identical to the authorized base. The CLI is outside package `--cov=algotrader_api`; its behavior is exercised by actual CLI integration tests, not falsely claimed as part of package coverage.

Artifacts under `/home/hermes/.hermes/cache/scratch/delisting-task1-safe/`:

- `red.log`, `red-all.log`, `red-final.log`, `red-extra.log` and corresponding XML files
- `green.log`, `green.xml`
- `regression-final.log`, `regression.xml`, `extended.log`, `extended.xml`
- `full-initial.log`, `full-initial.xml`, `full-initial-coverage.json`
- `full-before-extra.log`, `full-before-extra.xml` (successful earlier full run)
- `full.log`, `full.xml`, `full-coverage.json`, `full.coverage`
- `isolated-suite.sh`, `repo/` (exact exercised source/test candidate), test-owned scratch DBs and `safe-hooks/pre-commit`

Baseline artifacts: `/home/hermes/.hermes/cache/scratch/delisting-task1-baseline-safe/{isolated-suite.sh,full.log,full.xml,full-coverage.json}`.

## Commit and remaining gates

```bash
git diff --check
git add apps/api/scripts/backfill_no_trade_evidence.py   apps/api/tests/test_verified_delisting_metadata.py   apps/api/tests/test_backfill_no_trade_evidence_lock.py   apps/api/tests/test_aux_writer_lock_outcomes.py   apps/api/tests/test_sqlite_evidence_busy_defer.py
git -c core.hooksPath=/home/hermes/.hermes/cache/scratch/delisting-task1-safe/safe-hooks   commit -m "fix(ingestion): verify delisting metadata before listed-till mutation"
```

`git diff --check` exited 0. The per-command safe hook ran the repository credential scanner successfully. Its exact output was `SAFE_HOOK: credential scanner passed; codebase-memory index not updated (production reindex forbidden).` Shared git/hook configuration was not modified. The first documentation commit attempt was blocked by the scanner's `pan_card` heuristic on the full-precision coverage decimal; the report now uses the standard 97.34% display and retains full precision in the existing coverage JSON, without changing scanner configuration. Production reindex and pnpm/install hooks were not run. Implementation commit contains only the CLI/new tests/required inactive fixtures.

Independent exact-SHA spec and quality review, strict release validation, CI/PR/parent-owned merge, canonical apply/archive and deployment remain pending. No release or seven-day autonomous acceptance claim is made. This report and progress/task records are a follow-up documentation-only commit; the implementation SHA above remains the precise code/test review target.
