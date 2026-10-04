# Task 1 report: exact identities and inventory transition

## Status, scope and revision

- Status: **DONE_WITH_CONCERNS** for Task 1 as written in `task-1-brief.md` and the approved plan. Ready for the parent's independent exact-SHA review; Task 2 has not started.
- Worktree: `/home/hermes/worktrees/algotrader-daily-writer-expansion`.
- Base SHA: `01a2bcc31bf6a9acbe1b4fa8160fc8bd7eddc240`.
- Implementation branch: `feature/daily-reference-writer-coordination`, created locally at that exact base. The documentation branch and main were not changed, rebased or merged.
- Scope concern: the dispatch shorthand asks for a shared transaction primitive/error adapters, but the exact brief and plan Task 1 explicitly require additional role/phase literals only and say **“no new lock primitive”**. This implementation follows that narrower written scope. No transaction framework, new owner acquisition, or later-task error adapter was added. A different primitive scope requires a revised brief/plan from the parent.
- The review SHA is the local commit containing this report, returned to the parent after commit/readback. Resolve it with `git log -1 --format=%H -- .superpowers/sdd/2026-10-04-daily-reference-data-writer-coordination/task-1-report.md`; no self-referential commit hash is embedded.

## Changed files

1. `apps/api/src/algotrader_api/ingestion/writer_lock.py`: 18 added lines, limited to the two Literal aliases and their runtime frozensets. Added roles `universe-sync`, `backfill-metadata`, `corporate-actions`, `dividends`; phases `instruments`, `metadata`, `corporate-actions`, `adjusted-bars`, `dividends`. All previous values remain valid.
2. `apps/api/tests/test_writer_lock.py`: six new role/phase cases, six original-role regressions, and a contender-thread test. The thread joins while the outer owner remains held, produces exactly one `WriterLockReentrant`, never enters its body, and can acquire after release.
3. `apps/api/tests/test_writer_lock_diagnostics.py`: six actual kernel-flock contention cases with exact role/phase, canonical paths and complete bounded formatter output; 0.03-second test acquisition timeout, no pending body execution, and successful subsequent acquisition.
4. `apps/api/tests/test_writer_inventory.py`: exact seven-owner map plus the existing rowcount bar owner; qualified class/nested-function resolution; explicit non-owner/borrower negatives; import-alias and decorator scanning; deduplicated process scan including worker/common importer; no missing-file filtering.
5. This report, force-added by its exact path because the SDD directory is ignored.

Original `IN_SCOPE_FUNCTIONS`, script markers and raw-bar proof remain unchanged. Removed only the universe/dividend-fetcher blanket exclusions; retained the six other old module exclusions and added `db/sqlite.py` and `db/migrations_runner.py`. Inventory totals verified programmatically: 6 original entries, 7 daily entries, 1 supplemental entry, 8 whole-module negatives, 22 non-owner functions, 3 adjustment borrowers, and 7 Python owner files in the union scan.

The new positive inventory tests prove symbol presence, not runtime ownership. Each later task must add its owner's runtime protection tests. No xfail, skip, weakened selector, coverage omission or pragma was introduced.

## TDD evidence

Every command below used the existing interpreter with `PYTHONPATH` and `PYTHONHOME` unset and cache/parent bytecode disabled. Commands ran from the worktree root, not main.

```bash
# Baseline, then GREEN after the production edit:
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_writer_lock.py apps/api/tests/test_writer_lock_diagnostics.py \
  apps/api/tests/test_writer_inventory.py -q -p no:cacheprovider

# Individual pre-production RED checks:
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_writer_lock.py -k daily_role_phase -q -p no:cacheprovider --tb=short
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_writer_lock_diagnostics.py -k daily_role_timeout -q -p no:cacheprovider --tb=short
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_writer_lock.py -k daily_role_does -q -p no:cacheprovider --tb=short
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_writer_inventory.py -k writer_lock_scan -q -p no:cacheprovider --tb=short

# Final complete three-file RED before any production code change:
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_writer_lock.py apps/api/tests/test_writer_lock_diagnostics.py \
  apps/api/tests/test_writer_inventory.py -q -p no:cacheprovider --tb=short
```

Observed results:

- Baseline: **94 passed, 0 failed, 0 errors, 0 skipped, 2 warnings**, exit 0.
- Role/phase RED: **6 failed, 47 deselected, 2 warnings**, exit 1; missing new Literal values, not import/fixture failures.
- Real daily timeout RED: **6 failed, 28 deselected, 2 warnings**, exit 1; `WriterLockError: unknown writer role`, before the missing identities became valid.
- Thread regression pre-edit: **1 failed, 52 deselected, 2 warnings**, exit 1; the outer new role was rejected before the existing thread guard could be reached. No claim that the guard itself needed a fix.
- Alias-scanner RED: **8 failed, 1 passed, 26 deselected, 2 warnings**, exit 1; the old scanner missed all eight import-alias/qualified acquisition forms.
- Final complete pre-production RED: **13 failed, 148 passed, 0 errors, 0 skipped, 2 warnings**, exit 1. Only missing daily identities remained failing.
- Three-file GREEN: **161 passed, 0 failed, 0 errors, 0 skipped, 2 warnings**, exit 0.

## Corrected test-infrastructure finding

The first expanded combined RED had **14 failed, 145 passed, 2 warnings**. Its additional inventory failure was traced to the existing `_evidence_writer_lock`: it returns a context manager; acquisition occurs in its callers' `with` blocks. Treating context construction as immediate acquisition was a false positive.

