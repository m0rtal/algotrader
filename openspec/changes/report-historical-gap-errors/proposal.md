# Report historical gap errors

## Why

The real historical recovery producer can emit `ticker_progress` with `status=error`, persist error metadata and return zero. The worker observes errors only while a trailing FIGI is active; historical recovery precedes that context. A parent-run exact-deployed-source probe produced a false successful phase with `failed=0` despite a real emitted error. Its healthy-empty control remained successful.

## What Changes

- Extend worker-local error observation to the requested historical FIGIs and the currently active trailing FIGI.
- Return phase failure for any relevant error event and count distinct failed FIGIs across both passes.
- Preserve successful counts, persisted bars, empty-result semantics, client cleanup, existing metadata BUSY handling and noncritical daily continuation.

## Impact

Only `apps/api/worker.py` runtime behavior and bounded tests. The signatures of `recover_gaps`, `BackfillRunner` and the worker phase remain unchanged. Add a complementary data-quality requirement after verified implementation, without replacing existing trailing requirements.

## Non-Goals

No source-routing, partial-chunk redesign, new upstream requests, fabricated bars/evidence, denominator/universe/identity changes, freshness instrumentation, scheduling changes or production-readiness claim. Parent owns PR/CI/merge/backup/deploy after independent gates. No credentials, secrets or raw upstream exception text in new phase diagnostics.
