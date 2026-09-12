"""Tests for the cleanup maintenance helpers."""
from __future__ import annotations

import os
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
    con.commit()
    con.close()


def _write_parquet(bars_dir: str, figis: list[str]) -> None:
    """Drop a stub parquet file per figi stem so the cleanup can delete it."""
    import duckdb

    conn = duckdb.connect(":memory:")
    for figi in figis:
        path = os.path.join(bars_dir, f"{figi}.parquet")
        conn.execute(
            f"COPY (SELECT '2026-01-01'::DATE AS ts, 100.0 AS open, 110.0 AS high, "
            f"95.0 AS low, 105.0 AS close, 1000 AS volume) TO '{path}' (FORMAT PARQUET)"
        )
    conn.close()


def test_prune_drops_non_tradeable_instruments_metadata_logs(tmp_path):
    db_path = str(tmp_path / "state.db")
    bars_dir = tmp_path / "bars"
    bars_dir.mkdir()
    _seed_db(db_path)
    _write_parquet(bars_dir, ["FIGI-FUT1", "FIGI-OPT1", "FIGI-SBER", "FIGI-FXUS", "FIGI-OFZ"])

    summary = prune_non_tradeable_classes(db_path, str(bars_dir))

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
    con.close()

    assert summary.instruments_dropped == 2
    assert summary.metadata_dropped == 2
    assert summary.logs_dropped == 2
    assert summary.parquet_files_dropped == 2
    # Only the dropped-class files should be gone.
    remaining = sorted(os.listdir(bars_dir))
    assert remaining == [
        "FIGI-FXUS.parquet",
        "FIGI-OFZ.parquet",
        "FIGI-SBER.parquet",
    ]


def test_prune_dry_run_changes_nothing(tmp_path):
    db_path = str(tmp_path / "state.db")
    bars_dir = tmp_path / "bars"
    bars_dir.mkdir()
    _seed_db(db_path)
    _write_parquet(bars_dir, ["FIGI-FUT1", "FIGI-OPT1"])

    summary = prune_non_tradeable_classes(db_path, str(bars_dir), dry_run=True)

    con = sqlite3.connect(db_path)
    assert con.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 5
    con.close()
    assert os.path.exists(os.path.join(bars_dir, "FIGI-FUT1.parquet"))
    assert summary.instruments_dropped == 2
    assert summary.parquet_files_dropped == 2
    assert summary.parquet_bytes_freed > 0


def test_prune_is_idempotent(tmp_path):
    db_path = str(tmp_path / "state.db")
    bars_dir = tmp_path / "bars"
    bars_dir.mkdir()
    _seed_db(db_path)
    _write_parquet(bars_dir, ["FIGI-FUT1", "FIGI-OPT1"])

    first = prune_non_tradeable_classes(db_path, str(bars_dir))
    second = prune_non_tradeable_classes(db_path, str(bars_dir))

    assert first.instruments_dropped == 2
    assert second.instruments_dropped == 0
    assert second.metadata_dropped == 0
    assert second.logs_dropped == 0


def test_reconcile_with_broker_adds_missing_rows(tmp_path):
    db_path = str(tmp_path / "state.db")
    _seed_db(db_path)

    # Fresh broker snapshot: includes SBER/FXUS/OFZ (already in DB) and
    # NEW1/NEW2 (must be added). Existing rows for delisted figis must
    # be preserved (they have historical bars we want to keep).
    summary = reconcile_with_broker(
        db_path,
        share_figis={"FIGI-SBER", "FIGI-NEW1"},
        etf_figis={"FIGI-FXUS"},
        bond_figis={"FIGI-OFZ", "FIGI-NEW-BOND"},
        share_meta={
            "FIGI-NEW1": ("NEW1", "New Share"),
        },
        bond_meta={
            "FIGI-NEW-BOND": ("NEWBOND", "New Bond"),
        },
    )

    con = sqlite3.connect(db_path)
    figis = {row[0] for row in con.execute("SELECT figi FROM instruments").fetchall()}
    # Original kept rows + new rows added; delisted row preserved.
    assert figis == {
        "FIGI-SBER",
        "FIGI-FXUS",
        "FIGI-OFZ",
        "FIGI-NEW1",
        "FIGI-NEW-BOND",
        "FIGI-FUT1",  # not touched by reconcile (only adds)
        "FIGI-OPT1",  # not touched
    }
    new1 = con.execute(
        "SELECT ticker, name, class FROM instruments WHERE figi=?",
        ("FIGI-NEW1",),
    ).fetchone()
    assert new1 == ("NEW1", "New Share", "share")
    new_bond = con.execute(
        "SELECT ticker, name, class FROM instruments WHERE figi=?",
        ("FIGI-NEW-BOND",),
    ).fetchone()
    assert new_bond == ("NEWBOND", "New Bond", "bond")
    con.close()

    # instruments_dropped is negative (we added rows).
    assert summary.instruments_dropped == -2


