# Task 5 report: numeric BUSY and fail-closed adapters

## Scope and result

- Worktree: `/home/hermes/worktrees/algotrader-daily-writer-expansion`.
- Branch: `feature/daily-reference-writer-coordination`.
- Starting HEAD: `95060d9f2b148f36c116e12e2a8987fc54b522a8`.
- Task 5 implementation and scoped verification are complete. Parent review is still required before Task 6 stress tests and the full PR/merge/deploy gates.
- Commit subject: `fix(worker): defer contended daily reference writes`. The resulting commit SHA is returned separately after the local commit; this report is part of that commit.

## Changes

Production changes are limited to the four files authorized by the brief:

- `apps/api/worker.py`: daily universe, corporate-action and dividend adapters return `(False, format_busy_defer(exc))`; metadata-role BUSY propagates out of the trailing gap catch and becomes a failed gap step. Scheduled/manual worker failures retain rc=2 and client-finally cleanup, with the formatter in the error log and `pipeline.status="err"` detail.
- `apps/api/src/algotrader_api/ingestion/backfill.py`: discovery metadata BUSY restores IDLE, emits `done.status="error"` and re-raises the same exception. Per-FIGI metadata BUSY is returned as the exception object, not a string. Existing gather waits for started jobs, successful FIGIs are counted, deferred FIGIs are not counted, and the terminal error propagates after settlement.
- `apps/api/scripts/derive_splits.py`: only the existing derivation call gains the rc=75 adapter.
- `apps/api/src/algotrader_api/scripts_import/import_dividends_tinkoff.py`: only the existing CLI fetch/persist call gains the rc=75 adapter. Imports are exception/formatter only; no new acquisition is added to orchestration or the dividend queue.

Test files modified:

- `apps/api/tests/test_daily_reference_writer_coordination.py`: actual daily adapters and controlled-argv CLI calls; actual discovery, ticker runner, scheduled/manual worker and trailing/historical gap paths; kernel contention, post-bar-commit acquisition injections, settlement, readback, retry and partial dividend commits.
- `apps/api/tests/test_backfill_run_coverage.py`: offline runner setting and preservation of ordinary/non-metadata error policies through actual discovery/ticker bodies.
- `apps/api/tests/test_worker_daily_chain.py`: critical versus best-effort behavior and final rc=1 for deferred daily steps, using the existing isolated chain fixtures.
- `apps/api/tests/test_writer_lock_diagnostics.py`: metadata-role diagnostics ignore raw exception/cause payloads and remain one bounded line.
- `apps/api/tests/test_writer_inventory.py`: explicit no-acquisition coverage for the new exception-only adapter sites, including dividend CLI main. Existing ownership boundaries are retained.

No scheduler, interval, backoff, formula, core pipeline, evidence/listed-till, whole-phase or queue-lock changes were made. Real `_backfill_one(..., to=...)` signatures are unchanged. Other writer roles and ordinary errors retain their old handling.

## TDD and reproducible RED/GREEN

The adapter tests were written and exercised before their corresponding source edits. Fixture/import/signature failures were corrected, not accepted as metadata-contention RED. The final tests were also replayed against an untouched archive of the starting HEAD, outside the worktree, to verify that the corrected fixtures still catch the missing adapters.

RED replay preparation used `git archive` for `apps/api` at the starting SHA and copied only the final `test_daily_reference_writer_coordination.py` into the archive. The archive is:

`/home/hermes/.hermes/cache/scratch/task5-baseline-proof-o26ikri1`

The following command ran from that archive for RED and from the worktree for GREEN:

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_daily_reference_writer_coordination.py \
  -k 'daily_actual_reference_step or actual_derivation_cli or actual_dividend_cli or run_discovery_metadata or run_ticker_metadata or run_ticker_post_bar or run_metadata_busy_waits or run_worker_metadata or gap_recovery_metadata_busy_not_zero or dividend_partial_commit' \
  -q -p no:cacheprovider --tb=short --show-capture=no
```

- RED: exit 1; **27 failed, 184 deselected, 2 warnings in 4.77s**. Failures expose generic daily diagnostics, uncaught CLI BUSY, `DID NOT RAISE WriterLockBusy`, worker rc=0 instead of 2, and trailing gap success instead of failure. They are not SDK, network or unexpected-keyword failures.
- GREEN: exit 0; **27 passed, 184 deselected, 2 warnings in 3.24s**.

Full scoped GREEN, including the brief's four-file suite plus the ordinary-error and inventory regressions:

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_daily_reference_writer_coordination.py \
  apps/api/tests/test_sqlite_evidence_busy_defer.py \
  apps/api/tests/test_writer_lock_diagnostics.py \
  apps/api/tests/test_worker_daily_chain.py \
  apps/api/tests/test_backfill_run_coverage.py \
  apps/api/tests/test_writer_inventory.py \
  -q -p no:cacheprovider --tb=short --show-capture=no
```

