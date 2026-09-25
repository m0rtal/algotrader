"""One-shot repair for production DBs left in a half-migrated state.

Detects the broken state (instruments missing, instruments_new
present) and finishes the interrupted migration 016 by renaming
instruments_new → instruments and recreating the ml_features view.

Also updates the schema_migrations hash for 016 so the runner does
not raise MigrationHashMismatch on the next API start (Task 1's
runner refuses to silently re-apply a migration whose content hash
differs from the recorded one).

Usage (the operators stops supervisors first because the script
takes the write lock):
    bash scripts/algotrader-supervisor.sh kill algotrader-moex-backfill
    bash scripts/algotrader-supervisor.sh kill algotrader-derived
    bash scripts/algotrader-api-supervisor.sh kill
    python scripts/repair_instruments_table.py
    bash scripts/startup-algotrader.sh
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from pathlib import Path


DEFAULT_DB = "/home/hermes/algotrader/apps/api/data/state.db"
# Migration file path is resolved relative to this script so the
# repair tool can update the recorded hash without importing any
# package code (ADAPT-4: no package imports).
MIGRATION_FILE_REL = "../apps/api/src/algotrader_api/db/migrations/016_instruments_figi_pk.sql"


def _current_migration_hash() -> str:
    migration_path = (Path(__file__).parent / MIGRATION_FILE_REL).resolve()
    return hashlib.sha256(
        migration_path.read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()


def main() -> int:
    db_path = Path(
        os.environ.get("ALGOTRADER_STATE_DB", DEFAULT_DB)
    )
    if not db_path.exists():
        print(f"ERROR: {db_path} does not exist", file=sys.stderr)
        return 2
    new_hash = _current_migration_hash()
    con = sqlite3.connect(str(db_path), timeout=30)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        has_instruments = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='instruments'"
        ).fetchone() is not None
        has_instruments_new = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='instruments_new'"
        ).fetchone() is not None
        if has_instruments and not has_instruments_new:
            print("OK: state already clean (instruments present, no instruments_new)")
        elif not has_instruments and not has_instruments_new:
            print(
                "ERROR: both instruments and instruments_new are missing",
                file=sys.stderr,
            )
            return 2
        else:
            view_sql = con.execute(
                "SELECT sql FROM sqlite_master WHERE type='view' "
                "AND name='ml_features'"
            ).fetchone()
            con.execute("BEGIN IMMEDIATE")
            if view_sql:
                con.execute("DROP VIEW IF EXISTS ml_features")
            con.execute("ALTER TABLE instruments_new RENAME TO instruments")
            if view_sql:
                con.execute(view_sql[0])
            con.execute("COMMIT")
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            n = con.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
            print(f"OK: repair complete, instruments now has {n} rows")
        # Always reconcile the recorded 016 hash with the current
        # migration file so the runner does not raise
        # MigrationHashMismatch on next API start (ADAPT-11: brief's
        # verbatim repair script did not account for Task 1's
        # schema_migrations tracking; without this UPDATE the API
        # would refuse to start because the on-disk migration was
        # rewritten and its content hash changed).
        con.execute(
            "UPDATE schema_migrations SET content_hash = ? "
            "WHERE migration_id = '016_instruments_figi_pk.sql'",
            (new_hash,),
        )
        if con.total_changes == 0:
            # No row existed — insert one (defensive; should not
            # happen on a normally-bootstrapped DB).
            con.execute(
                "INSERT INTO schema_migrations "
                "(migration_id, content_hash) VALUES "
                "('016_instruments_figi_pk.sql', ?)",
                (new_hash,),
            )
        con.commit()
        print(f"OK: schema_migrations hash for 016 updated to {new_hash[:16]}...")
        return 0
    except Exception as e:
        con.execute("ROLLBACK")
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())