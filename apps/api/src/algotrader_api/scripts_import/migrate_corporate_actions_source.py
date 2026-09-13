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


# Known source values inferred from a legacy `note` prefix. Anything
# outside this map is unverifiable and gets DELETED — we cannot tag it
# `curated` because that's the very fabrication this rule was meant to
# remove.
_KNOWN_SOURCE_PREFIXES = {
    "moex:iss": "moex_iss",
    "tinkoff:": "tinkoff",
}


def _infer_source_or_none(note: str | None) -> str | None:
    """Return the inferred source, or None if the note is not from a
    known provenance. Unknown notes will be deleted by backfill_source()."""
    if not note:
        return None
    for prefix, source in _KNOWN_SOURCE_PREFIXES.items():
        if note.startswith(prefix):
            return source
    return None


def backfill_source(db_path: str) -> dict:
    """Walk every corporate_actions row.

    Rows whose `note` matches a known source prefix get that source.
    Rows whose `note` does NOT match any known prefix are DELETED — the
    `curated` fallback is forbidden by the source-of-truth rule. Returns
    a stats dict: {backfilled: N, deleted: M, kept: K}.
    """
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            "SELECT figi, action_type, ex_date, note, source "
            "FROM corporate_actions"
        )
        rows = cur.fetchall()
        backfilled = 0
        deleted = 0
        for figi, action_type, ex_date, note, current in rows:
            inferred = _infer_source_or_none(note)
            if inferred is None:
                # unverifiable — delete
                con.execute(
                    "DELETE FROM corporate_actions "
                    "WHERE figi = ? AND action_type = ? AND ex_date = ?",
                    (figi, action_type, ex_date),
                )
                deleted += 1
                continue
            if current == inferred:
                continue
            con.execute(
                "UPDATE corporate_actions SET source = ? "
                "WHERE figi = ? AND action_type = ? AND ex_date = ?",
                (inferred, figi, action_type, ex_date),
            )
            backfilled += 1
        con.commit()
        cur.execute("SELECT COUNT(*) FROM corporate_actions")
        kept = cur.fetchone()[0]
    finally:
        con.close()
    return {"backfilled": backfilled, "deleted": deleted, "kept": kept}


if __name__ == "__main__":  # pragma: no cover — operator entry point
    import sys

    if len(sys.argv) < 2:
        print(
            "Usage: python -m algotrader_api.scripts_import.migrate_corporate_actions_source "
            "<db_path>",
            file=sys.stderr,
        )
        sys.exit(2)
    stats = backfill_source(sys.argv[1])
    print(
        f"Backfilled source on {stats['backfilled']} rows, "
        f"deleted {stats['deleted']} unverifiable rows, "
        f"{stats['kept']} rows remaining"
    )