# Design

## Existing path and confirmed failure

`worker._step_gap_recovery` -> `find_gaps` -> real `recover_gaps` -> real `BackfillRunner._backfill_one` -> `_backfill_one_tinkoff` -> transport `get_candles`.
The runner can emit an error event and return zero. The current sink filters on active trailing context, which is absent for historical recovery. Zero alone is not an error; a successful empty transport response emits `empty` and must remain allowed.

## Decision

Keep the change in the worker. Maintain a set of requested historical FIGIs and a set of distinct failed FIGIs. A `ticker_progress`/`error` event is relevant only when its string FIGI is in the historical request set or exactly equals the non-null currently active trailing FIGI. Ignore unrelated and non-string FIGIs. Register historical identities before awaiting recovery. Existing caught trailing exceptions add to the same failure set. Derive phase success from that set, not from returned bar counts; `failed=N` is the cardinality of the union, so repeated failures and a FIGI failing in both passes count once.
Do not change `recover_gaps` return types or runner behavior. Do not echo event error messages. Retain committed rows and accumulated success counts; returning-error historical calls still allow the helper to continue. Preserve the existing propagated metadata-BUSY defer and finally/close behavior. `gap_recovery` stays noncritical: failed status contributes rc=1 while later daily phases still execute.

## Verification boundaries

The parent probe uses actual worker, runner, gap finder and recovery helper, a migrated disposable file-backed database, transport-only error/empty fakes and a bounded trailing-selection override. It wraps `_emit` only to observe and then calls the original emitter. No fake phase/runner return is used. Keep that topology for acceptance tests. Use a fresh exact candidate archive inside the validated mount/network/PID isolation; read-only dependency mounts and masked production/Hermes paths. No installs or dependency sync.

## Risks

An overbroad observer could attribute unrelated events; explicit FIGI membership/current-context guards and unrelated-event controls prevent this. An overly strict observer could reject a valid zero; the actual empty control guards this. Failure cardinality is distinct FIGIs, not number of gaps or failed chunks. Previously silent partial chunks that produce no error event remain outside this bounded fix. Legacy migration cleanup messages observed in both parent fixtures are not the assertion failure: both fixtures have integrity ok and the transport-error case reaches the real error event and persisted metadata.
