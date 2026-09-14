"""Tests for issue #4: empty moex_holidays table thundering-herd fix.

The moex_holidays migration (006) creates the table but seeded no rows.
On a fresh install we got INCOMPLETE_HISTORY for every figi because
``_weekdays_excluding_holidays`` returned the raw weekday count without
subtracting the holiday count.

Fix:
1. Migration seeds the known 2020-2027 MOEX holiday calendar so the
   first guardian run after install has the data it needs.
2. ``compute_all`` logs a warning when the table is suspiciously empty
   so operators notice if the seed ever fails.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations


def test_migration_seeds_moex_holidays_table(tmp_path):
    """Fresh install must have holidays seeded, not just an empty table."""
    db_path = str(tmp_path / "state.db")
    run_migrations(db_path, str(MIGRATIONS_DIR))

    con = sqlite3.connect(db_path)
    try:
        count = con.execute("SELECT COUNT(*) FROM moex_holidays").fetchone()[0]
    finally:
        con.close()

    assert count >= 70, f"migration seeded only {count} holidays; expected >=70"


def test_migration_seed_matches_json_source(tmp_path):
    """The seeded rows must match the canonical JSON — no drift."""
    db_path = str(tmp_path / "state.db")
    run_migrations(db_path, str(MIGRATIONS_DIR))

    json_path = (
        Path("/home/hermes/algotrader/apps/api/src/algotrader_api/scripts_import")
        / "data" / "moex_holidays.json"
    )
    expected = {entry["date"] for entry in json.loads(json_path.read_text())}

    con = sqlite3.connect(db_path)
    try:
        actual = {row[0] for row in con.execute("SELECT date FROM moex_holidays").fetchall()}
    finally:
        con.close()

    assert expected == actual, f"row drift: missing={expected-actual}, extra={actual-expected}"


def test_migration_seed_is_idempotent(tmp_path):
    """Re-running migrations on an existing DB must not double-insert."""
    db_path = str(tmp_path / "state.db")
    run_migrations(db_path, str(MIGRATIONS_DIR))
    con = sqlite3.connect(db_path)
    first_count = con.execute("SELECT COUNT(*) FROM moex_holidays").fetchone()[0]
    con.close()

    run_migrations(db_path, str(MIGRATIONS_DIR))
    con = sqlite3.connect(db_path)
    second_count = con.execute("SELECT COUNT(*) FROM moex_holidays").fetchone()[0]
    con.close()

    assert first_count == second_count


def test_compute_all_warns_on_empty_holidays_table(caplog):
    """Defensive guard: log a warning if the holidays table is empty so
    operators notice the missing-data signal at first guardian run.
    """
    import logging

    from algotrader_api.data_quality.health import compute_all

    # Build a fresh DB with NO holidays. We can't run run_migrations here
    # because migration 006 will seed — instead we drop the table after.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "state.db")
        run_migrations(db_path, str(MIGRATIONS_DIR))
        con = sqlite3.connect(db_path)
        con.execute("DELETE FROM moex_holidays")
        con.commit()
        con.close()

        with caplog.at_level(logging.WARNING, logger="algotrader_api.data_quality.health"):
            compute_all(db_path)
        # `caplog` is captured across loggers; we look for our specific event.
        msgs = [rec.message for rec in caplog.records]
        assert any("moex_holidays" in m and "empty" in m.lower() for m in msgs), \
            f"expected empty-holidays warning, got: {msgs}"


# ── defensive-branch coverage (QA #18) ──────────────────────────────


def test_seed_helper_skips_when_table_does_not_exist(tmp_path):
    """Pre-migration-006 databases: ``moex_holidays`` doesn't exist yet.

    The ``OperationalError`` branch in ``_seed_moex_holidays_if_missing``
    must return silently rather than raise — the migration runner
    itself must not crash because the table it was about to seed
    doesn't exist yet (it gets created by migration 006 *during*
    this run, so the race only triggers on a DB that pre-dates 006).
    """
    import sqlite3
    from algotrader_api.db.sqlite import _seed_moex_holidays_if_missing

    db_path = str(tmp_path / "state.db")
    # Open a DB WITHOUT running migrations — no moex_holidays table.
    con = sqlite3.connect(db_path)
    # Should not raise.
    _seed_moex_holidays_if_missing(con, db_path)
    con.close()


def test_seed_helper_skips_when_table_already_populated(tmp_path):
    """Direct coverage of the ``row[0] > 0`` early-return.

    When the table is non-empty (operator ran the import script,
    or a previous ``run_migrations`` already seeded) the helper
    must not call ``import_moex_holidays`` again. We assert the
    row count is preserved exactly — no duplicates, no re-seed.
    """
    from algotrader_api.db.sqlite import _seed_moex_holidays_if_missing

    db_path = str(tmp_path / "state.db")
    run_migrations(db_path, str(MIGRATIONS_DIR))

    con = sqlite3.connect(db_path)
    before = con.execute("SELECT COUNT(*) FROM moex_holidays").fetchone()[0]
    _seed_moex_holidays_if_missing(con, db_path)
    after = con.execute("SELECT COUNT(*) FROM moex_holidays").fetchone()[0]
    con.close()

    assert before > 0  # migration seeded
    assert before == after  # second call was a no-op


def test_seed_helper_returns_silently_on_import_failure(tmp_path, monkeypatch):
    """Coverage of the ``except Exception`` branch on the import.

    If ``import_moex_holidays`` raises (missing JSON, broken path,
    etc.) the helper must return silently rather than crash the
    migration runner — the table will simply remain empty and the
    ``_warn_if_holidays_empty`` defensive guard will surface the
    issue at compute time.
    """
    import builtins

    from algotrader_api.db.sqlite import _seed_moex_holidays_if_missing

    db_path = str(tmp_path / "state.db")
    run_migrations(db_path, str(MIGRATIONS_DIR))
    con = sqlite3.connect(db_path)
    # Wipe so the seed branch is taken (not the >0 early-return).
    con.execute("DELETE FROM moex_holidays")
    con.commit()

    # Force the import to raise — the helper's except-clause must
    # catch it. We patch builtins.__import__ to fail only for the
    # ``import_moex_holidays`` module name.
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if "import_moex_holidays" in name:
            raise ImportError("simulated broken JSON")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    try:
        # Should NOT raise.
        _seed_moex_holidays_if_missing(con, db_path)
    finally:
        con.close()

    # The table is still empty — exactly the scenario the
    # ``_warn_if_holidays_empty`` guard catches downstream.
    con = sqlite3.connect(db_path)
    try:
        assert con.execute("SELECT COUNT(*) FROM moex_holidays").fetchone()[0] == 0
    finally:
        con.close()
