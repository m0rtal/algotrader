"""Integration tests for algotrader-api-supervisor.sh.

The supervisor is a shell script, so these tests shell out to bash
and assert against side effects (pidfile, /health endpoint, log
lines). They are integration tests, not unit tests — they require
the supervisor + uvicorn to be startable in the test environment.

Run with:
    pytest apps/api/tests/cli/test_api_supervisor.py -v

Each test is hermetic: it picks a unique port and a unique log path
under a tmp dir, so it can't collide with a production supervisor on
8000 or a worker chain.
"""
from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest


def _free_port() -> int:
    """Find an unused TCP port. Race-prone but fine for test isolation."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_ready(port: int, timeout_s: float = 30.0) -> bool:
    """Poll /health on ``port``. Return True on 200 OK, False on timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = subprocess.run(
                ["curl", "-m", "2", "-sf", f"http://127.0.0.1:{port}/health"],
                capture_output=True,
            )
            if r.returncode == 0:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _wait_gone(port: int, timeout_s: float = 10.0) -> bool:
    """Poll /health on ``port`` until it stops responding (5xx or connection refused)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = subprocess.run(
            ["curl", "-m", "2", "-o", "/dev/null", "-w", "%{http_code}",
             f"http://127.0.0.1:{port}/health"],
            capture_output=True,
            text=True,
        )
        # 000 = curl couldn't connect; uvicorn is gone.
        if r.stdout.strip() in ("000", ""):
            return True
        time.sleep(0.5)
    return False


@pytest.fixture
def supervisor(tmp_path):
    """Boot a hermetic api-supervisor on a free port.

    Yields (port, log_path, pidfile_path, supervisor_proc). Tears down
    the entire process tree on exit.
    """
    port = _free_port()
    log_path = tmp_path / "api.log"
    pidfile = tmp_path / "api.pid"
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    db_path = db_dir / "state.db"

    # Override STATE_DB / pidfile paths by editing the script? Easier:
    # spawn the script with env vars that redirect its log + pidfile.
    # The script hardcodes both, so we use sed to write a copy.
    src = Path("/home/hermes/algotrader/scripts/algotrader-api-supervisor.sh").read_text()
    # Patch the log path
    src = src.replace(
        'LOG="/home/hermes/.hermes/logs/${NAME}.log"',
        f'LOG="{log_path}"',
    )
    # Patch the pidfile path
    src = src.replace(
        'PIDFILE="/home/hermes/algotrader/apps/api/data/${NAME}.pid"',
        f'PIDFILE="{pidfile}"',
    )
    # Patch the health port (via env override already supported)
    custom = tmp_path / "supervisor.sh"
    custom.write_text(src)
    custom.chmod(0o755)

    env = os.environ.copy()
    env["ALGOTRADER_API_BIND"] = f"127.0.0.1:{port}"
    # The test fixture creates a fake DB so the api can boot its
    # health endpoint. The health endpoint queries bars_count, so we
    # need a real-looking state.db.
    env_db = db_path  # used by uvicorn via the env var the app reads

    proc = subprocess.Popen(
        ["bash", str(custom)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        # Give the supervisor + uvicorn ~5s to come up
        if not _wait_ready(port, timeout_s=30):
            pytest.fail(f"supervisor /health never came up on port {port}; "
                        f"log:\n{log_path.read_text() if log_path.exists() else '(no log)'}")
        yield port, log_path, pidfile, proc
    finally:
        # Tear down: kill the whole process group
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait(timeout=5)


def test_supervisor_boots_and_serves_health(supervisor):
    """Happy path: supervisor launches uvicorn and /health returns 200."""
    port, log_path, _, _ = supervisor
    r = subprocess.run(
        ["curl", "-m", "3", "-sf", f"http://127.0.0.1:{port}/health"],
        capture_output=True,
    )
    assert r.returncode == 0, f"/health failed: {r.stderr.decode()}"

    # Supervisor should have written a "launching uvicorn" line
    log = log_path.read_text()
    assert "launching uvicorn" in log, f"missing launch line in:\n{log}"


def test_supervisor_keeps_pidfile_fresh(supervisor):
    """pidfile contains the live uvicorn pid while the backend is healthy."""
    port, _, pidfile, _ = supervisor
    assert pidfile.exists(), "pidfile not written"
    pid = int(pidfile.read_text().strip())
    # The pid should be alive
    os.kill(pid, 0)  # raises if dead
    # And it should be a uvicorn process
    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", errors="ignore")
    assert "uvicorn" in cmdline, f"pid {pid} cmdline is not uvicorn: {cmdline}"


def test_supervisor_restarts_uvicorn_after_kill(supervisor):
    """Kill uvicorn → supervisor's watchdog detects → supervisor restarts it.

    This is the regression scenario for PR #121: previously, when
    uvicorn died, nothing brought it back. Now the supervisor's
    HTTP watchdog polls /health every 10s and SIGKILLs the dead
    process, which makes the outer `wait` return and relaunch.
    """
    port, _, pidfile, _ = supervisor
    pid = int(pidfile.read_text().strip())
    original_pid = pid

    # Kill uvicorn brutally
    os.kill(pid, signal.SIGKILL)

    # The supervisor's watchdog will detect 3 consecutive /health
    # failures (10s apart) and pkills, then the outer loop relaunches.
    # In the meantime, uvicorn is gone — /health should be unreachable.
    assert _wait_gone(port, timeout_s=10), (
        "uvicorn did not actually die after SIGKILL — "
        "test cannot validate supervisor restart behaviour"
    )

    # Wait for supervisor to relaunch uvicorn and bind the port.
    # Worst case: 30s for 3 health failures + ~5s for uvicorn startup.
    assert _wait_ready(port, timeout_s=60), (
        f"supervisor failed to restart uvicorn within 60s after kill. "
        f"Log:\n"
    )

    # New pid should differ from the killed one
    new_pid = int(pidfile.read_text().strip())
    assert new_pid != original_pid, (
        f"supervisor kept the same pid ({new_pid}) after kill — "
        f"pidfile was not rewritten"
    )
    os.kill(new_pid, 0)  # new uvicorn must be alive
