# Coordinate daily reference-data writers

## Why

The daily `first` and `derived` subsets can run in independent processes against the same SQLite file. At base `951dee8`, the writer-coordination contract intentionally covers bars, evidence, `listed_till`, and expected-bars updates, but excludes universe, corporate-action, dividend, and bookkeeping paths. `tests/test_writer_inventory.py` enforces those exclusions. Extending imports without changing that contract first would be a specification violation.

A reported smoke encountered SQLite BUSY despite owning the advisory flock. Its actual competing transaction owner is unproven. Source inspection identifies uncoordinated daily reference-data transactions, not a proven cause of that smoke. The proposal narrows one remaining coordination gap; it does not promise to eliminate every SQLite BUSY.

## What Changes

- Extend the existing `<database-path>.writer.lock` namespace to exactly seven additional transaction owners: `universe.upsert_instruments`; `BackfillRunner._upsert_instrument`, `_seed_metadata_for_figi`, `_upsert_metadata`; `merge_into_corporate_actions`; `worker._step_corporate_actions` adjustment transaction; `merge_into_dividends`.
- Add exact diagnostic roles `universe-sync`, `backfill-metadata`, `corporate-actions`, `dividends` and phases `instruments`, `metadata`, `corporate-actions`, `adjusted-bars`, `dividends`. Existing roles/phases remain valid.
- Keep selection, row preparation, broker/MOEX requests, split derivation, rate-limit waits, and sleeps outside the flock. Allow only transaction-local consistency checks and SQL mutation inside it.
- Put BEGIN, every mutating statement, commit, and attempted rollback under the same owner and flock. Never close a borrowed connection. Keep per-row universe commits; no new bulk-upsert API or 100-row atomicity promise.
- Replace the former blanket universe/dividend module exclusions with explicit transaction-owner positives and function-level orchestration/queue negatives. Retain every existing bar/evidence/identity/dry-run proof.
- Preserve fail-closed timeout, non-reentrancy, same-process thread guard, and narrow numeric SQLite BUSY deferral. Existing `sqlite-evidence-busy-defer` code/change remains untouched.

## Impact

Affected capability: `writer-coordination` only. This is a proposal, not an approved canonical rewrite. The delta contains complete MODIFIED requirement blocks for Shared Market-Data Lock Namespace, Bounded Coordination Diagnostics, and Explicit Capability Boundary, plus one ADDED daily ownership requirement. Other canonical requirements and scenarios remain unchanged.

Future implementation touches `ingestion/writer_lock.py`, `ingestion/universe.py`, three bounded methods in `ingestion/backfill.py`, `scripts_import/import_corporate_actions_common.py`, the corporate-action transaction and contention adapters in `apps/api/worker.py`, and existing operator CLI adapters. Tests change explicitly with the inventory. No schema, dependency, scheduler topology, ML denominator, or threshold change is proposed.

Implementation plan: `docs/superpowers/plans/2026-10-04-daily-reference-data-writer-coordination.md`.

## Non-Goals

- No global locking in `get_connection`, `execute`, or `execute_returning_id`; no lock for every read or write.
- No coordination of pipeline/pipeline_log/pipeline_runs/pipeline_heartbeat, heartbeat threads, ingestion logs, guardian/anomaly/completeness bookkeeping, circuit-breaker columns, dividend throttle queue, maintenance, migrations, startup repair, or the legacy curated corporate-action importer.
- No decorators around discovery, fetch, derivation, `run`, daily phases, or worker lifetimes.
- No stale-PID bypass, lock lease, force-unlock, extra retries, or increased SQLite/lock timeout.
- No metadata semantic repair, split-algorithm rewrite, new batching contract, forced production backfill, deployment, PR publication, or production access in this documentation change.

## Acceptance and Approval

Review and approve the specification before implementation. Require targeted RED/GREEN tests, unchanged backend coverage gate `fail_under = 95`, full isolated regressions, exact-SHA independent review, PR checks, and an operator-approved backup-first rollout. Use zero-network temporary-file fixtures for bounded metadata/corporate/dividend smoke before observing a real complete autonomous pipeline cycle.

The standing production goal is separately measured: ML-ready coverage at least 95%, pipeline freshness at most 4 hours, and observed daily retry/resume without manual coordination. Passing lock tests does not establish that goal. Excluded short writes remain possible sources of BUSY or shared-connection interference; investigate them separately if actual evidence requires it.