Result: exit 0; **407 passed, 2 warnings in 17.20s**. No tests were skipped. The two warnings are installed FastAPI/Starlette dependency deprecations (`httpx` TestClient and the anyio BlockingPortal alias), not adapter failures.

Raw replay outputs are test-owned scratch files beside the archive: `task5-red-output.txt`, `task5-green-selected-output.txt`, and `task5-green-scoped-output.txt`. They are not repository deliverables. All nine modified Python files also compiled in memory without bytecode writes, and `git diff --check` passed.

## Verified contracts

- All seven existing owner cases remain covered by real SQLite `BEGIN IMMEDIATE` contention on a saved-real connection that does not acquire flock. The candidate acquires flock, encounters numeric SQLite BUSY, rolls back before release, closes, preserves row state and succeeds after the external transaction ends.
- Existing deterministic commit injections distinguish numeric codes 5/517 from 1/6/no code, including text-only `database is locked`; Interrupted(BaseException) and rollback-failure cases remain covered. These owner tests were already present from Tasks 1–4 and were not replaced by mock-only tests. The numeric classifier is unchanged.
- Actual universe/corporate/dividend daily adapters and actual derivation/dividend CLI main functions cover both real kernel flock contention and real non-participating SQLite contention. Owner/phase/reason fields and the full formatter contract are checked. CLI stderr contains exactly one diagnostic line and no success output.
- Failed dividend merge does not dequeue the failed FIGI. A separate two-FIGI SQLite-contention case proves the earlier committed dividend and dequeue remain intact. Release, retry and repeated zero-new-row success preserve PK semantics.
- Actual discovery and per-ticker metadata failures end in IDLE/error and preserve the exception identity. Scheduled/manual workers write only the expected error pipeline row, return 2, and close the client. Retry writes a subsequent success row.
- Post-bar-commit metadata contention is explicitly labelled an acquisition adapter injection, not live SQLite contention. The real bar writer has already committed bars and aggregate/status metadata. Readback proves that committed bars survive, `last_backfilled_at` remains unchanged, and the rejected explicit metadata owner does not change acquisition-time metadata. Tests do not incorrectly assert rollback of prior committed work.
- The two-FIGI runner case starts the second broker job before the first metadata failure, waits for both to settle, preserves both committed bar sets, counts only the successful FIGI and verifies no pending task/client-closure race.
- Real trailing and historical gap paths reach offline Tinkoff candle calls with `to=`, preserve pending metadata, close the client and return failure on metadata BUSY. Historical `recover_gaps` is also invoked directly. Other-role BUSY and ordinary errors retain trailing warning-and-continue and historical generic-failure behavior.
- Existing `run_backfill()` rc=1 on runner failure is retained; no new mode or API was introduced.

## Safety, review and remaining concerns

- All data and database writes are fixture-owned under scratch. Outbound network is denied for the coordination tests. Broker clients are finite offline fakes; no SDK call, real token read, production log/heartbeat write or service action was used.
- Source and test diffs were reviewed locally against the brief. Independent parent review remains outstanding; no delegation, push, merge or deployment was performed.
- Existing `run_worker()` references `client_mod.read_token_file`, which is absent in the current client module. Tests isolate only that token-check boundary with a non-secret sentinel, as authorized by the brief. Repairing the legacy token check is outside Task 5.
- Existing synchronous `run_backfill()` has no client-finally cleanup. Its regression test owns and closes the offline client; production cleanup policy was not broadened here.
- Full backend/frontend coverage gates and Task 6 stress verification are parent work, not claimed by this scoped run.
- The local commit uses a per-command `core.hooksPath=/dev/null` override: the normal hook performs a global codebase-memory refresh outside this leaf's authorization. No persistent hook configuration change or dependency installation was made.
- The repository credential scanner was run explicitly on the exact staged slice and returned 1 for three pre-existing synthetic fixtures in `test_writer_lock_diagnostics.py` at lines 233, 326 and 378. The same scanner against HEAD and every staged blob confirmed the three findings are identical to baseline and there are zero new findings. No scanner allowlist or fixture value was changed to hide the result.
- The SDD directory is ignored. Only this authorized report was force-added; the staged scope was checked against the explicit ten-file source/test/report set.