Added two scanner regression cases against the worker/common-importer scan entries. `-k process_scan -q -p no:cacheprovider --tb=short` returned **2 failed, 72 deselected, 2 warnings** before the scanner correction. The scanner now follows transparent returned contexts and checks their actual caller critical sections; it does not exempt those bodies. Standalone inventory GREEN returned **74 passed, 2 warnings**, exit 0. All original raw-bar/marker assertions remain unchanged. Initial patch-context validation refusals were atomic and changed no files; the temporary test-helper type annotation diagnostic was corrected before GREEN.

## Retained SQLite failure contracts and final scoped gate

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_sqlite_evidence_busy_defer.py \
  apps/api/tests/test_sqlite_reconcile_commit_rollback.py \
  apps/api/tests/test_bars_sqlite_reconcile_defer.py -q -p no:cacheprovider

env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_writer_lock.py apps/api/tests/test_writer_lock_diagnostics.py \
  apps/api/tests/test_writer_inventory.py apps/api/tests/test_sqlite_evidence_busy_defer.py \
  apps/api/tests/test_sqlite_reconcile_commit_rollback.py \
  apps/api/tests/test_bars_sqlite_reconcile_defer.py -q -p no:cacheprovider \
  --junitxml=/home/hermes/.hermes/cache/scratch/daily-writer-task-1-01a2bcc-direct.xml
```

- Retained failure-contract subset: **37 passed, 0 failed, 0 errors, 0 skipped, 2 warnings**, exit 0.
- Final six-file direct CLI gate: **198 passed, 0 failed, 0 errors, 0 skipped, 2 warnings**, exit 0.
- The unchanged regression tests exercise actual file-backed SQLite BUSY from independent non-flock `BEGIN IMMEDIATE` owners; commit failures with codes 5/517 are deterministic injections, not claimed live commit reproductions. They verify kernel-flock-held rollback, borrowed connections remaining open, chained original BUSY exceptions, unchanged non-BUSY exception identity, direct `BaseException`, rollback failure and retained committed bars.
- The two ordinary warnings are existing Starlette/httpx and anyio deprecations. No dependency installation or warning suppression was performed.

## Self-review and strict validation

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python \
  /home/hermes/.hermes/cache/scratch/daily-writer-task-1-01a2bcc-self-review.py
env OPENSPEC_TELEMETRY=0 DO_NOT_TRACK=1 OPENSPEC_NO_UPDATE_CHECK=1 \
  openspec validate coordinate-daily-reference-data-writers --type change --strict --no-interactive
env OPENSPEC_TELEMETRY=0 DO_NOT_TRACK=1 OPENSPEC_NO_UPDATE_CHECK=1 \
  openspec validate writer-coordination --type spec --strict --no-interactive
git diff --check
```

- Read-only self-review: **65 checks passed** including **38 rejected in-memory AST mutations**. Source files/runtime owners were never mutated by those probes. The probes catch acquisition in every named non-owner, commit/rollback/close in every borrower, and qualified lock/process calls in every Python owner file.
- Exact brief owner maps, retained inventory/marker/raw-bar AST, candidate import path and 30.0-second default verified. All production AST outside the four role/phase definitions is unchanged from the base.
- The self-review runner also executed all six files with parent-process socket-connect denial: **198 passed, 0 failed, 0 errors, 0 skipped, 0 network attempts**, exit 0; JUnit counts match. That in-process runner had **3 warnings**, including one harness-only `PytestAssertRewriteWarning` for pre-imported anyio, in addition to the two baseline deprecations. The fresh direct CLI gate has only the two baseline warnings. Existing subprocess lock tests import the candidate primitive and use their own temporary paths; this is not a whole-backend namespace-isolation claim.
- Both strict validations passed, exit 0. Canonical validation retains its existing informational note about a requirement longer than 500 characters.
- `git diff --check` passed. Canonical/delta specs, archive, plan, schema, SQLite helpers, owner implementations, adapters, coverage configuration and dependencies are unchanged.
- Self-review script SHA-256: `9fafaf3e88d05e2d7cc81c204f6d877d9d0464b2dca2cf233357b0bcd8ef297d`. Scratch artifacts may be pruned; they are not committed.

## Verified Git blob SHAs

- `apps/api/src/algotrader_api/ingestion/writer_lock.py`: `916c50307a49b27e9e92a93fed5095b314a46dc7`.
- `apps/api/tests/test_writer_lock.py`: `84a17e714c03d092e6d85973b5c7254243438c73`.
- `apps/api/tests/test_writer_lock_diagnostics.py`: `b3b81106bd6e7ed91425c9617936a6504766dcb9`.
- `apps/api/tests/test_writer_inventory.py`: `71dc46d0f7bb12badf39cc6a0b7c1f7b89e6de49`.

## Commit scope and remaining gates

The local commit stages only the four source/test paths above and this exact report path, with message `test(ingestion): define daily writer scope and identities`. Use command-local `-c core.hooksPath=/dev/null -c commit.gpgsign=false`: repository hooks invoke secret/package/codebase-memory operations outside this leaf's no-secrets/no-install/no-network/no-global-memory scope. No hook, signature or index-refresh success is claimed.

No delegation, global-memory/skill update, production DB/process/schedule/secret access, installation, network operation, push, merge, deployment or archive application was performed. Full backend regression/coverage gates remain for the parent to rerun in the established isolated environment; this report claims only the scoped gates above. Independent Task 1 review must precede Task 2. Runtime coordination of the seven daily owners, later adapters/concurrency tests, rollout and the full scheduled cycle/seven-day standing-goal acceptance remain unimplemented or unverified by this leaf.
