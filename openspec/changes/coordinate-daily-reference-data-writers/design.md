# Daily reference-data transaction ownership

## Status and evidence boundary

Draft from `951dee8a92da9c4d636ec57ae0331b913b56cda9`, on independent documentation branch `docs/daily-writer-expansion`. No source/test edits, production reads, network probes, process management, deployment, or publication are authorized here. The canonical spec and `sqlite-evidence-busy-defer` change are not applied or archived by this draft.

Local baseline: OpenSpec 1.13.0; canonical `writer-coordination` strict validation passes; current static inventory runs with **26 passed, 2 existing dependency deprecation warnings**. This proves the old inventory, not new runtime coordination. The reported smoke's BUSY owner is unknown. Transaction durations below are source classifications, not measured production timings.

## Real daily wiring at the base

`apps/api/worker.py:307-319` defines `first = migrations, universe_sync, backfill_moex, bonds_depth, gap_recovery` and `derived = corporate_actions, dividends, freshness_check, guardian`. They are separate subsets, not a single global lock. No scheduler topology changes are proposed.

- `worker._step_universe_sync:440-454` calls `run_universe_sync`; `universe_sync.py:29-30` awaits discovery and calls `upsert_instruments`. `universe.py:99-123` slices rows into groups of 100 but calls `execute_returning_id` for each row. `db/sqlite.py:63-76` commits **each row**, despite the universe docstring claiming batches of 100.
- `worker._step_backfill_moex:457-490` calls `BackfillRunner.backfill_from_moex(recent_tail_days=5)`. Bonds/gap recovery reuse the common coordinated bar path. Broader BackfillRunner discovery and Tinkoff recovery paths also use `_upsert_instrument`, `_seed_metadata_for_figi`, and `_upsert_metadata`; including these owners avoids a public/manual runner bypass, without wrapping the whole runner.
- `worker._step_corporate_actions:807-828` first calls `derive_splits.run_derivation`; `derive_splits.py:251-277` reads local bars and computes candidates before calling `merge_into_corporate_actions`. The worker then opens its own connection, calls `apply_all_pending`, and commits once. Adjustment across all selected events is one transaction and can touch many adjusted bars.
- `worker._step_dividends:831-855` calls `fetch_and_persist`; `import_dividends_tinkoff.py:235-267` rate-limits, fetches, maps rows, then calls `merge_into_dividends` for one FIGI. Queue/dequeue writes are separate short bookkeeping transactions and remain excluded.
- Existing bar owners in `db/bars_sqlite.py:310-346,412-433` acquire, mutate and commit, release, then reacquire for reconciliation. Do not wrap those calls with a second daily lock.

## Exact additional owners and boundaries

Inventory identities are `<role>/<phase>:<qualified symbol>`; prefixes are diagnostic identities, not Python function names.

1. `universe-sync/instruments:upsert_instruments`, `ingestion/universe.py:63-124`. Prepare filtered row parameters outside. Use a dedicated connection owned by this function so an excluded heartbeat using the cached connection cannot commit this new transaction. Acquire per row immediately before BEGIN; execute existing figi-keyed UPSERT; commit/rollback; release. Close only the function-owned connection. Preserve the previous 30.0-second SQLite timeout, foreign_keys=ON setting and existing database journal mode. Preserve return count, filters, duplicate tickers, and locally computed fields. Do not invent atomic 100-row batches.
2. `backfill-metadata/instruments:BackfillRunner._upsert_instrument`, `ingestion/backfill.py:2841-2881`. Prepare default broker fields before acquisition. Existing owned connection; one current UPSERT and commit per invocation. Preserve `COALESCE` for absent ISIN/sector and all derived fields.
3. `backfill-metadata/metadata:BackfillRunner._seed_metadata_for_figi`, `backfill.py:2883-2902`. Existing owned connection; one `INSERT OR IGNORE` and commit. Repeated discovery must not reset metadata.
4. `backfill-metadata/metadata:BackfillRunner._upsert_metadata`, `backfill.py:2904-2940`. Compute timestamp/parameters before acquisition. Keep current metadata UPSERT semantics and return `None`; no repair of existing aggregate/status behavior in this change.
5. `corporate-actions/corporate-actions:merge_into_corporate_actions`, `scripts_import/import_corporate_actions_common.py:32-70`. Normalize dates/parameter tuples outside. Own one connection and one existing complete-list transaction. Check duplicate PKs inside the transaction, write non-duplicates, count actual inserts, commit/rollback before unlock. Existing duplicate-skip behavior wins over the misleading “replacing” docstring.
6. `corporate-actions/adjusted-bars:_step_corporate_actions`, `apps/api/worker.py:807-828`. Derive/merge first with its own acquisition; release before adjustment acquisition. On the worker-owned connection select chronological events and parse `(figi, date, float)` before the lock. Under one BEGIN call existing `apply_forward_split(conn, figi, ex_date, factor)` for each prepared tuple and commit/rollback once. This replaces the worker call to `apply_all_pending`, not its public borrower API. Do not change split math or transaction grouping.
7. `dividends/dividends:merge_into_dividends`, `import_corporate_actions_common.py:124-160`. Broker fetch and `_to_row` stay outside. Own one connection and one existing row-list transaction, per caller FIGI. Keep PK including `revision_n`, duplicate-skip semantics and actual insert count. No fetch/queue/limiter in the lock.

