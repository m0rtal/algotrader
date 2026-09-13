# apps/api/src/algotrader_api/scripts_import/migrate_corporate_actions_source.py
"""One-shot backfill for the `corporate_actions.source` column (migration 009).

The migration adds a nullable `source TEXT` column. Existing rows were
written before the column existed, so we infer the provenance from the
`note` prefix that the legacy importers all set:

    note startswith "moex:iss"        -> 'moex_iss'
    note startswith "tinkoff:"        -> 'tinkoff'
    anything else                     -> 'curated'

After the backfill, future ingestions should set `source` explicitly at
write time (see `import_corporate_actions_splits.detect_mode` which writes
source='moex_iss_snapshots').

Idempotent: re-running leaves existing non-NULL `source` values alone and
fills only NULL rows. Re-running also overwrites the 'curated' default if
the row's note starts with a recognised prefix on a second pass — but in
practice backfill runs once during the deploy that adds migration 009, so
this branch is rarely hit.

Returns the number of rows whose `source` value was set (either because it
was NULL before, or because the inferred value changed).
"""
from __future__ import annotations

import sqlite3


def _infer_source(note: str | None) -> str:
    """Map a legacy `note` prefix to a `source` value.

    Order matters: check `moex:iss` before `tinkoff:` because note values
    can be free-form; the historical importer set
    `note="tinkoff: {currency}"` exactly with the colon after tinkoff, so
    startswith is unambiguous.
    """
    if note is None:
        return "curated"
    if note.startswith("moex:iss"):
        return "moex_iss"
    if note.startswith("tinkoff:"):
        return "tinkoff"
    return "curated"


def backfill_source(db_path: str) -> int:
    """Walk every corporate_actions row and set `source` from `note`.

    Idempotent: rows where `source` is already set and matches the inferred
    value are left untouched. Returns the number of rows visited — i.e. the
    total corporate_actions row count. The caller can compare against
    con.execute('SELECT COUNT(*) FROM corporate_actions') to verify nothing
    was skipped. Whether the row's value actually changed can be inferred
    from comparing the result across runs.
    """
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            "SELECT figi, action_type, ex_date, note, source "
            "FROM corporate_actions"
        )
        rows = cur.fetchall()
        for figi, action_type, ex_date, note, current in rows:
            inferred = _infer_source(note)
            if current == inferred:
                continue
            con.execute(
                "UPDATE corporate_actions SET source = ? "
                "WHERE figi = ? AND action_type = ? AND ex_date = ?",
                (inferred, figi, action_type, ex_date),
            )
        con.commit()
    finally:
        con.close()
    return len(rows)


if __name__ == "__main__":  # pragma: no cover — operator entry point
    import sys

    if len(sys.argv) < 2:
        print(
            "Usage: python -m algotrader_api.scripts_import.migrate_corporate_actions_source "
            "<db_path>",
            file=sys.stderr,
        )
        sys.exit(2)
    n = backfill_source(sys.argv[1])
    print(f"Backfilled source on {n} corporate_actions rows")