# Data Quality Specification (delta)

## ADDED Requirements

### Requirement: Truthful Historical Gap Recovery Status

The worker SHALL account for relevant error events from historical as well as trailing gap recovery. A relevant event SHALL be `ticker_progress` with `status=error` and a string FIGI belonging to the requested historical gap set or exactly matching the non-null active trailing FIGI. Historical identities SHALL be registered before awaiting their recovery. A phase with any such error SHALL return false even when the runner returns zero or other FIGIs successfully commit bars. The `failed=N` diagnostic SHALL count distinct failed FIGIs across both passes, without echoing raw event errors. Repeated errors and the same FIGI failing in both passes SHALL count once. Unrelated or non-string FIGIs SHALL not cause a phase failure. Existing caught trailing exceptions SHALL continue to count as failures.

Successful empty responses without error events SHALL remain successful. The worker SHALL preserve actual successful bar counters, committed bars, subsequent recovery calls, owned client cleanup, existing metadata-BUSY defer/rollback/exception policy and noncritical daily continuation with rc=1. The change SHALL NOT alter runner/helper signatures, upstream routing or producers, cached expected bars, instrument universe, identity validation, no-trade evidence rules, coverage thresholds or scheduling. It SHALL NOT certify production freshness or seven-day autonomy.

This requirement SHALL extend `Truthful Best-Effort Trailing Gap Recovery`: its prior trailing-only `failed=N` scope and historical-event exclusion SHALL be superseded for the ordinary phase-wide summary by the distinct historical/trailing union defined above. Historical errors for requested FIGIs SHALL no longer be excluded from that phase result. Unrelated identities SHALL remain excluded, and all other trailing recovery, cleanup, deferral and continuation requirements SHALL remain binding.

#### Scenario: Real historical upstream error fails the phase

- GIVEN a migrated temporary DB with one instrument and bars on 2026-09-07 and 2026-09-09, producing a real historical gap on 2026-09-08
- WHEN the actual worker, gap helper and runner execute with no trailing selection and a transport-only client that raises a bounded RuntimeError
- THEN the emitted error event and persisted error metadata SHALL cause phase false and failed=1
- AND existing bars SHALL remain unchanged and the owned client SHALL close

#### Scenario: Healthy empty historical control is allowed

- GIVEN the same real historical path and a successful empty transport response
- WHEN the actual runner emits empty rather than error
- THEN the phase SHALL return true with zero bars and failed=0
- AND existing bars SHALL remain unchanged and the client SHALL close

#### Scenario: Mixed historical results retain successful work

- GIVEN requested historical FIGIs with one returning-error result and another successfully committed result
- WHEN recovery completes
- THEN the phase SHALL fail with failed=1 while retaining actual successful bar counts and committed rows
- AND later requested historical calls SHALL still execute

#### Scenario: Relevant failures are deduplicated and unrelated events ignored

- GIVEN repeated historical errors, an error for the same FIGI in the trailing pass, and unrelated or non-string event FIGIs
- WHEN the worker summarizes recovery
- THEN only the distinct requested/active failed FIGIs SHALL be counted
- AND unrelated or non-string event identities SHALL not change phase status

#### Scenario: Metadata contention retains its existing defer contract

- GIVEN an existing metadata WriterLockBusy failure during historical or trailing recovery
- WHEN the phase handles that failure
- THEN the existing structured defer and false phase result SHALL remain unchanged
- AND pending metadata rollback/ownership behavior and owned client closure SHALL remain intact

#### Scenario: Daily best-effort policy remains noncritical

- GIVEN a relevant historical error and runnable later daily phases
- WHEN the actual daily chain completes
- THEN gap_recovery SHALL be recorded as error, later phases SHALL run and chain rc SHALL be 1
- AND no coverage, identity, evidence, universe or schedule rule SHALL change