Existing six inventory entries stay unchanged. Add explicit coverage of existing `replace_bars_for_figi_with_rowcount` as a bar owner, not a new role. Preserve historical `listed_till` script and expected-bars markers.

## Corporate preparation versus mutation

`apply_all_pending:99-112` combines candidate SELECT/date/float conversion with calls to `apply_forward_split`. It does **not** contain split detection, network calls, rate limiting, or heavy Python calculations. The expensive derivation is already in `derive_splits_for_figi` and remains unlocked. The potentially expensive part of adjustment is SQL `INSERT ... SELECT` and `UPDATE bars_adjusted`, which must be under the transaction and lock.

The minimal daily change inlines the existing event SELECT and conversion before acquisition in the worker, then uses the **existing** `apply_forward_split` borrower. No new prepare/apply API is assumed. `apply_all_pending` remains available to its existing direct tests/callers with the same borrower contract. `_already_applied` is a bounded consistency read required inside each adjustment transaction; it must not become an unlocked stale idempotency decision. SQL COUNT/MIN/MAX inside the existing bar mutation similarly remain transaction-local. “Reads outside” means preparatory scans, not moving correctness checks out of their transaction.

If implementation discovers non-trivial Python adjustment computation, stop and revise the approved design with a separately named preparation function and its exact signature/tests. Do not hide derivation or scans under a whole-function decorator. Do not precompute adjusted values from a stale snapshot and overwrite concurrent bars. Keep current all-selected-events adjustment atomicity; batching/chunking requires another approved semantic change.

## Transaction and connection lifecycle

The unchanged primitive rejects a second active same-canonical-path acquisition anywhere in the same Python process, including another thread. It does not queue that thread; it raises `WriterLockReentrant`. Across independent processes the kernel flock waits with the existing monotonic 30-second default. New role strings must not create separate namespaces or relax this guard.

Every new transaction has this order: prepare; obtain idle owned connection; acquire existing writer lock; BEGIN; DML and transaction-local consistency checks; commit or attempted rollback; release; close owned connection. Acquisition failure starts no transaction. An already-active **borrowed** transaction must be rejected without committing, rolling back, or closing someone else's work. Common bar/evidence borrowers keep their lifetime contracts; worker `_step_bonds_depth` is not an extra transaction owner.

Rollback handling covers BEGIN, body, commit, and `BaseException`. A rollback error cannot skip unlock/descriptor close or mask the primary failure. No whole-run `with conn:` outside the flock: its implicit commit/rollback would execute after unlock. No contextmanager/decorator that reacquires inside the borrower.

## Diagnostics and deferral

Extend both Literal aliases and runtime frozensets together; retain exact old values. Roles add `universe-sync`, `backfill-metadata`, `corporate-actions`, `dividends`. Phases add `instruments`, `metadata`, `corporate-actions`, `adjusted-bars`, `dividends`. Tests assert the exact role and phase, not merely a prefix or string inclusion.

Each new owner catches only numeric primary SQLite BUSY using existing `is_sqlite_busy`, attempts rollback while flock is held, then raises `WriterLockBusy` with `reason="sqlite-busy"`, correct role/phase, canonical paths, existing lock timeout, and chained cause. Preserve SQLite LOCKED, other OperationalError, missing error code, validation errors, and interruptions. Do not inspect message text, retry or increase timeout. Existing evidence/listed-till behavior remains the narrow base implementation and its independent delta.

At daily worker boundaries catch `WriterLockBusy` before generic exception handling and return `(False, format_busy_defer(exc))`. Critical universe failure aborts; best-effort corporate/dividend failure lets remaining checks run but returns a failed/degraded chain. No success checkpoint for failed pending work. Already-committed universe rows, corporate derivation, bars, and earlier FIGI dividends survive and are reused on the next normal schedule. Existing direct derivation/dividend CLI adapters print the shared formatter and exit 75; no new CLI mode. Successful acquisitions remain unlogged.

## Explicit inventory/test-contract transition

