"""Tests for the shared corporate_actions writer (Task 1).

Covers INSERT, idempotency, conflict update, and action_type validation.
Uses the same per-file `db` fixture pattern as
`tests/test_data_quality_service.py` (no shared conftest fixture).
"""
from datetime import date

import pytest

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations
from algotrader_api.scripts_import.import_corporate_actions_common import (
    CorporateActionRow,
    InvalidActionType,
    merge_into_corporate_actions,
)


@pytest.fixture
def db(tmp_path):
    """Migrated DB on a fresh temp path."""
    import sqlite3

    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    yield p


def _seed_figi(db_path: str, figi: str = "BBG004730N88") -> None:
    import sqlite3

    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO instruments(figi, ticker, class, name, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (figi, "SBER", "share", "Sber", "rub", 10),
    )
    con.commit()
    con.close()


def test_merge_inserts_new_rows(db):
    _seed_figi(db)
    rows = [
        CorporateActionRow(
            figi="BBG004730N88",
            action_type="split",
            ex_date=date(2020, 6, 19),
            factor=2.0,
            cash_amount=None,
            note="test",
        ),
    ]
    n = merge_into_corporate_actions(db, rows)
    assert n == 1
    import sqlite3

    con = sqlite3.connect(db)
    cur = con.execute(
        "SELECT factor FROM corporate_actions WHERE figi=? AND action_type='split'",
        ("BBG004730N88",),
    )
    assert cur.fetchone()[0] == 2.0


def test_merge_is_idempotent(db):
    _seed_figi(db)
    rows = [
        CorporateActionRow(
            "BBG004730N88", "dividend", date(2024, 7, 8), 1.0, 387.0, "test"
        ),
    ]
    assert merge_into_corporate_actions(db, rows) == 1
    assert merge_into_corporate_actions(db, rows) == 1
    import sqlite3

    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0] == 1


def test_merge_updates_existing_row_on_conflict(db):
    _seed_figi(db)
    rows_old = [
        CorporateActionRow(
            "BBG004730N88", "dividend", date(2024, 7, 8), 1.0, 100.0, "old"
        )
    ]
    rows_new = [
        CorporateActionRow(
            "BBG004730N88", "dividend", date(2024, 7, 8), 1.0, 387.0, "new"
        )
    ]
    merge_into_corporate_actions(db, rows_old)
    merge_into_corporate_actions(db, rows_new)
    import sqlite3

    con = sqlite3.connect(db)
    cash = con.execute(
        "SELECT cash_amount, note FROM corporate_actions WHERE figi=?",
        ("BBG004730N88",),
    ).fetchone()
    assert cash == (387.0, "new")


def test_merge_validates_action_type(db):
    """Validation fires in the dataclass __post_init__; constructor rejects."""
    _seed_figi(db)
    with pytest.raises(InvalidActionType):
        CorporateActionRow(
            "BBG004730N88", "coupon", date(2024, 7, 8), 1.0, 100.0, "x"
        )


def test_merge_rejects_zero_factor(db):
    """factor must be > 0 (CHECK constraint & schema sanity)."""
    _seed_figi(db)
    with pytest.raises(ValueError):
        CorporateActionRow(
            "BBG004730N88", "split", date(2020, 6, 19), 0.0, None, "x"
        )


def test_merge_empty_rows_returns_zero(db):
    """No rows → no work, return 0, no DB write."""
    assert merge_into_corporate_actions(db, []) == 0


def test_merge_persists_source_column(db):
    """Regression: the writer must persist the `source` field set on the
    CorporateActionRow. Without this, the `source` column is decorative —
    set on the dataclass but never written to SQLite."""
    import sqlite3

    _seed_figi(db)
    row = CorporateActionRow(
        figi="BBG004730N88",
        action_type="dividend",
        ex_date=date(2024, 7, 8),
        factor=1.0,
        cash_amount=387.0,
        note="x",
        source="test:source",
    )
    merge_into_corporate_actions(db, [row])
    con = sqlite3.connect(db)
    src = con.execute(
        "SELECT source FROM corporate_actions WHERE figi=?",
        ("BBG004730N88",),
    ).fetchone()[0]
    assert src == "test:source"
