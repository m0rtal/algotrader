"""Regression: SQLite singleton must set busy_timeout=30000ms.

Without busy_timeout, concurrent connections hit
'database is locked' immediately (5s default is also
too short under sustained load). Observed 2026-09-27
18:28 MSK: every backfill_moex cycle failed with
'database is locked' because the heartbeat daemon
thread was holding a write lock that starved the
main thread.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile


def test_singleton_connection_has_busy_timeout_30s():
    """The shared singleton must enable busy_timeout=30000."""
    from algotrader_api.db.sqlite import get_connection
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    try:
        conn = get_connection(tmp.name)
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert busy_timeout == 30000, (
            f"expected busy_timeout=30000 (30s), got {busy_timeout}ms. "
            f"Without this, concurrent connections hit 'database is locked' "
            f"immediately on contended writes — observed 2026-09-27 18:28 MSK."
        )
    finally:
        os.unlink(tmp.name)


def test_singleton_connection_has_wal_mode():
    """The shared singleton must enable WAL journal mode (existed already)."""
    from algotrader_api.db.sqlite import get_connection
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    try:
        conn = get_connection(tmp.name)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal", f"expected WAL, got {mode!r}"
    finally:
        os.unlink(tmp.name)
