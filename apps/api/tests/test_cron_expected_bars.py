"""Integration checks for the hourly expected-bars data job.

Runs only against temporary SQLite databases; never writes to production.
"""
from __future__ import annotations

import fcntl
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest


JOB = Path(__file__).resolve().parent.parent / "scripts/cron_expected_bars.sh"


@pytest.fixture
def sample_db(tmp_path: Path) -> Path:
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE instruments (
                figi TEXT PRIMARY KEY, class TEXT,
                source_updated_at TEXT, expected_bars INTEGER
            );
            CREATE TABLE bars (figi TEXT, ts TEXT);
            CREATE TABLE moex_holidays (date TEXT PRIMARY KEY);
            INSERT INTO instruments VALUES ('F1', 'share', '2020-01-02', NULL);
            INSERT INTO bars VALUES ('F1', '2020-01-02');
        """)
    return db


def run_job(db: Path, log: Path, *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(JOB), "--db", str(db), "--log", str(log)],
        env={**os.environ, "ALGOTRADER_EXPECTED_BARS_PYTHON": sys.executable},
        text=True, capture_output=True, timeout=timeout,
    )


def expected(db: Path) -> int | None:
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT expected_bars FROM instruments WHERE figi='F1'"
        ).fetchone()[0]


def test_hourly_job_populates_and_logs_success(sample_db: Path, tmp_path: Path) -> None:
    assert JOB.stat().st_mode & 0o111, "cron invokes this file directly"
    log = tmp_path / "expected-bars.log"
    result = run_job(sample_db, log)
    assert result.returncode == 0, result.stderr
    value = expected(sample_db)
    assert value is not None and value > 0
    assert "success" in log.read_text().lower()
    assert run_job(sample_db, log).returncode == 0
    assert "success" in log.read_text().lower()


def test_hourly_job_refuses_overlapping_run(sample_db: Path, tmp_path: Path) -> None:
    log = tmp_path / "expected-bars.log"
    lock_path = Path(f"{sample_db}.expected-bars.lock")
    with lock_path.open("w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run_job(sample_db, log)
    assert result.returncode != 0
    assert expected(sample_db) is None
    assert "locked" in log.read_text().lower()


def test_hourly_job_reports_missing_database(tmp_path: Path) -> None:
    db = tmp_path / "missing.db"
    log = tmp_path / "expected-bars.log"
    result = run_job(db, log)
    assert result.returncode != 0
    assert "success" not in log.read_text().lower()
    assert "error" in log.read_text().lower()


def test_hourly_job_retries_transient_sqlite_write_lock(sample_db: Path, tmp_path: Path) -> None:
    """The first 30-second SQLite attempt fails; a released lock lets retry win."""
    log = tmp_path / "expected-bars.log"
    holder = sqlite3.connect(sample_db)
    holder.execute("BEGIN IMMEDIATE")
    proc = subprocess.Popen(
        ["bash", str(JOB), "--db", str(sample_db), "--log", str(log)],
        env={**os.environ, "ALGOTRADER_EXPECTED_BARS_PYTHON": sys.executable},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if log.exists() and "retry" in log.read_text().lower():
                break
            if proc.poll() is not None:
                pytest.fail(f"job exited before retry (rc={proc.returncode})")
            time.sleep(0.1)
        else:
            pytest.fail("no retry after SQLite's 30-second busy timeout")
    except BaseException:
        proc.kill()
        proc.communicate(timeout=5)
        raise
    finally:
        holder.rollback()
        holder.close()
    stdout, stderr = proc.communicate(timeout=40)
    assert proc.returncode == 0, (stdout, stderr)
    value = expected(sample_db)
    assert value is not None and value > 0
    assert "success" in log.read_text().lower()


def test_hourly_job_preserves_wrapper_local_lockfile(tmp_path: Path) -> None:
    """The wrapper's local ``${DB}.expected-bars.lock`` is preserved
    as duplicate-invocation protection; the Python writer
    separately acquires the canonical ``${DB}.writer.lock``.

    Two simultaneous wrapper invocations on the same database must
    not run their Python children concurrently: the wrapper-local
    ``flock`` is the gate. The canonical ``${DB}.writer.lock`` is
    acquired by the Python child only AFTER the wrapper has
    dropped into the retry loop, so it can never be held while
    the wrapper-local lock is still held by an overlapping
    invocation.
    """
    import sys as _sys

    # Use a real schema so the Python child can run end-to-end.
    from test_populate_expected_bars_lock import (
        _migrate as _migrate_lock,
    )
    # Build a tmp dir with a real migrated DB. Reuse the helper
    # from the lock-test module so we share the migration logic.
    db = tmp_path / "state.db"
    _migrate_lock(db)
    # Insert at least one instrument so the Python child has
    # something to update.
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO instruments "
            "(ticker, figi, class, name, currency, lot_size, isin, "
            " source_updated_at) "
            "VALUES ('AAA', 'FIGI-A', 'share', 'AAA', 'RUB', 1, 'X', "
            "        '2020-01-02')"
        )
        conn.commit()
    log_a = tmp_path / "a.log"
    log_b = tmp_path / "b.log"

    env = {**os.environ, "ALGOTRADER_EXPECTED_BARS_PYTHON": _sys.executable}
    proc_a = subprocess.Popen(
        ["bash", str(JOB), "--db", str(db), "--log", str(log_a)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    # Give proc_a a moment to acquire the wrapper-local lock.
    time.sleep(0.3)
    proc_b = subprocess.run(
        ["bash", str(JOB), "--db", str(db), "--log", str(log_b)],
        env=env, text=True, capture_output=True, timeout=30,
    )
    stdout_a, stderr_a = proc_a.communicate(timeout=30)
    # proc_a should succeed (no holder).
    assert proc_a.returncode == 0, (stdout_a, stderr_a)
    # proc_b should have been refused by the wrapper-local lock
    # (the brief: wrapper-local lock is preserved as
    # duplicate-invocation protection). The wrapper logs
    # ``ERROR locked: another expected-bars run is active`` and
    # exits 75.
    assert proc_b.returncode == 75, (
        f"second wrapper should exit 75 (overlap refused), got "
        f"{proc_b.returncode}: {proc_b.stderr!r}"
    )
    text_b = log_b.read_text()
    assert "locked" in text_b.lower(), (
        f"second wrapper log missing 'locked' marker: {text_b!r}"
    )
    # Direct invocation is still protected by the canonical
    # ${DB}.writer.lock: a Python child invoked against this DB
    # acquires and releases the canonical lock. We assert by
    # running a one-shot script that records the lock path.
    _API_SRC = Path(__file__).resolve().parent.parent / "src"
    probe_script = (
        "import json, sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(_API_SRC)!r})\n"
        "from algotrader_api.ingestion.writer_lock import (\n"
        "    writer_lock, writer_lock_path,\n"
        ")\n"
        f"db = {str(db)!r}\n"
        "paths = []\n"
        "def _record(p, **kw):\n"
        "    paths.append(str(writer_lock_path(p)))\n"
        "    from contextlib import contextmanager\n"
        "    @contextmanager\n"
        "    def cm():\n"
        "        with writer_lock(p, role='expected-bars',\n"
        "                         phase='expected-bars',\n"
        "                         timeout_seconds=2.0):\n"
        "            yield\n"
        "    return cm()\n"
        "with _record(Path(db)):\n"
        "    pass\n"
        "print(json.dumps({'paths': paths}))\n"
    )
    probe = subprocess.run(
        [_sys.executable, "-c", probe_script],
        env={"PATH": os.environ["PATH"],
             "PYTHONPATH": str(_API_SRC),
             "PYTHONHOME": ""},
        capture_output=True, text=True, timeout=10,
    )
    assert probe.returncode == 0, probe.stderr
    import json
    payload = json.loads(probe.stdout.strip())
    assert payload["paths"] == [f"{db}.writer.lock"], payload
    # The two lock files are distinct paths.
    wrapper_lock = Path(f"{db}.expected-bars.lock")
    canonical_lock = Path(payload["paths"][0])
    assert wrapper_lock != canonical_lock, (
        f"wrapper-local lock and canonical lock collide: "
        f"{wrapper_lock} vs {canonical_lock}"
    )