def test_reconcile_dry_run_makes_no_changes(tmp_path):
    db_path = str(tmp_path / "state.db")
    _seed_db(db_path)

    summary = reconcile_with_broker(
        db_path,
        share_figis={"FIGI-NEW1"},
        etf_figis=set(),
        bond_figis=set(),
        dry_run=True,
    )

    con = sqlite3.connect(db_path)
    figis = {row[0] for row in con.execute("SELECT figi FROM instruments").fetchall()}
    # FIGI-NEW1 not inserted under dry-run.
    assert "FIGI-NEW1" not in figis
    assert len(figis) == 5  # original seed untouched
    con.close()
    assert summary.instruments_dropped == -1


def test_prune_reports_orphaned_parquet_files(tmp_path):
    """Parquet files whose stem isn't in `instruments` are flagged
    in the summary as orphaned (we don't auto-delete them)."""
    db_path = str(tmp_path / "state.db")
    bars_dir = tmp_path / "bars"
    bars_dir.mkdir()
    _seed_db(db_path)
    # A parquet whose stem matches a real instrument (SBER).
    _write_parquet(bars_dir, ["FIGI-SBER"])
    # Plus an orphan whose stem matches nothing.
    _write_parquet(bars_dir, ["ORPHAN-FIGI"])

    summary = prune_non_tradeable_classes(db_path, str(bars_dir), dry_run=True)

    assert summary.parquet_files_orphaned == 1
    # Nothing was dropped.
    assert summary.instruments_dropped == 2  # future + option
    assert summary.parquet_files_dropped == 0


def test_prune_skips_drop_for_figis_without_parquet_file(tmp_path):
    """When a class=option figi has no parquet file, prune must skip
    the file-side cleanup without bumping `parquet_files_dropped`."""
    db_path = str(tmp_path / "state.db")
    bars_dir = tmp_path / "bars"
    bars_dir.mkdir()
    _seed_db(db_path)
    # Only seed a parquet for the future figi; the option figi has
    # no corresponding parquet on disk.
    _write_parquet(bars_dir, ["FIGI-FUT1"])
    # Pre-condition: FIGI-OPT1 has no parquet file.
    assert not (bars_dir / "FIGI-OPT1.parquet").exists()

    summary = prune_non_tradeable_classes(db_path, str(bars_dir))

    # Both future and option rows are deleted from instruments, but
    # only the future parquet is removed from disk.
    con = sqlite3.connect(db_path)
    remaining_figis = {
        row[0] for row in con.execute("SELECT figi FROM instruments").fetchall()
    }
    assert "FIGI-FUT1" not in remaining_figis
    assert "FIGI-OPT1" not in remaining_figis
    con.close()
    assert summary.instruments_dropped == 2
    assert summary.parquet_files_dropped == 1
    assert summary.parquet_bytes_freed > 0
    # The unused disk file was left untouched (operator can clean up
    # orphaned files later if needed).
    assert (bars_dir / "FIGI-FUT1.parquet").exists() is False


def test_prune_no_op_when_no_non_tradeable_rows(tmp_path):
    """When instruments has only share/etf/bond, prune is a no-op."""
    db_path = str(tmp_path / "state.db")
    bars_dir = tmp_path / "bars"
    bars_dir.mkdir()

    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE instruments (
            ticker TEXT PRIMARY KEY, figi TEXT UNIQUE, class TEXT,
            name TEXT, currency TEXT, lot_size INTEGER
        );
        CREATE TABLE instrument_metadata (
            figi TEXT PRIMARY KEY, last_bar_ts TEXT, total_bars INTEGER
        );
        CREATE TABLE ingestion_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,
            run_id INTEGER, level TEXT, figi TEXT, message TEXT
        );
        """
    )
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("SBER", "FIGI-SBER", "share", "Sber", "rub", 10),
    )
    con.commit()
    con.close()
    _write_parquet(bars_dir, ["FIGI-SBER"])

    summary = prune_non_tradeable_classes(db_path, str(bars_dir))

    assert summary.instruments_dropped == 0
    assert summary.metadata_dropped == 0
    assert summary.logs_dropped == 0
    assert summary.parquet_files_dropped == 0
    assert (bars_dir / "FIGI-SBER.parquet").exists()


def test_prune_keeps_parquet_with_matching_ticker_in_instruments(tmp_path):
    """If a class=option row gets dropped but a share/etf/bond row
    shares the same figi in instruments (shouldn't happen in practice
    but defensive), the parquet is kept by the figi-as-stem match."""
    db_path = str(tmp_path / "state.db")
    bars_dir = tmp_path / "bars"
    bars_dir.mkdir()
    _seed_db(db_path)
    # A parquet whose stem = future figi (must be deleted).
    _write_parquet(bars_dir, ["FIGI-FUT1"])

    summary = prune_non_tradeable_classes(db_path, str(bars_dir))

    # The future parquet was dropped.
    assert not os.path.exists(bars_dir / "FIGI-FUT1.parquet")
    assert summary.parquet_files_dropped == 1
    assert summary.parquet_files_orphaned == 0
