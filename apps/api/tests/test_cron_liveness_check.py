"""Tests for scripts/cron_liveness_check.sh.

Why a Python wrapper test rather than a pure bash harness:
  * The script's contract (read DB, log result, conditionally dispatch
    clean supervisor) is most reliably verified by inspecting the log
    file from a Python subprocess, and by stubbing every process-touching
    boundary (pgrep / kill / setsid / supervisor launch) so the test
    cannot affect production.

Spec contract: openspec/.../specs/data-quality/spec.md — Requirement:
Autonomous Pipeline Liveness, Scenario 'Stale heartbeat triggers
supervisor restart' and 'Missing first worker dispatches clean supervisor'.

These tests use a tmp SQLite DB, a tmp log, a tmp lock file, and
process-command doubles. They MUST NOT touch the live worker, the
production log, the production DB, or the real supervisor wrapper.

Boundary contract the script honours:
  LIVENESS_LOG                -> log file path (default: /home/hermes/.hermes/logs/algotrader-liveness-cron.log)
  LIVENESS_LOCK_FILE          -> flock target (default: alongside DB)
  LIVENESS_FIRST_MATCH_FILE   -> NUL-separated argv tokens for the first worker
  LIVENESS_SUPERVISOR_MATCH_FILE -> NUL-separated argv tokens for the supervisor
  LIVENESS_SUPERVISOR_LAUNCH  -> command to dispatch when recovery is needed
  LIVENESS_PGREP_BIN          -> pgrep replacement (defaults to /usr/bin/pgrep)
  LIVENESS_KILL_BIN           -> kill replacement (defaults to /bin/kill)
  LIVENESS_SETSID_BIN         -> setsid replacement (defaults to /usr/bin/setsid)

Process selection rule the script must follow:
  * Never trust pgrep -f substring matching alone. Always verify the
    candidate PID's argv (read /proc/$PID/cmdline) contains the required
    match tokens in order.
  * A wrong-subset command (e.g. 'worker.py live' or 'worker.py daily
    second') must never be selected as the first worker.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #

_TEST_FILE = Path(__file__).resolve()
REPO_ROOT = _TEST_FILE.parents[3]  # test → tests → api → apps → repo
SCRIPT = REPO_ROOT / "scripts" / "cron_liveness_check.sh"

# The hard-coded production log path MUST never be written to by a test.
# Tests assert the script writes only to its override path, not here.
PROD_LOG = Path("/home/hermes/.hermes/logs/algotrader-liveness-cron.log")


# --------------------------------------------------------------------------- #
# fixtures / harness
# --------------------------------------------------------------------------- #


@pytest.fixture
def harness(tmp_path: Path):
    """Per-test isolated boundary.

    Creates under tmp_path:
      * empty SQLite DB with the schema the script reads
      * isolated log file (script writes here, NOT to PROD_LOG)
      * isolated lock file (script uses flock against here)
      * stubbed pgrep/kill/setsid binaries (record calls; never exec real ones)
      * stubbed supervisor launch script (records invocation)
      * children with deterministic argv (real spawned PIDs) for the
        process-argv selection tests.

    Yields a Harness object the tests use to set up scenarios and
    assert what the script emitted.
    """
    # 1. tmp DB (script reads ALGOTRADER_STATE_DB; tests point at this)
    db = tmp_path / "state.db"

    # 2. tmp log + lock under tmp_path; never the production paths
    log = tmp_path / "liveness.log"
    lock = tmp_path / "liveness.lock"

    # 3. stubbed supervisor launcher (records invocation; never execs real)
    launcher = tmp_path / "launch.sh"
    launcher.write_text(
        "#!/usr/bin/env bash\n"
        "echo \"$@\" >> \"$LAUNCH_LOG\"\n"
        # The 2s sleep ensures a concurrent watcher observes the lock\n"
        # held while this stub is running. Without it the launcher\n"
        # returns too fast and the test races: the second watcher\n"
        # never sees the lock and both dispatch.\n"
        "sleep 2\n"
        "exit 0\n"
    )
    launcher.chmod(0o755)

    # 4. stub pgrep: prints PIDs (from PID_FILE) on stdout, one per line,
    #    exit 0 if any, exit 1 if none. NEVER scans /proc.
    pgrep_stub = tmp_path / "pgrep-stub.sh"
    pgrep_stub.write_text(
        "#!/usr/bin/env bash\n"
        "# argv ignored (script passes pattern); we echo test-controlled PIDs.\n"
        "cat \"$PGREP_PID_FILE\" 2>/dev/null\n"
        "[ -s \"$PGREP_PID_FILE\" 2>/dev/null ] && exit 0 || exit 1\n"
    )
    pgrep_stub.chmod(0o755)

    # 5. stub kill: appends invocation to KILL_LOG, exit 0.
    #    This OVERRIDES the bash builtin kill when called as a command
    #    because the script invokes it as a subprocess via the override
    #    LIVENESS_KILL_BIN, not via the builtin.
    kill_stub = tmp_path / "kill-stub.sh"
    kill_stub.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'kill %s\\n' \"$*\" >> \"$KILL_LOG\"\n"
        "exit 0\n"
    )
    kill_stub.chmod(0o755)

    # 6. stub setsid: appends invocation to SETSID_LOG AND runs the
    # subsequent command (so the launcher stub actually executes in
    # tests). Without this, setsid-stub.sh would just record and exit
    # without ever running the launcher — and the lock would be
    # released immediately, making the concurrency test racy.
    setsid_stub = tmp_path / "setsid-stub.sh"
    setsid_stub.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'setsid %s\\n' \"$*\" >> \"$SETSID_LOG\"\n"
        "exec \"$@\"\n"
    )
    setsid_stub.chmod(0o755)

    class Harness:
        def __init__(self):
            self.tmp_path = tmp_path
            self.db = db
            self.log = log
            self.lock = lock
            self.launcher = launcher
            self.kill_log = tmp_path / "kill.log"
            self.setsid_log = tmp_path / "setsid.log"
            self.launch_log = tmp_path / "launch.log"
            self.pgrep_pids = tmp_path / "pgrep-pids"  # newline list
            # Pre-create match-argv files so the harness can hand them
            # to the script. Tests that need a non-default pattern
            # rewrite them via set_first_match / set_supervisor_match.
            self.first_match_file = tmp_path / "first-match.argv"
            self.supervisor_match_file = tmp_path / "supervisor-match.argv"
            self.first_match_file.write_bytes(
                "\0".join([sys.executable, "worker.py", "daily", "first"]).encode()
                + b"\0"
            )
            self.supervisor_match_file.write_bytes(
                "\0".join([
                    "algotrader-supervisor.sh",
                    "algotrader-moex-backfill",
                    "worker.py", "daily", "first",
                ]).encode() + b"\0"
            )
            self.spawned: list[tuple[int, list[str]]] = []  # (pid, argv)

        # ---- scenario setup helpers ---------------------------------- #
        def fresh_heartbeat(self, age_s: int = 30):
            """Seed a fresh pipeline_heartbeat row at `age_s` seconds old."""
            self._seed_pipeline_heartbeat(age_s)

        def no_heartbeat(self):
            """Empty DB: no rows in either heartbeat table."""
            if self.db.exists():
                self.db.unlink()
            con = sqlite3.connect(str(self.db))
            con.execute("CREATE TABLE foo (bar INTEGER);")
            con.commit()
            con.close()

        def spawn_argv(self, argv: list[str], *, sleep_s: int = 5) -> int:
            """Spawn a long-lived child with a deterministic argv.

            The child's argv is what /proc/$PID/cmdline will show; the
            argv-selection logic in the script must match this exactly.
            We use execvp on the test python so argv0 is real python,
            not a shell wrapper.

            `sleep_s` is short (default 5s) so a leaked child self-dies
            quickly even if the fixture cleanup fails.
            """
            argv = [
                sys.executable, "-c",
                f"import time, sys; time.sleep({sleep_s})",
            ] + list(argv)
            pid = os.spawnvp(os.P_NOWAIT, sys.executable, argv)
            cmdline = "\0".join(argv) + "\0"
            (self.tmp_path / f"cmdline.{pid}").write_text(cmdline)
            self.spawned.append((pid, argv))
            _spawned_registry.append((pid, list(argv)))
            return pid

        def set_pgrep_pids(self, *pids: int):
            """Stub-pgrep returns these PIDs (one per line)."""
            self.pgrep_pids.write_text(
                "\n".join(str(p) for p in pids) + ("\n" if pids else "")
            )

        def run(
            self,
            extra_env: dict | None = None,
            first_argv_match: list[str] | None = None,
            supervisor_argv_match: list[str] | None = None,
            threshold: int = 300,
        ) -> subprocess.CompletedProcess:
            """Run the script with the harness boundaries set up.

            first_argv_match / supervisor_argv_match default to the
            production argv tokens. Tests override for the
            wrong-subset case.
            """
            if first_argv_match is not None:
                self.first_match_file.write_bytes(
                    "\0".join(first_argv_match).encode() + b"\0"
                )
            if supervisor_argv_match is not None:
                self.supervisor_match_file.write_bytes(
                    "\0".join(supervisor_argv_match).encode() + b"\0"
                )
            env = os.environ.copy()
            env["ALGOTRADER_STATE_DB"] = str(self.db)
            env["LIVENESS_STALE_THRESHOLD"] = str(threshold)
            env["LIVENESS_LOG"] = str(self.log)
            env["LIVENESS_LOCK_FILE"] = str(self.lock)
            env["LIVENESS_SUPERVISOR_LAUNCH"] = str(self.launcher)
            env["LIVENESS_PGREP_BIN"] = str(pgrep_stub)
            env["LIVENESS_KILL_BIN"] = str(kill_stub)
            env["LIVENESS_SETSID_BIN"] = str(setsid_stub)
            env["LIVENESS_FOREGROUND_DISPATCH"] = "1"
            env["LIVENESS_FIRST_MATCH_FILE"] = str(self.first_match_file)
            env["LIVENESS_SUPERVISOR_MATCH_FILE"] = str(self.supervisor_match_file)
            env["PGREP_PID_FILE"] = str(self.pgrep_pids)
            env["KILL_LOG"] = str(self.kill_log)
            env["SETSID_LOG"] = str(self.setsid_log)
            env["LAUNCH_LOG"] = str(self.launch_log)
            # Defensive: the harness must NOT inherit a production
            # ALGOTRADER_STATE_DB pointing at a live DB.
            env.pop("ALGOTRADER_LIVE_WORKER", None)
            if extra_env:
                env.update(extra_env)
            return subprocess.run(
                ["bash", str(SCRIPT)],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

        # ---- assertion helpers --------------------------------------- #
        def log_text(self) -> str:
            return self.log.read_text() if self.log.exists() else ""

        def kill_log_text(self) -> str:
            return self.kill_log.read_text() if self.kill_log.exists() else ""

        def setsid_log_text(self) -> str:
            return self.setsid_log.read_text() if self.setsid_log.exists() else ""

        def launch_log_text(self) -> str:
            return self.launch_log.read_text() if self.launch_log.exists() else ""

        # ---- internals ----------------------------------------------- #
        def _seed_pipeline_heartbeat(self, age_s: int):
            if self.db.exists():
                self.db.unlink()
            con = sqlite3.connect(str(self.db))
            con.executescript(
                """
                CREATE TABLE pipeline_heartbeat (
                    worker_pid   INTEGER NOT NULL,
                    phase        TEXT    NOT NULL,
                    last_bar_ts  TEXT,
                    updated_at   TEXT    NOT NULL,
                    PRIMARY KEY (worker_pid, phase)
                );
                """
            )
            iso = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
            con.execute(
                "INSERT INTO pipeline_heartbeat (worker_pid, phase, last_bar_ts, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (99999, "live_loop", "2024-01-15", iso),
            )
            con.commit()
            con.close()

    try:
        yield Harness()
    finally:
        # Reap every child we spawned in this test.
        registry = list(_spawned_registry)
        for pid, _argv in registry:
            try:
                os.kill(pid, 9)
            except (ProcessLookupError, PermissionError):
                pass
            _spawned_registry.remove((pid, _argv))


# Track spawned PIDs across the fixture body so the finally clause can
# reap them even if the harness object is GC'd.
_spawned_registry: list[tuple[int, list[str]]] = []


@pytest.fixture(autouse=True)
def _ensure_prod_log_untouched():
    """Defensive autouse guard: record PROD_LOG mtime/size before and
    after each test. If anything in this test file accidentally writes
    to the production log path, this fixture catches it."""
    if not PROD_LOG.exists():
        pre = None
    else:
        pre = (PROD_LOG.stat().st_mtime, PROD_LOG.stat().st_size)
    yield
    if pre is None:
        # If prod log still doesn't exist, no test could have created it
        # (we never run as root; permissions allow).
        assert not PROD_LOG.exists(), (
            f"test created the production log file {PROD_LOG} — "
            "watchdog test must never touch production log"
        )
    else:
        post = PROD_LOG.stat()
        assert (post.st_mtime, post.st_size) == pre, (
            f"test modified production log {PROD_LOG} — "
            "watchdog test must never touch production log"
        )


# --------------------------------------------------------------------------- #
# Sanity: the script file itself
# --------------------------------------------------------------------------- #


def test_script_exists_and_is_executable():
    """Operator sanity check."""
    assert SCRIPT.exists(), f"missing script at {SCRIPT}"
    import stat
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, f"script not executable: {SCRIPT} (mode={oct(mode)})"


def test_script_parses_cleanly():
    """bash -n must pass — no syntax errors."""
    rc = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert rc.returncode == 0, f"bash -n failed: {rc.stderr}"


# --------------------------------------------------------------------------- #
# Core: process-isolation contract
# --------------------------------------------------------------------------- #


def test_no_real_pgrep_kill_setsid_executed(harness):
    """The script must invoke LIVENESS_PGREP_BIN / KILL_BIN / SETSID_BIN
    (the test stubs), not /usr/bin/pgrep, /bin/kill, /usr/bin/setsid.

    This is the boundary that keeps watchdog tests from killing the
    real daily worker.
    """
    harness.no_heartbeat()
    harness.set_pgrep_pids()  # pgrep returns nothing
    proc = harness.run()
    assert proc.returncode == 0
    log = harness.log_text()
    assert "no heartbeat row and no worker process" in log, (
        f"expected fallback branch in log; got:\n{log}"
    )
    # Setsid stub captured the dispatch (synchronous, before the script
    # returns). The dispatch line in liveness.log confirms the script's
    # intent; the setsid stub captures the actual exec call.
    setsid_log = harness.setsid_log_text()
    assert "setsid bash" in setsid_log and "launch.sh" in setsid_log, (
        f"expected setsid stub to capture the launcher dispatch; got:\n{setsid_log}"
    )
    assert harness.kill_log_text() == "", (
        f"kill stub was invoked unexpectedly: {harness.kill_log_text()!r}"
    )


def test_no_production_log_or_db_touched(harness):
    """The script must write ONLY to LIVENESS_LOG; it must never open
    or truncate the production LOG path or the production DB path."""
    prod_db = Path("/home/hermes/algotrader/apps/api/data/state.db")
    pre_db_exists = prod_db.exists()
    pre_db_mtime = prod_db.stat().st_mtime if pre_db_exists else None

    harness.fresh_heartbeat(age_s=10)
    proc = harness.run()
    assert proc.returncode == 0

    # Production log untouched (also asserted by autouse fixture).
    # Production DB untouched.
    if pre_db_exists:
        assert prod_db.stat().st_mtime == pre_db_mtime, (
            "watchdog test must not modify the production DB"
        )
    # Our isolated log was written.
    assert "heartbeat fresh" in harness.log_text()


# --------------------------------------------------------------------------- #
# Healthy paths
# --------------------------------------------------------------------------- #


def test_fresh_heartbeat_with_worker_present_noop(harness):
    """Fresh heartbeat AND a present first worker → no kill, no launch."""
    pid = harness.spawn_argv(["worker.py", "daily", "first"])
    harness.fresh_heartbeat(age_s=10)
    harness.set_pgrep_pids(pid)
    proc = harness.run()
    assert proc.returncode == 0
    assert "heartbeat fresh" in harness.log_text()
    assert harness.kill_log_text() == "", "must not kill the live worker"
    assert harness.launch_log_text() == "", "must not relaunch supervisor"


def test_fresh_heartbeat_missing_first_worker_dispatches_clean_supervisor(harness):
    """[BUG] Fresh heartbeat (live-loop running) but no first worker
    and no supervisor → MUST dispatch the clean supervisor. The derived
    worker's heartbeat must not hide the missing first worker."""
    harness.fresh_heartbeat(age_s=10)
    harness.set_pgrep_pids()  # neither worker nor supervisor
    proc = harness.run()
    assert proc.returncode == 0
    log = harness.log_text()
    assert "missing first worker and supervisor; dispatching clean supervisor" in log, (
        f"expected dispatch line in log; got:\n{log}"
    )
    # Recovery uses the LIVENESS_SUPERVISOR_LAUNCH path, NOT startup-algotrader.sh.
    assert "startup-algotrader.sh" not in log
    # setsid stub captured the dispatch synchronously (script runs in
    # LIVENESS_FOREGROUND_DISPATCH=1 mode for tests).
    sl = harness.setsid_log_text()
    assert "setsid bash" in sl, (
        f"expected setsid dispatch; got:\n{sl}"
    )
    assert harness.kill_log_text() == "", "must not kill anything"


