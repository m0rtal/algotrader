# Design: truthful trailing outcomes

## Existing flow

`run_daily_chain` calls `_step_gap_recovery(db_path) -> tuple[bool, str]`. The step constructs one client and one `BackfillRunner`, runs `recover_gaps(db_path, runner, gaps)` followed by trailing `_backfill_one(*, figi, ticker, from_, to, source)` attempts in one `asyncio.run`, and awaits `client.aclose()` in `finally`. Historical errors already escape through the existing outer handler; do not change that behavior.

The trailing handler catches ordinary exceptions and continues. Real `_backfill_one_tinkoff` emits `BackfillEvent(type="ticker_progress", payload={"figi": ..., "status": "error", ...})` after all chunks fail, then returns `0`. `_async_noop_sink` currently loses that signal. Empty broker responses emit status `empty`, also returning `0`.

## Minimal change

Use a local `set[str]` of failed trailing FIGIs and local active FIGI. Pass a local async observer to this runner. Add only matching `ticker_progress` status `error` events for the currently active trailing FIGI. Set active FIGI immediately before each trailing await and reset it in `finally`. Historical events therefore do not create trailing failures. Ignore event error/message payloads, warnings, skipped/empty events, and unrelated FIGIs. Add caught ordinary exception FIGIs to the same set; repeated events plus an exception count once.

Keep metadata `WriterLockBusy(role="backfill-metadata")` re-raise and outer `format_busy_defer` unchanged: step `False` and bounded `DEFER writer-lock-busy` with `result=deferred`. Standalone writer entrypoints' temporary-failure exit code 75 remains outside this function; its tuple does not encode process exit 75. The daily chain still returns 1, not 75. A bar-writer lock timeout is a caught trailing failure (`False`, `failed=N`), not metadata DEFER. Do not convert ordinary errors to BUSY.

Keep returned counts: historical sum, trailing sum, and trailing `moex`/`tinkoff` row totals. Existing `_write_bars` returns candles passed in, not unique inserts; preserve this integer API rather than introducing database-delta accounting. Duplicate/idempotent candles may return a positive legacy count without adding rows. Add `failed=N` to the ordinary summary; return `not failed_trailing_figis`. If no gaps exist, retain `True, "gap recovery: no gaps"`. On new trailing warnings log `figi` and `error_type=type(exc).__name__`, not raw exception strings. The observer never logs or persists an event payload. Existing runner logging/metadata, outer historical error formatting, and unrelated log redaction remain outside this change.

## Lifetime and cancellation

Retain serial awaits in one loop and existing `finally: await client.aclose()`. No detached tasks, new gather, thread work, or retry. Direct `asyncio.CancelledError` must propagate and broker jobs must have settled before exactly-once close. Do not alter close-failure precedence or general pre-loop client construction semantics without separate evidence and authorization.

## Risks and verification

An exception-only flag misses real runner failures; real-runner tests are mandatory. A phase-wide observer could accidentally broaden historical failure behavior; active trailing scope prevents that. Counting events instead of FIGIs double-counts. Empty/idempotent results are not errors. Successful commits survive a failed sibling. Event observation covers emitted terminal errors, not every swallowed warning or partially failed source attempt.

Use fixed dates, fresh scratch-owned SQLite fixtures, cached connections closed between tests, actual flock contention, and finite broker sequences. Guard sockets, requests, SDK/client/token factories, and settings loading before actual worker import. Do not copy AST/function-clone probes into regression tests. Record RED, GREEN, coverage, independent review, CI, and written release evidence separately; none has been executed by this docs-only drafting task.
