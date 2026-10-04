# Design

## Existing contract

The canonical writer-coordination spec already requires lock release only after commit or rollback, and reconciliation as a separate transaction after the bar transaction commits and releases its lock. The fix restores that commit atomicity; it does not change the coordination boundary.

## Transaction boundary

Keep the existing public wrapper and private DELETE helper. Move `conn.commit()` inside the wrapper's `try`. Catch `BaseException`, attempt `conn.rollback()` while flock remains held, then translate only numeric SQLite BUSY using the existing helper and the current timeout constant. Use role `evidence-reconcile`, phase `reconcile`, the explicit database path, and `writer_lock_path(db_path)` in `WriterLockBusy`. Re-raise other failures unchanged. Preserve the original error if rollback itself raises, matching PR #186.

The borrowed connection is never closed. The bar writer continues logging BUSY as bounded deferral and swallowing other ordinary reconciliation failures. Previously committed bar and metadata changes cannot be rolled back by this separate transaction. Network calls, calculations, and rate-limit sleeps remain outside locks. No new acquisition, retry, or wait is introduced.

## Verification

Use migrated file-backed temporary databases and a proxy over a real cached SQLite connection. Only the commit boundary is injected; reconciliation executes the real DELETE. Check SQLite transaction state, committed evidence and bars through another connection, kernel flock ownership during rollback, and successful later reuse. Cover error codes 5, 517, 14, 6, missing numeric code, and a direct `BaseException`; also exercise real SQL BUSY from an independent SQLite writer. Test both public bar entrypoints through the real reconciliation hook. A rollback-failure test verifies original-error preservation and lock release, not a guarantee that an unsuccessful rollback cleans the transaction.
