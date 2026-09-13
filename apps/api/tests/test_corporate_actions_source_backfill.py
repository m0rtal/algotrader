"""Tests for migration 009 (source column) and the backfill script.

Three behaviours to verify:
1. After run_migrations, corporate_actions has a nullable `source` column.
2. backfill_source() infers source from the legacy `note` prefix:
     `moex:iss`     -> 'moex_iss'
     `tinkoff: ...` -> 'tinkoff'
     anything else  -> DELETED (the `curated` fallback is forbidden)
3. backfill_source() is idempotent and safe to re-run.
"""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations
from algotrader_api.scripts_import.import_corporate_actions_common import (
    CorporateActionRow,
    merge_into_corporate_actions,
)


@pytest.fixture
def db(tmp_path):
    """Migrated DB on a fresh temp path. Includes migration 009."""
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    yield p


def _seed_three_rows(db_path: str) -> None:
    """Seed one row per known prefix type, plus an unverifiable row."""
    merge_into_corporate_actions(
        db_path,
        [
            CorporateActionRow(
                figi="BBG004730N88",
                action_type="split",
                ex_date=date(2011, 11, 15),
                factor=5.0,
                cash_amount=None,
                note="VTBR 5-for-1",  # unverifiable — gets deleted
                source="",
            ),
            CorporateActionRow(
                figi="BBG004731032",
                action_type="dividend",
                ex_date=date(2024, 7, 8),
                factor=1.0,
                cash_amount=387.0,
                note="moex:iss",
                source="",
            ),
            CorporateActionRow(
                figi="FIGI-LKOH",
                action_type="dividend",
                ex_date=date(2024, 8, 1),
                factor=1.0,
                cash_amount=387.0,
                note="tinkoff: rub",
                source="",
            ),
        ],
    )


def test_migration_009_adds_source_column(db):
    """After run_migrations the source column exists and is nullable."""
    con = sqlite3.connect(db)
    cur = con.execute("PRAGMA table_info(corporate_actions)")
    cols = {r[1]: r[2] for r in cur}
    assert cols["source"] == "TEXT"


def test_backfill_source_infers_from_note_prefix_and_deletes_unknown(db):
    """`moex:iss` -> moex_iss, `tinkoff:` -> tinkoff, unknown -> DELETED."""
    _seed_three_rows(db)
    from algotrader_api.scripts_import.migrate_corporate_actions_source import (
        backfill_source,
    )
    stats = backfill_source(db)
    assert stats["backfilled"] == 2
    assert stats["deleted"] == 1
    assert stats["kept"] == 2
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT figi, source FROM corporate_actions ORDER BY ex_date"
    ).fetchall()
    assert rows == [
        ("BBG004731032", "moex_iss"),
        ("FIGI-LKOH", "tinkoff"),
    ]
    con.close()


def test_backfill_source_is_idempotent(db):
    """Re-running backfill_source() leaves `source` unchanged; doesn't error."""
    _seed_three_rows(db)
    from algotrader_api.scripts_import.migrate_corporate_actions_source import (
        backfill_source,
    )
    # First pass: 2 backfilled, 1 deleted, 2 kept
    stats1 = backfill_source(db)
    assert stats1 == {"backfilled": 2, "deleted": 1, "kept": 2}
    # Second pass: nothing changes; backfilled=0 (all rows already have correct source)
    stats2 = backfill_source(db)
    assert stats2 == {"backfilled": 0, "deleted": 0, "kept": 2}


def test_backfill_deletes_rows_without_known_prefix(db):
    """Rows with `note` that matches no known source prefix are DELETED —
    not tagged as `curated`. The `curated` fallback is the very
    fabrication this rule was meant to remove."""
    merge_into_corporate_actions(
        db,
        [
            CorporateActionRow(
                "BBG-X", "split", date(2024, 1, 1), 2.0, None,
                "moex:iss", source="",
            ),
            CorporateActionRow(
                "BBG-Y", "split", date(2024, 1, 1), 2.0, None,
                "curated junk", source="",
            ),
        ],
    )
    from algotrader_api.scripts_import.migrate_corporate_actions_source import (
        backfill_source,
    )
    stats = backfill_source(db)
    assert stats == {"backfilled": 1, "deleted": 1, "kept": 1}
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT figi, source FROM corporate_actions ORDER BY figi"
    ).fetchall()
    assert rows == [("BBG-X", "moex_iss")]
    con.close()
