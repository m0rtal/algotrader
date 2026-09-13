"""Tests for migration 009 (source column) and the backfill script.

Three behaviours to verify:
1. After run_migrations, corporate_actions has a nullable `source` column.
2. backfill_source() infers source from the legacy `note` prefix:
     `moex:iss`     -> 'moex_iss'
     `tinkoff: ...` -> 'tinkoff'
     anything else  -> 'curated'
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


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """Migrated DB on a fresh temp path. Includes migration 009."""
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    yield p


def _seed_figi(db_path: str, figi: str = "BBG004730N88") -> None:
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO instruments(figi, ticker, class, name, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (figi, "SBER", "share", "Sber", "rub", 10),
    )
    con.commit()
    con.close()


# --- tests -----------------------------------------------------------------


def test_migration_009_adds_source_column(db):
    """After run_migrations, `source` is a nullable TEXT column."""
    con = sqlite3.connect(db)
    cols = [row[1] for row in con.execute("PRAGMA table_info(corporate_actions)")]
    assert "source" in cols
    # Source is nullable: `notnull` flag from PRAGMA table_info is 0 for nullable cols.
    info = con.execute("PRAGMA table_info(corporate_actions)").fetchall()
    source_row = next(r for r in info if r[1] == "source")
    assert source_row[3] == 0  # notnull flag


def test_backfill_source_infers_from_note_prefix(db):
    """Each note-prefix maps to a known source value."""
    _seed_figi(db)
    rows = [
        CorporateActionRow(
            figi="BBG004730N88",
            action_type="dividend",
            ex_date=date(2024, 7, 8),
            factor=1.0,
            cash_amount=387.0,
            note="moex:iss",
        ),
        CorporateActionRow(
            figi="BBG004730N88",
            action_type="dividend",
            ex_date=date(2024, 8, 1),
            factor=1.0,
            cash_amount=18.0,
            note="tinkoff: rub",
        ),
        CorporateActionRow(
            figi="BBG004730N88",
            action_type="split",
            ex_date=date(2011, 11, 15),
            factor=5.0,
            cash_amount=None,
            note="VTBR 5-for-1 (denomination)",
        ),
    ]
    merge_into_corporate_actions(db, rows)

    # Lazy import so the test file fails at collection-time before the
    # implementation exists.
    from algotrader_api.scripts_import.migrate_corporate_actions_source import (
        backfill_source,
    )

    n = backfill_source(db)
    assert n == 3

    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT ex_date, source FROM corporate_actions ORDER BY ex_date"
    ).fetchall()
    assert rows == [
        (date(2011, 11, 15).isoformat(), "curated"),
        (date(2024, 7, 8).isoformat(), "moex_iss"),
        (date(2024, 8, 1).isoformat(), "tinkoff"),
    ]


def test_backfill_source_is_idempotent(db):
    """Re-running backfill_source() leaves `source` unchanged; doesn't error."""
    _seed_figi(db)
    merge_into_corporate_actions(
        db,
        [
            CorporateActionRow(
                figi="BBG004730N88",
                action_type="dividend",
                ex_date=date(2024, 7, 8),
                factor=1.0,
                cash_amount=387.0,
                note="moex:iss",
            ),
        ],
    )

    from algotrader_api.scripts_import.migrate_corporate_actions_source import (
        backfill_source,
    )

    # First pass sets it.
    assert backfill_source(db) == 1
    con = sqlite3.connect(db)
    src_first = con.execute(
        "SELECT source FROM corporate_actions"
    ).fetchone()[0]
    assert src_first == "moex_iss"

    # Second pass: same value, no change — still returns 1 (rows visited)
    # but the table value is unchanged.
    assert backfill_source(db) == 1
    src_second = con.execute(
        "SELECT source FROM corporate_actions"
    ).fetchone()[0]
    assert src_second == "moex_iss"