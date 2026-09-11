"""Lifespan integrity check: orphan `ok` rows get flagged as error.

When `_flag_orphan_ok_rows` runs at startup, it flips `instrument_metadata`
rows that claim `status='ok'` but the corresponding figi has no bars in
the DuckDB `bars` view. These rows are false-positive bookkeeping left
over from earlier broken runs and would otherwise silently satisfy the
UI's "up to date" counter.
"""
from __future__ import annotations

import sqlite3

import duckdb
import pytest
from fastapi.testclient import TestClient

from algotrader_api.main import create_app


@pytest.fixture
def bars_db(tmp_path):
    """Spin up a self-contained env with a single empty parquet.

    DuckDB needs *something* matching the `*.parquet` glob before it
    will register a non-empty view; we write a tiny parquet with the
    AFKS ticker so the orphan check can see exactly one figi with bars.
    """
    bars_dir = tmp_path / "bars"
    bars_dir.mkdir()
    conn = duckdb.connect(":memory:")
    conn.execute(
        f"COPY (SELECT 'AFKS'::VARCHAR AS ticker, DATE '2026-09-01' AS ts, "
        f"100.0 AS open, 101.0 AS high, 99.0 AS low, 100.5 AS close, "
        f"1000::BIGINT AS volume, 100.5 AS adj_close) "
        f"TO '{bars_dir}/afks.parquet' (FORMAT PARQUET)"
    )
    conn.close()
    return bars_dir


@pytest.fixture
def app_with_orphans(tmp_path, monkeypatch, bars_db):
    """Build a fresh app whose DB has one real and one orphan `ok` row."""
    monkeypatch.setenv("ALGOTRADER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALGOTRADER_LOG_SAMPLE_HEALTH", "1.0")
    # Seed instruments: AFKS (real bars) and ABCD (no bars on disk).
    con = sqlite3.connect(str(tmp_path / "state.db"))
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS instruments (
            ticker TEXT PRIMARY KEY, figi TEXT UNIQUE, class TEXT, name TEXT,
            currency TEXT, lot_size INTEGER, isin TEXT, sector TEXT,
            source_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS instrument_metadata (
            figi TEXT PRIMARY KEY,
            last_bar_ts TEXT,
            last_run_at TEXT,
            total_bars INTEGER,
            last_run_status TEXT,
            last_error TEXT
        );
        """
    )
    con.executemany(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("AFKS", "FIGI-AFKS", "share", "AFK Sistema", "rub", 100),
            ("ABCD", "FIGI-ABCD", "share", "Test Co", "rub", 100),
        ],
    )
    con.executemany(
        "INSERT INTO instrument_metadata (figi, last_run_status, total_bars, last_bar_ts) "
        "VALUES (?, 'ok', ?, ?)",
        [
            ("FIGI-AFKS", 200, "2026-09-08"),
            ("FIGI-ABCD", 999, "2026-09-08"),
        ],
    )
    con.commit()
    con.close()
    app = create_app()
    with TestClient(app):
        yield app


def test_orphan_ok_rows_get_flagged_as_error(app_with_orphans, tmp_path):
    """After lifespan startup, FIGI-ABCD should no longer be 'ok'."""
    db = sqlite3.connect(str(tmp_path / "state.db"))
    rows = db.execute(
        "SELECT figi, last_run_status, last_error FROM instrument_metadata ORDER BY figi"
    ).fetchall()
    statuses = {figi: (status, err) for figi, status, err in rows}
    # Real one keeps its status
    assert statuses["FIGI-AFKS"][0] == "ok"
    # Orphan flipped to error with a clear reason
    assert statuses["FIGI-ABCD"][0] == "error"
    assert "bars_missing_on_disk" in (statuses["FIGI-ABCD"][1] or "")
