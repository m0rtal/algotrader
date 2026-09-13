"""Tests for the cleanup maintenance helpers.

After `remove-duckdb-and-parquet`, `prune_non_tradeable_classes`
deletes rows from SQLite tables (`instruments`, `instrument_metadata`,
`ingestion_logs`, `bars`) — there are no parquet files to clean up.
"""
from __future__ import annotations

import sqlite3

import pytest

from algotrader_api.maintenance.cleanup import (
    CleanupSummary,
    prune_non_tradeable_classes,
    reconcile_with_broker,
)


def _seed_db(path: str) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE instruments (
            ticker TEXT PRIMARY KEY, figi TEXT UNIQUE, class TEXT,
            name TEXT, currency TEXT, lot_size INTEGER
        );
        CREATE TABLE instrument_metadata (
            figi TEXT PRIMARY KEY, last_bar_ts TEXT,
            last_backfilled_at TEXT, total_bars INTEGER NOT NULL DEFAULT 0,
            last_run_status TEXT, last_run_at TEXT, last_error TEXT
        );
        CREATE TABLE ingestion_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, run_id INTEGER NOT NULL,
            level TEXT NOT NULL, figi TEXT, message TEXT NOT NULL
        );
        CREATE TABLE bars (
            figi TEXT NOT NULL,
            ts TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            PRIMARY KEY (figi, ts)
        );
        """
    )
    con.executemany(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("SBER", "FIGI-SBER", "share", "Sber", "rub", 10),
            ("FXUS", "FIGI-FXUS", "etf", "FXUS", "rub", 1),
            ("OFZ", "FIGI-OFZ", "bond", "OFZ", "rub", 1),
            ("Si-9.26", "FIGI-FUT1", "future", "Future", "rub", 1),
            ("VB58CU6B", "FIGI-OPT1", "option", "Opt", "rub", 1),
        ],
    )
    con.executemany(
        "INSERT INTO instrument_metadata (figi, last_run_status, total_bars) "
        "VALUES (?, ?, ?)",
        [
            ("FIGI-SBER", "ok", 200),
            ("FIGI-FXUS", "ok", 200),
            ("FIGI-OFZ", "ok", 200),
            ("FIGI-FUT1", "ok", 200),
            ("FIGI-OPT1", "error", 0),
        ],
    )
    con.executemany(
        "INSERT INTO ingestion_logs (ts, run_id, level, figi, message) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("2026-01-01T00:00:00", 1, "warn", "FIGI-SBER", "kept"),
            ("2026-01-01T00:00:01", 1, "warn", "FIGI-FUT1", "drop me"),
            ("2026-01-01T00:00:02", 1, "warn", "FIGI-OPT1", "drop me"),
            ("2026-01-01T00:00:03", 1, "warn", "FIGI-ROOT", "keep me (orphan figi)"),
        ],
    )
    con.executemany(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("FIGI-SBER", "2026-01-01", 100.0, 110.0, 95.0, 105.0, 1000),
            ("FIGI-FUT1", "2026-01-01", 50.0, 55.0, 48.0, 53.0, 500),
            ("FIGI-OPT1", "2026-01-01", 20.0, 22.0, 19.0, 21.0, 200),
        ],
    )
    con.commit()
    con.close()


def test_prune_drops_non_tradeable_instruments_metadata_logs(tmp_path):
    db_path = str(tmp_path / "state.db")
    _seed_db(db_path)

    summary = prune_non_tradeable_classes(db_path)

    con = sqlite3.connect(db_path)
    rows = con.execute("SELECT figi, class FROM instruments ORDER BY figi").fetchall()
    assert rows == [
        ("FIGI-FXUS", "etf"),
        ("FIGI-OFZ", "bond"),
        ("FIGI-SBER", "share"),
    ], f"share/etf/bond must be kept; got {rows}"
    assert con.execute("SELECT COUNT(*) FROM instrument_metadata").fetchone()[0] == 3
    # Logs whose figi is null are kept (e.g. lifecycle events); the
    # FIGI-ROOT orphan log is also kept because no matching figi is
    # being deleted.
    log_figis = {r[0] for r in con.execute("SELECT figi FROM ingestion_logs").fetchall()}
    assert log_figis == {"FIGI-SBER", "FIGI-ROOT"}, f"kept logs: {log_figis}"
    # Bars for non-tradeable classes are deleted.
    bar_figis = {
        r[0]
        for r in con.execute(
            "SELECT DISTINCT figi FROM bars WHERE figi IS NOT NULL"
        ).fetchall()
    }
    assert bar_figis == {"FIGI-SBER"}, f"kept bars: {bar_figis}"
    con.close()

    assert summary.instruments_dropped == 2
    assert summary.metadata_dropped == 2
    assert summary.logs_dropped == 2
    assert summary.bars_dropped == 2
    assert summary.bars_bytes_freed > 0


def test_prune_dry_run_changes_nothing(tmp_path):
    db_path = str(tmp_path / "state.db")
    _seed_db(db_path)

    summary = prune_non_tradeable_classes(db_path, dry_run=True)

    con = sqlite3.connect(db_path)
    assert con.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 5
    assert con.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 3
    con.close()
    assert summary.instruments_dropped == 2
    assert summary.bars_dropped == 2
    assert summary.bars_bytes_freed > 0


def test_prune_is_idempotent(tmp_path):
    db_path = str(tmp_path / "state.db")
    _seed_db(db_path)

    first = prune_non_tradeable_classes(db_path)
    second = prune_non_tradeable_classes(db_path)

    assert first.instruments_dropped == 2
    assert second.instruments_dropped == 0
    assert second.metadata_dropped == 0
    assert second.logs_dropped == 0
    assert second.bars_dropped == 0


def test_reconcile_with_broker_adds_missing_rows(tmp_path):
    db_path = str(tmp_path / "state.db")
    _seed_db(db_path)
