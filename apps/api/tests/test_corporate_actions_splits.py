"""Tests for the MOEX-ISS face_value diff split detector (Task 2).

Two modes live in import_corporate_actions_splits:

  - snapshot_mode(db_path, *, observed_at=None, fetcher=None)
      For every tradeable figi in `instruments`, fetch current face_value
      and lot_size from MOEX ISS and INSERT OR IGNORE into
      `instruments_snapshot(secid, observed_at, face_value, lot_size)`.
      The fetcher is injectable so tests can mock the network.

  - detect_mode(db_path)
      Self-join consecutive (secid, observed_at) pairs in
      `instruments_snapshot` where face_value changed. Emits one
      CorporateActionRow per change with factor = new / prev and
      source='moex_iss_snapshots'. INSERT OR IGNORE on corporate_actions
      keeps the run idempotent.

Four behaviours covered:

1. snapshot_mode persists a row per figi.
2. detect_mode emits a split when face_value changes between two snapshots.
3. detect_mode is a no-op when face_value is unchanged.
4. detect_mode handles the reverse-split case (face_value goes UP,
   factor > 1) which the curated JSON historically couldn't represent.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """Migrated DB on a fresh temp path. Includes migrations 007 + 009 + 009b."""
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    yield p


def _seed_figi(db_path: str, figi: str, ticker: str, face_value: float = 1.0, lot_size: int = 10) -> None:
    """Insert a single figi with the metadata fields the snapshot writer needs."""
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO instruments(figi, ticker, class, name, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (figi, ticker, "share", ticker, "rub", lot_size),
    )
    con.commit()
    con.close()


def _mock_urlopen(payload):
    """Build a context-manager-friendly urlopen MagicMock."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda self, *a: None
    return resp


def _iss_securities_payload(secid: str, face_value: float, lot_size: int):
    """Build the payload for `GET /iss/securities/{secid}.json`."""
    return {
        "securities": {
            "columns": ["secid", "face_value", "lot_size"],
            "data": [[secid, face_value, lot_size]],
        }
    }


# --- tests -----------------------------------------------------------------


def test_snapshot_mode_writes_row_per_figi(db):
    """snapshot_mode hits MOEX once per figi and persists a row."""
    _seed_figi(db, "BBG004730N88", "SBER", face_value=1.0, lot_size=10)
    _seed_figi(db, "BBG004730RP0", "GAZP", face_value=1.0, lot_size=10)

    def _fake_urlopen(req, timeout=15):
        url = req if isinstance(req, str) else req.full_url
        if "SBER" in url:
            return _mock_urlopen(_iss_securities_payload("SBER", 1.0, 10))
        if "GAZP" in url:
            return _mock_urlopen(_iss_securities_payload("GAZP", 50.0, 100))
        return _mock_urlopen(
            {"securities": {"columns": ["secid"], "data": []}}
        )

    from algotrader_api.scripts_import.import_corporate_actions_splits import (
        snapshot_mode,
    )

    with patch(
        "algotrader_api.scripts_import.import_corporate_actions_splits.urllib.request.urlopen"
    ) as urlopen:
        urlopen.side_effect = _fake_urlopen
        n = snapshot_mode(db, observed_at="2026-09-13T20:30:00Z")

    assert n == 2

    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT secid, face_value, lot_size FROM instruments_snapshot "
        "ORDER BY secid"
    ).fetchall()
    assert rows == [("GAZP", 50.0, 100), ("SBER", 1.0, 10)]


def test_detect_mode_emits_split_when_face_value_changes(db):
    """Two snapshots for the same secid with different face_value → 1 split row."""
    _seed_figi(db, "BBG004730ZJ9", "VTBR", face_value=1.0, lot_size=10)
    con = sqlite3.connect(db)
    # Earlier: 1 kopek (face_value stored in RUB: 0.01)
    # Later:   50 RUB  → 5000x reverse split
    con.executemany(
        "INSERT INTO instruments_snapshot(secid, observed_at, face_value, lot_size) "
        "VALUES (?, ?, ?, ?)",
        [
            ("VTBR", "2024-06-01T20:30:00Z", 0.01, 10),
            ("VTBR", "2024-07-15T20:30:00Z", 50.0, 10),
        ],
    )
    con.commit()
    con.close()

    from algotrader_api.scripts_import.import_corporate_actions_splits import (
        detect_mode,
    )

    n = detect_mode(db)
    assert n == 1

    con = sqlite3.connect(db)
    row = con.execute(
        "SELECT figi, action_type, factor, source, note "
        "FROM corporate_actions"
    ).fetchone()
    assert row[0] == "VTBR"
    assert row[1] == "split"
    # factor = new/prev = 50.0 / 0.01 = 5000.0 (reverse split).
    assert row[2] == 5000.0
    assert row[3] == "moex_iss_snapshots"
    assert "face_value" in row[4]


def test_detect_mode_noop_when_face_value_unchanged(db):
    """Two snapshots with the same face_value → no corporate_actions row written."""
    _seed_figi(db, "BBG004730N88", "SBER", face_value=1.0)
    con = sqlite3.connect(db)
    con.executemany(
        "INSERT INTO instruments_snapshot(secid, observed_at, face_value, lot_size) "
        "VALUES (?, ?, ?, ?)",
        [
            ("SBER", "2024-06-01T20:30:00Z", 1.0, 10),
            ("SBER", "2024-07-01T20:30:00Z", 1.0, 10),  # same
        ],
    )
    con.commit()
    con.close()

    from algotrader_api.scripts_import.import_corporate_actions_splits import (
        detect_mode,
    )

    assert detect_mode(db) == 0
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0] == 0


def test_detect_mode_emits_forward_split_when_face_value_drops(db):
    """face_value 5 → 1 (forward split / denomination): factor = 1/5 = 0.2."""
    _seed_figi(db, "BBG004730ZJ9", "VTBR", face_value=5.0, lot_size=10)
    con = sqlite3.connect(db)
    con.executemany(
        "INSERT INTO instruments_snapshot(secid, observed_at, face_value, lot_size) "
        "VALUES (?, ?, ?, ?)",
        [
            ("VTBR", "2011-10-01T20:30:00Z", 5.0, 10),
            ("VTBR", "2011-11-20T20:30:00Z", 1.0, 10),
        ],
    )
    con.commit()
    con.close()

    from algotrader_api.scripts_import.import_corporate_actions_splits import (
        detect_mode,
    )

    n = detect_mode(db)
    assert n == 1
    con = sqlite3.connect(db)
    factor = con.execute(
        "SELECT factor FROM corporate_actions"
    ).fetchone()[0]
    # Factor = new/prev = 1.0/5.0 = 0.2.
    assert factor == pytest.approx(0.2)