def test_no_heartbeat_no_processes_dispatches_clean_supervisor(harness):
    """[BUG] No heartbeat row AND no processes → recovery via the
    clean supervisor wrapper, NOT via startup-algotrader.sh."""
    harness.no_heartbeat()
    harness.set_pgrep_pids()
    proc = harness.run()
    assert proc.returncode == 0
    log = harness.log_text()
    assert "no heartbeat row and no worker process" in log
    # Recovery uses clean wrapper, not startup-algotrader.sh.
    assert "startup-algotrader.sh" not in log
    assert (
        "Dispatching clean supervisor wrapper" in log
        or "dispatching clean supervisor" in log
    ), f"expected clean-supervisor dispatch in log; got:\n{log}"
    assert "setsid bash" in harness.setsid_log_text(), (
        f"expected setsid dispatch; got:\n{harness.setsid_log_text()}"
    )


def test_no_heartbeat_worker_present_noop(harness):
    """No heartbeat row but first worker process IS alive → supervisor
    will respawn live-loop on its own; watchdog must NOT relaunch."""
    pid = harness.spawn_argv(["worker.py", "daily", "first"])
    harness.no_heartbeat()
    harness.set_pgrep_pids(pid)
    proc = harness.run()
    assert proc.returncode == 0
    log = harness.log_text()
    assert "no heartbeat row but worker process alive" in log, (
        f"expected 'worker process alive' branch; got:\n{log}"
    )
    assert harness.launch_log_text() == "", "must not relaunch"
    assert harness.kill_log_text() == "", "must not kill"


