"""Tests for pipeline phase tracking."""
from __future__ import annotations

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db import sqlite as sqlitedb
from algotrader_api.ingestion import pipeline


def test_start_phase_inserts_idle_row(tmp_path):
    db_path = str(tmp_path / "test.db")
    sqlitedb.run_migrations(db_path, MIGRATIONS_DIR)
    rid = pipeline.start_phase(db_path, "discover_universe")
    assert rid > 0
    row = sqlitedb.execute(db_path, "SELECT status, phase FROM pipeline WHERE id = ?", (rid,))[0]
    assert row["status"] == "idle"
    assert row["phase"] == "discover_universe"


def test_end_phase_updates_status_and_rows(tmp_path):
    db_path = str(tmp_path / "test.db")
    sqlitedb.run_migrations(db_path, MIGRATIONS_DIR)
    rid = pipeline.start_phase(db_path, "fetch_bars")
    pipeline.end_phase(db_path, rid, status="ok", rows_processed=12345)
    row = sqlitedb.execute(db_path, "SELECT status, rows_processed, finished_at FROM pipeline WHERE id = ?", (rid,))[0]
    assert row["status"] == "ok"
    assert row["rows_processed"] == 12345
    assert row["finished_at"] is not None


def test_end_phase_supports_err_status_with_detail(tmp_path):
    db_path = str(tmp_path / "test.db")
    sqlitedb.run_migrations(db_path, MIGRATIONS_DIR)
    rid = pipeline.start_phase(db_path, "fetch_bars")
    pipeline.end_phase(db_path, rid, status="err", detail="RESOURCE_EXHAUSTED")
    row = sqlitedb.execute(db_path, "SELECT status, detail FROM pipeline WHERE id = ?", (rid,))[0]
    assert row["status"] == "err"
    assert row["detail"] == "RESOURCE_EXHAUSTED"


def test_latest_per_phase_returns_only_latest(tmp_path):
    db_path = str(tmp_path / "test.db")
    sqlitedb.run_migrations(db_path, MIGRATIONS_DIR)
    rid1 = pipeline.start_phase(db_path, "fetch_bars")
    pipeline.end_phase(db_path, rid1, status="ok", rows_processed=100)
    rid2 = pipeline.start_phase(db_path, "fetch_bars")
    pipeline.end_phase(db_path, rid2, status="ok", rows_processed=200)

    results = pipeline.latest_per_phase(db_path)
    assert len(results) == 1
    assert results[0].id == rid2
    assert results[0].rows_processed == 200


def test_latest_per_phase_returns_one_per_phase_name(tmp_path):
    db_path = str(tmp_path / "test.db")
    sqlitedb.run_migrations(db_path, MIGRATIONS_DIR)
    pipeline.start_phase(db_path, "discover_universe")
    pipeline.start_phase(db_path, "fetch_bars")
    results = pipeline.latest_per_phase(db_path)
    assert len(results) == 2
    phases = {r.phase for r in results}
    assert phases == {"discover_universe", "fetch_bars"}


def test_latest_per_phase_empty_returns_empty_list(tmp_path):
    db_path = str(tmp_path / "test.db")
    sqlitedb.run_migrations(db_path, MIGRATIONS_DIR)
    assert pipeline.latest_per_phase(db_path) == []
