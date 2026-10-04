# data-quality Specification (delta)

## ADDED Requirements

### Requirement: Truthful Best-Effort Trailing Gap Recovery

The worker gap-recovery step SHALL report `False` when a trailing FIGI attempt raises an ordinary exception or emits an existing `ticker_progress` event with status `error`, while continuing subsequent trailing attempts unless existing metadata lock deferral or cancellation requires exit. The ordinary summary SHALL include bounded numeric `failed=N`, counting unique failed trailing FIGIs across exceptions and events once, and SHALL retain accurate historical, trailing, and source totals using existing runner integer counts. It SHALL preserve committed bars, valid zero-row results, one-loop owned-client cleanup, cancellation propagation, existing historical handling, and metadata lock deferral. Gap recovery SHALL remain best-effort: subsequent daily phases run and the cycle returns `rc=1` after failure. Newly introduced trailing failure diagnostics SHALL NOT copy raw exception or event error payloads.

#### Scenario: Raised trailing failure does not become success

- GIVEN multiple trailing FIGIs and an ordinary failure for one FIGI
- WHEN the worker runs gap recovery
- THEN later trailing FIGIs are attempted and the step returns `False` with `failed=1`
- AND summary row counts retain successfully returned additions
- AND new trailing warnings contain the exception type, not its raw message

#### Scenario: Actual runner error event is observed

- GIVEN actual BackfillRunner candle fetch chunks all fail for a trailing FIGI
- WHEN the runner emits `ticker_progress` status `error` and returns `0`
- THEN the worker returns `False` with `failed=1`
- AND warnings or unrelated events alone do not increase the count

#### Scenario: Failure signals count unique FIGIs

- GIVEN one trailing FIGI emits repeated error events and then raises an ordinary exception
- WHEN the worker aggregates trailing failures
- THEN that FIGI contributes exactly one to `failed=N`
- AND historical events and events for an unrelated FIGI do not contribute

#### Scenario: Partial success survives sibling failure

- GIVEN a successful FIGI commits a bar and another trailing FIGI fails
- WHEN the step finishes
- THEN the committed bar remains and the step returns `False`
- AND historical, trailing, moex, and tinkoff counters reflect their successful returned additions

#### Scenario: Empty and no-work results stay valid

- GIVEN no gaps or valid empty or idempotent trailing responses without an error signal
- WHEN the worker runs gap recovery
- THEN zero added rows alone do not cause `False`
- AND a no-gap run retains its existing no-gap detail

#### Scenario: Existing lock meanings stay distinct

- GIVEN actual writer lock contention during trailing recovery
- WHEN metadata acquisition raises WriterLockBusy with role `backfill-metadata`
- THEN the step retains `False` with existing structured `DEFER writer-lock-busy` with `result=deferred` rather than changing its tuple into process exit 75
- AND a bar-writer timeout instead produces an ordinary failed trailing summary
- AND ordinary exceptions are not relabelled BUSY

#### Scenario: One-loop cleanup and cancellation remain intact

- GIVEN historical and trailing jobs use one owned client
- WHEN they finish or direct asyncio cancellation interrupts the trailing await
- THEN started coroutine jobs settle before exactly-once awaited client close on their shared event loop
- AND cancellation propagates rather than becoming an ordinary failure result

#### Scenario: Best-effort failure degrades the daily cycle

- GIVEN gap recovery returns `False`
- WHEN the daily chain runs
- THEN later phases still execute and phase status is `error` with cycle `rc=1`
- AND migrations, universe_sync, and backfill_moex retain their existing critical behavior