def test_live_supervisor_without_worker_does_not_duplicate(harness):
    """Supervisor alive, no worker → supervisor's restart loop will
    respawn the worker; watchdog must NOT dispatch a duplicate
    supervisor (which would double-start the chain)."""
    sup_pid = harness.spawn_argv([
        "algotrader-supervisor.sh",
        "algotrader-moex-backfill",
        "/home/hermes/algotrader/apps/api/.venv/bin/python",
        "worker.py", "daily", "first",
        "/home/hermes/algotrader/apps/api",
    ])
    harness.fresh_heartbeat(age_s=10)
    # Stub pgrep returns the supervisor pid (script must verify argv
    # AND must distinguish "supervisor-only" from "first worker").
    harness.set_pgrep_pids(sup_pid)
    proc = harness.run()
    assert proc.returncode == 0
    assert harness.launch_log_text() == "", (
        "watchdog must not dispatch a duplicate supervisor when one "
        "is already alive and no worker exists"
    )
    assert harness.kill_log_text() == "", "must not kill the supervisor"


# --------------------------------------------------------------------------- #
# Stale path
# --------------------------------------------------------------------------- #


def test_stale_heartbeat_kills_only_selected_pid(harness):
    """Stale heartbeat + matching first worker PID → kill -9 ONLY that
    PID; nothing else; no relaunch (supervisor restarts)."""
    pid = harness.spawn_argv(["worker.py", "daily", "first"])
    harness._seed_pipeline_heartbeat(age_s=900)
    harness.set_pgrep_pids(pid)
    proc = harness.run(threshold=300)
    assert proc.returncode == 0
    log = harness.log_text()
    assert "FATAL: heartbeat stale" in log
    kill_log = harness.kill_log_text()
    assert f"kill -9 {pid}" in kill_log or f"kill {pid}" in kill_log or f"kill -9 -9 {pid}" in kill_log, (
        f"expected kill of pid={pid}; got:\n{kill_log}"
    )
    # Recovery dispatch path must NOT fire for stale-but-present-worker.
    assert harness.launch_log_text() == "", "must not relaunch supervisor"


