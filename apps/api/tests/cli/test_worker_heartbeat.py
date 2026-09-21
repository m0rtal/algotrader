"""Test worker.heartbeat_loop emits a pipeline row.

Worker.py imports its real deps from the venv; these tests run inside
the venv (no sys.modules stubs needed).
"""
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

# Worker.py adds apps/api/src to sys.path when invoked; mirror that.
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SRC = _ROOT / "apps" / "api" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Default venv import — no stubs, real algotrader_api.config etc.
import worker  # noqa: E402


def _make_db(tmp_path):
    """Tiny SQLite DB with the pipeline table shape worker uses."""
    p = str(tmp_path / "test.db")
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS pipeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phase TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            rows_processed INTEGER DEFAULT 0,
            status VARCHAR DEFAULT 'ok',
            detail TEXT
        );
        """
    )
    con.commit()
    con.close()
    return p


def test_worker_heartbeat_thread_emits_row(tmp_path):
    db_path = _make_db(tmp_path)
    # 0.1s interval × 1s runtime = up to ~10 rows, expect ≥1.
    t = threading.Thread(
        target=worker.heartbeat_loop,
        args=(db_path, 0.1),
        daemon=True,
    )
    t.start()
    time.sleep(1.0)
    # Don't join — heartbeat_loop is infinite. The daemon thread will die
    # when this test process exits.

    con = sqlite3.connect(db_path)
    n = con.execute(
        "SELECT COUNT(*) FROM pipeline WHERE phase='worker.heartbeat'"
    ).fetchone()[0]
    con.close()
    assert n >= 1, f"expected ≥1 heartbeat row, got {n}"


def test_worker_heartbeat_includes_pid_in_detail(tmp_path):
    db_path = _make_db(tmp_path)
    t = threading.Thread(
        target=worker.heartbeat_loop,
        args=(db_path, 0.1),
        daemon=True,
    )
    t.start()
    time.sleep(0.5)

    con = sqlite3.connect(db_path)
    detail = con.execute(
        "SELECT detail FROM pipeline WHERE phase='worker.heartbeat' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    assert detail is not None
    assert f"pid={os.getpid()}" in detail[0]
