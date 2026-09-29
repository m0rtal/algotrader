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