def test_stale_heartbeat_no_pid_uses_existing_recovery_branch(harness):
    """Stale heartbeat but no matching worker process → must NOT
    silently swallow it. The existing recovery branch (live supervisor
    respawns; or dispatch clean supervisor if no supervisor) is fine,
    so long as it doesn't kill anything."""
    harness._seed_pipeline_heartbeat(age_s=900)
    harness.set_pgrep_pids()
    proc = harness.run(threshold=300)
    assert proc.returncode == 0
    assert harness.kill_log_text() == "", "must not kill anything (no PID)"
    log = harness.log_text()
    assert "FATAL: heartbeat stale" in log
    # Either the recovery dispatch fired (no worker, no supervisor) or
    # the script logged the supervisor-alive branch (supervisor pid
    # present, just no worker).
    assert (
        "dispatching clean supervisor" in log
        or "supervisor alive without worker" in log
    ), f"unexpected stale-no-pid log:\n{log}"


# --------------------------------------------------------------------------- #
# Argv-selection: only the intended worker / supervisor can be picked
# --------------------------------------------------------------------------- #


def test_wrong_subset_worker_argv_is_not_selected(harness):
    """A process whose argv contains 'worker.py daily' but NOT 'first'
    must NOT be selected as the first worker (live-loop worker would
    otherwise match the broad pgrep -f pattern)."""
    pid = harness.spawn_argv(["worker.py", "daily", "second"])
    harness._seed_pipeline_heartbeat(age_s=900)
    harness.set_pgrep_pids(pid)
    proc = harness.run(threshold=300)
    assert proc.returncode == 0
    # argv did not include 'first' so the script must NOT kill this pid
    # AND must NOT mistakenly classify it as 'live supervisor without
    # worker'. It must dispatch recovery instead.
    kill_log = harness.kill_log_text()
    assert f" {pid}" not in kill_log, (
        f"wrong-subset pid={pid} was killed; argv match failed:\n{kill_log}"
    )
    log = harness.log_text()
    assert "FATAL: heartbeat stale" in log
    assert (
        "dispatching clean supervisor" in log
        or "no first worker and supervisor; dispatching" in log
    ), f"expected recovery dispatch for wrong-subset pid; got:\n{log}"


