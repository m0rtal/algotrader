# Report gap-recovery failures

## Why

`_step_gap_recovery` reports success after ordinary trailing exceptions. Actual `BackfillRunner` also converts an all-chunks-failed Tinkoff fetch into `ticker_progress` status `error` and integer `0`; the worker discards that event. The daily chain already continues after best-effort failure and returns `rc=1`, but cannot do so when the step falsely returns `True`.

## What Changes

Add one data-quality requirement for truthful trailing failure status. Observe existing terminal runner error events only during trailing attempts; union their FIGIs with caught trailing exceptions. Keep successful row totals and return `False` with bounded numeric `failed=N` detail when this set is nonempty. Preserve metadata lock deferral, bar-writer failure meaning, cancellation, owned-client cleanup, and best-effort continuation.

## Impact

Planned runtime edit: `apps/api/worker.py:_step_gap_recovery` only. Planned regression file: `apps/api/tests/test_worker_gap_recovery_failure_status.py`, importing the actual worker and runner. No runner API or schema change. Canonical `data-quality` receives this additive requirement only after implementation review. This draft neither applies nor archives the delta.

Canonical Autonomous Pipeline Liveness supplies cycle observability context. Its feature-gate `auto_recovery` requirement does not already specify this trailing function; this delta makes that narrow contract explicit.

## Non-Goals

No new retries, dependencies, thresholds, critical phases, routes, writer roles, lock policies, history recovery semantics, or partial-chunk outcome architecture. No rollback of committed bars. No classification of zero rows alone as failure. No readiness or seven-day reliability claim. No production, network, secret, push, merge, or deployment action in this docs-only task.