`test_writer_inventory.py:26-33`: retain `IN_SCOPE_FUNCTIONS` exactly. Add `DAILY_TRANSACTION_OWNERS` with the seven prefixed identities listed above and add the existing rowcount bar owner to a supplemental owner inventory. Implement a qualified-symbol resolver: locate class `BackfillRunner` then its method; a matching name in another class is not evidence. Prefix is stripped before resolving the symbol. Keep owner identities separate from public helper/borrower identities.

`OUT_OF_SCOPE_MODULES:35-44`: remove **only** `ingestion/universe.py` and `scripts_import/import_dividends_tinkoff.py` from whole-module no-import assertions. Keep these exact existing module exclusions: `pipeline/assertions.py`, `data_quality/service.py`, `data_quality/completeness.py`, `maintenance/cleanup.py`, `ingestion/universe_sync.py`, `scripts_import/import_corporate_actions.py`. Add `db/sqlite.py` and `db/migrations_runner.py` as explicit no-lock module proofs. Require each inventory file to exist; do not silently skip missing files.

Replace the two removed negatives with function-level no-acquisition assertions:

- `universe.discover_universe`;
- dividend `fetch_and_persist` and its nested `_run`, `_list_tradeable_figis`, `_read_pending_figis`, `_queue_throttled_figi`, `_dequeue_figi`;
- worker `heartbeat_loop`, `_heartbeat_loop`, `_log_chain_phase`, `_write_pipeline_run`, `_step_guardian`, `_step_freshness_check`, `_step_universe_sync`, `_step_dividends`, `run_daily_chain`, `run_live_mode` (adapter catches may import exception/formatter but may not acquire);
- BackfillRunner `_log`, `_emit`, `_discover_universe`, and breaker `_tinkoff_breaker_record_failure`, `_tinkoff_breaker_record_success`;
- forward-adjustment `apply_all_pending`, `apply_forward_split`, `_already_applied` remain non-owning borrowers with no acquire/commit/rollback/close added.

Scan the union of original and additional owner files for process creation under locks; include `worker.py` and the common importer, not only the original list. Preserve raw-bar-insertion, identity, dry-run, safe-path, timeout, canonical alias, release, and reconcile ordering tests. Add runtime boundary tests; static symbol presence alone does not prove protection. No `xfail`, deleted negatives without replacements, coverage exclusions, or reduced test selector is acceptable as the scope transition.

## Alternatives rejected

1. Global lock in `db.sqlite` helpers: serializes reads/telemetry, changes unrelated behavior, and cannot correctly infer transaction ownership. Rejected.
2. Whole daily-step/function decorators: hold locks through network, rate limiting and derivation, and nest around existing bar/evidence owners. Rejected.
3. New generic writer framework or bulk universe API: unnecessary abstraction and changed atomicity. Reuse current primitive and SQL at explicit owners instead.

## Verification and rollout gates

Use file-backed `tmp_path` databases, test-owned data/log paths, and an offline network-denial fixture. Test each additional owner for acquire-before-BEGIN, release-after-commit/rollback, no writes on flock timeout, real SQLite BUSY from an independent non-cooperating connection, duplicate/idempotent outcomes, correct diagnostic identities, and failure recovery. Use a Connection subclass for deterministic commit/rollback failure injection; never label synthetic commit BUSY as a live reproduction. Same-process thread tests prove rejection and peak occupancy one; separate subprocess tests prove actual first/derived owner interprocess serialization and independent DBs.

Backend threshold remains exactly 95. Existing `scripts_import/*` omission is not expanded; new common-importer behavior still gets direct transaction tests. Full-suite tests that touch fixed paths or real network must first be isolated, or the gate is blocked and reported; do not call selective offline tests “full regression”.

After specification approval: implement by TDD; obtain independent exact-SHA review; publish PR through existing policy; deploy only approved merged SHA after restricted online SQLite backup and integrity verification. Run bounded offline fixture smoke for one metadata row, one split, and one dividend, then observe the real scheduled first/derived complete cycle and next retry/resume. No standalone forced production history run. Record readiness percentage/failed cohorts, current session freshness and cycle age, and natural retry outcomes. Standing goal remains open unless ML-ready coverage is at least 95%, age at most 4 hours, and autonomous daily operation is actually observed.

## Remaining risk

Excluded transactions and shared cached-connection telemetry are deliberately not fixed. Most excluded paths are small DML/commit sections, but their live duration/cause is unmeasured here. They can still contend or interfere with an existing shared borrower; the expanded flock cannot serialize a non-participant. Retain BUSY deferral and investigate a separate telemetry/connection-ownership change only with attribution. File locking also does not repair metadata semantics, upstream completeness, corporate idempotency weaknesses, or ML coverage.