def test_diagnostic_command_argv_is_not_selected(harness):
    """A diagnostic shell whose argv contains 'worker.py daily first'
    only as a substring of its command line (e.g. grep) must NOT be
    selected. Selection requires argv tokens in order.

    We use a child whose argv[2] is the literal string but argv[3] is
    something else — simulates a shell doing 'echo worker.py daily first'.
    """
    pid = harness.spawn_argv([
        "/bin/echo",
        "diagnostic shell printing: worker.py daily first",
    ])
    harness._seed_pipeline_heartbeat(age_s=900)
    harness.set_pgrep_pids(pid)
    proc = harness.run(threshold=300)
    assert proc.returncode == 0
    kill_log = harness.kill_log_text()
    assert f" {pid}" not in kill_log, (
        f"diagnostic-shell pid={pid} was killed; argv match too loose:\n{kill_log}"
    )


# --------------------------------------------------------------------------- #
# Concurrency: single-flight
# --------------------------------------------------------------------------- #


def test_concurrent_runs_do_not_dispatch_twice(harness):
    """Two watchdog runs racing must NOT both dispatch a supervisor.

    The script uses an atomic mkdir-based lock around the dispatch
    path. The second caller observes the lock and skips dispatch. We
    assert at most one launcher invocation across two overlapping
    runs.

    Implementation: thread 1 acquires the lock, then thread 2 starts.
    Thread 2 must observe the lock and exit before dispatching.
    Finally, thread 1 finishes its work and releases the lock.
    """
    import threading

    harness.no_heartbeat()
    harness.set_pgrep_pids()

    # Start the FIRST run; it will acquire the lock and start dispatching.
    # We don't synchronously wait for it; instead we wait for it to
    # establish the lock by polling the lock directory.
    lock_dir = harness.tmp_path / "liveness.lock.d"
    assert not lock_dir.exists(), (
        f"lock dir should not exist before first run; exists at {lock_dir}"
    )

    results: list[subprocess.CompletedProcess] = []

    def _run1():
        results.append(harness.run())

    def _run2():
        # Wait until the lock is held, then race against it.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if lock_dir.exists():
                break
            time.sleep(0.01)
        else:
            # Lock never established; the test can't prove single-flight
            # in this case, but we still record the result.
            pass
        results.append(harness.run())

    import time

    t1 = threading.Thread(target=_run1)
    t2 = threading.Thread(target=_run2)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert all(r.returncode == 0 for r in results), (
        f"both runs should exit 0; got {[r.returncode for r in results]}"
    )
    # setsid stub log captures every dispatch synchronously (before
    # the script exits). Across two concurrent runs, at most one
    # caller should reach the dispatch path. The other caller should
    # have logged 'another liveness run is in progress' (lock blocked).
    setsid_lines = [
        line for line in harness.setsid_log_text().splitlines() if "setsid bash" in line
    ]
    log_text = harness.log_text()
    blocked_count = log_text.count("another liveness run is in progress")
    assert len(setsid_lines) + blocked_count >= 1, (
        f"neither dispatch nor lock-block observed; log:\n{log_text}"
    )
    assert len(setsid_lines) <= 1, (
        f"single-flight violated; setsid dispatch invoked {len(setsid_lines)} times:\n"
        f"{harness.setsid_log_text()}\n--- liveness log ---\n{log_text}"
    )
