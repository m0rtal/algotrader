"""Tests for the shared writer lock primitive (writer_lock module).

These tests drive the lock module through file-backed temporary SQLite
databases and real subprocesses. No production DB or process state is
touched.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import get_args

import pytest

# Make src/ importable when pytest runs from the repo root.
_API_ROOT = Path(__file__).resolve().parent.parent
_API_SRC = _API_ROOT / "src"
if str(_API_SRC) not in sys.path:
    sys.path.insert(0, str(_API_SRC))

from algotrader_api.ingestion import writer_lock as lock_module  # noqa: E402
from algotrader_api.ingestion.writer_lock import (  # noqa: E402
    WriterLockBusy,
    WriterLockError,
    WriterLockReentrant,
    assert_process_creation_allowed,
    writer_lock,
    writer_lock_path,
)


# ---------------------------------------------------------------------------
# Lock-path & validation
# ---------------------------------------------------------------------------


NEW_PAIRS = [
    ("universe-sync", "instruments"),
    ("backfill-metadata", "instruments"),
    ("backfill-metadata", "metadata"),
    ("corporate-actions", "corporate-actions"),
    ("corporate-actions", "adjusted-bars"),
    ("dividends", "dividends"),
]


@pytest.mark.parametrize("role,phase", NEW_PAIRS)
def test_daily_role_phase_is_valid(tmp_path, role, phase):
    assert role in get_args(lock_module.WriterRole)
    assert role in lock_module.VALID_ROLES
    assert phase in get_args(lock_module.WriterPhase)
    assert phase in lock_module.VALID_PHASES
    with writer_lock(tmp_path / "state.db", role=role, phase=phase,
                     timeout_seconds=0.05):
        pass


@pytest.mark.parametrize("role,phase", [
    ("bar-writer", "bars"),
    ("foreign-bars", "bars"),
    ("same-day", "listed-till"),
    ("no-trade-evidence", "evidence"),
    ("expected-bars", "expected-bars"),
    ("evidence-reconcile", "reconcile"),
])
def test_original_role_phase_remains_valid(tmp_path, role, phase):
    assert role in get_args(lock_module.WriterRole)
    assert role in lock_module.VALID_ROLES
    assert phase in get_args(lock_module.WriterPhase)
    assert phase in lock_module.VALID_PHASES
    with writer_lock(tmp_path / "state.db", role=role, phase=phase,
                     timeout_seconds=0.05):
        pass


def test_writer_lock_path_derives_lockfile(tmp_path):
    db = tmp_path / "a.db"
    assert writer_lock_path(db) == Path(f"{db}.writer.lock")
    # Sanity: no lock file created until acquisition.
    assert not (Path(f"{db}.writer.lock")).exists()


def test_writer_lock_path_distinct_per_db(tmp_path):
    a = writer_lock_path(tmp_path / "a.db")
    b = writer_lock_path(tmp_path / "b.db")
    assert a != b
    assert a.name == "a.db.writer.lock"
    assert b.name == "b.db.writer.lock"


def test_writer_lock_path_accepts_string(tmp_path):
    s = str(tmp_path / "s.db")
    assert writer_lock_path(s) == Path(f"{s}.writer.lock")


def test_writer_lock_rejects_oversize_basename(tmp_path, monkeypatch):
    """A basename longer than NAME_MAX is rejected before any mutation.

    We force a tiny NAME_MAX surrogate so we don't have to build a
    >255-char path on every host. The helper validates the basename
    length against ``os.pathconf`` when available, but the simplest
    way to prove the fail-closed branch is to pass an explicit value
    and assert WriterLockError is raised without creating the lock
    file. We do this by feeding an overlong path whose basename
    itself exceeds NAME_MAX on Linux (255 bytes).
    """
    long_name = "x" * (os.pathconf(tmp_path, "PC_NAME_MAX") + 1)
    db = tmp_path / f"{long_name}.db"
    lock = writer_lock_path(db)
    with pytest.raises(WriterLockError):
        with writer_lock(db, role="bar-writer", phase="bars",
                          timeout_seconds=0.0):
            pytest.fail("body must not execute on unsafe lock path")
    # The lock path itself exceeds NAME_MAX and so cannot exist on a
    # POSIX filesystem. Probe that the kernel can't even stat it
    # (ENAMETOOLONG). If somehow the filesystem accepts the name, fail
    # loudly.
    try:
        st = os.stat(str(lock))
    except OSError as e:
        if e.errno not in (36, 63):  # ENAMETOOLONG, ENOSYS
            pytest.fail(f"unexpected stat error on unsafe path: {e!r}")
    else:
        pytest.fail(f"unsafe lock path was created: {st}")


@pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW"),
    reason="platform lacks os.O_NOFOLLOW",
)
def test_writer_lock_rejects_existing_symlink_lock(tmp_path):
    """A pre-existing symlink at the lock path is rejected before the body.

    ``os.open(..., O_CREAT|O_RDWR|O_NOFOLLOW, ...)`` raises FileExistsError
    on Linux when the target is a symlink; the helper translates that to
    WriterLockError and yields without entering the body.
    """
    db = tmp_path / "victim.db"
    real_lock = tmp_path / "real.writer.lock"
    real_lock.write_text("placeholder")
    symlink_path = Path(f"{db}.writer.lock")
    os.symlink(real_lock, symlink_path)
    try:
        with pytest.raises(WriterLockError):
            with writer_lock(db, role="bar-writer", phase="bars",
                              timeout_seconds=0.0):
                pytest.fail("body must not execute on symlink lock path")
    finally:
        symlink_path.unlink(missing_ok=True)


def test_writer_lock_rejects_invalid_role(tmp_path):
    db = tmp_path / "x.db"
    with pytest.raises(WriterLockError):
        with writer_lock(db, role="not-a-role", phase="bars",
                          timeout_seconds=0.0):
            pass


def test_writer_lock_rejects_invalid_phase(tmp_path):
    db = tmp_path / "x.db"
    with pytest.raises(WriterLockError):
        with writer_lock(db, role="bar-writer", phase="bogus",
                          timeout_seconds=0.0):
            pass


# ---------------------------------------------------------------------------
# Ownership: single-process acquire/release
# ---------------------------------------------------------------------------


def test_writer_lock_acquire_and_release(tmp_path):
    db = tmp_path / "x.db"
    with writer_lock(db, role="bar-writer", phase="bars",
                      timeout_seconds=0.5):
        # Held section: a second acquisition on the same path must fail.
        with pytest.raises(WriterLockReentrant):
            with writer_lock(db, role="bar-writer", phase="bars",
                              timeout_seconds=0.0):
                pass
    # After release, a fresh acquisition succeeds.
    with writer_lock(db, role="bar-writer", phase="bars",
                      timeout_seconds=0.5):
        pass


def test_writer_lock_release_on_body_exception(tmp_path):
    db = tmp_path / "x.db"
    sentinel = RuntimeError("body exploded")
    with pytest.raises(RuntimeError):
        with writer_lock(db, role="bar-writer", phase="bars",
                          timeout_seconds=0.5):
            raise sentinel
    # Lock must be released — a fresh acquisition succeeds.
    with writer_lock(db, role="bar-writer", phase="bars",
                      timeout_seconds=0.5):
        pass


def test_daily_role_does_not_bypass_same_process_thread_guard(tmp_path):
    db = tmp_path / "thread.db"
    outcomes = []

    def contender():
        try:
            with writer_lock(db, role="dividends", phase="dividends",
                             timeout_seconds=0.05):
                outcomes.append("entered")
        except WriterLockReentrant as exc:
            outcomes.append(exc)

    with writer_lock(db, role="universe-sync", phase="instruments",
                     timeout_seconds=0.05):
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert len(outcomes) == 1
        assert isinstance(outcomes[0], WriterLockReentrant)
    with writer_lock(db, role="dividends", phase="dividends",
                     timeout_seconds=0.05):
        pass


def test_writer_lock_independent_paths_dont_block(tmp_path):
    a = tmp_path / "a.db"
    b = tmp_path / "b.db"
    start = time.monotonic()
    with writer_lock(a, role="bar-writer", phase="bars",
                      timeout_seconds=0.5):
        with writer_lock(b, role="bar-writer", phase="bars",
                          timeout_seconds=0.5):
            pass
    elapsed = time.monotonic() - start
    # Sequential: well under 0.4s combined timeout budget.
    assert elapsed < 0.4


# ---------------------------------------------------------------------------
# Ownership: contention / bounded timeouts
# ---------------------------------------------------------------------------


def _wait_for_holder_ready(sentinel: Path, *, timeout: float = 5.0) -> None:
    """Wait for the holder subprocess to write its inside-the-context
    sentinel. The lock file is created *before* ``flock`` returns, so
    waiting for the lock file alone races the kernel and lets the
    loser try to acquire before the holder has actually held it.
    The sentinel is written by the holder inside the ``with`` block,
    strictly after ``flock`` succeeded, so it is the real "holder is
    ready" signal.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sentinel.exists():
            return
        time.sleep(0.01)
    pytest.fail(
        f"holder never wrote ready sentinel {sentinel} within {timeout}s"
    )


def test_writer_lock_timeout_raises_busy(tmp_path):
    """A second process cannot acquire the lock while a holder keeps it.

    Uses a subprocess to avoid the process-local reentrancy guard —
    the loser runs in the test process, the holder in a child.
    """
    db = str(tmp_path / "x.db")
    ready = tmp_path / "holder_ready"
    holder_script = (
        "import time\n"
        "from pathlib import Path\n"
        "from algotrader_api.ingestion.writer_lock import writer_lock\n"
        f"ready = Path({str(ready)!r})\n"
        f"with writer_lock(Path({db!r}),\n"
        "                  role=\"bar-writer\", phase=\"bars\",\n"
        "                  timeout_seconds=5.0):\n"
        "    ready.write_text(\"ready\")\n"
        "    time.sleep(1.0)\n"
    )
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(_API_SRC),
        "PYTHONHOME": "",
    }
    holder = subprocess.Popen([sys.executable, "-c", holder_script], env=env)
    _wait_for_holder_ready(ready)
    try:
        with pytest.raises(WriterLockBusy):
            with writer_lock(db, role="bar-writer", phase="bars",
                              timeout_seconds=0.05):
                pass
    finally:
        holder.wait(timeout=5.0)


def test_writer_lock_timeout_subprocess_sentinel(tmp_path):
    """Subprocess-loser tries to acquire with timeout=0.05 while a
    holder keeps the lock. The loser must raise WriterLockBusy, must
    NOT have entered the critical section, and must NOT have written
    a sentinel that is supposed to live only inside the section.
    """
    db = str(tmp_path / "x.db")
    sentinel = str(tmp_path / "sentinel")
    ready = tmp_path / "holder_ready"
    # Holder subprocess.
    holder_script = (
        "import time\n"
        "from pathlib import Path\n"
        "from algotrader_api.ingestion.writer_lock import writer_lock\n"
        f"ready = Path({str(ready)!r})\n"
        f"with writer_lock(Path({db!r}), role=\"bar-writer\", phase=\"bars\",\n"
        "                  timeout_seconds=5.0):\n"
        "    ready.write_text(\"ready\")\n"
        "    time.sleep(1.0)\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_script],
        env={"PATH": os.environ["PATH"],
             "PYTHONPATH": str(_API_SRC),
             "PYTHONHOME": ""},
    )
    _wait_for_holder_ready(ready)
    try:
        # Loser subprocess tries with timeout 0.05. The sentinel is
        # written *inside* the with-body; if the body never runs,
        # the sentinel must be absent.
        loser_script = (
            "import json, sys\n"
            "from pathlib import Path\n"
            "from algotrader_api.ingestion.writer_lock import (\n"
            "    writer_lock, WriterLockBusy,\n"
            ")\n"
            "try:\n"
            f"    with writer_lock(Path({db!r}),\n"
            "                      role=\"bar-writer\", phase=\"bars\",\n"
            "                      timeout_seconds=0.05):\n"
            f"        Path({sentinel!r}).write_text(\"mutated\")\n"
            "except WriterLockBusy as exc:\n"
            "    print(json.dumps({\"role\": exc.role, \"phase\": exc.phase,\n"
            "                       \"timeout\": exc.timeout_seconds}))\n"
            "    sys.exit(0)\n"
            "sys.exit(1)\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", loser_script],
            env={"PATH": os.environ["PATH"],
                 "PYTHONPATH": str(_API_SRC),
                 "PYTHONHOME": ""},
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        assert proc.returncode == 0, (
            f"loser returned {proc.returncode}, stdout={proc.stdout!r} "
            f"stderr={proc.stderr!r}"
        )
        payload = json.loads(proc.stdout.strip())
        assert payload["role"] == "bar-writer"
        assert payload["phase"] == "bars"
        assert payload["timeout"] == 0.05
        assert not Path(sentinel).exists(), (
            "loser entered the critical section — mutation sentinel was written"
        )
    finally:
        holder.wait(timeout=5.0)


# ---------------------------------------------------------------------------
# Cross-process peak-occupancy = 1
# ---------------------------------------------------------------------------


def test_writer_lock_peak_occupancy_one(tmp_path):
    """Two subprocesses race for the same lock with shared counter; peak
    occupancy measured by both must be exactly 1.
    """
    db = str(tmp_path / "x.db")
    counter_path_str = str(tmp_path / "peak.json")
    lp_a = str(tmp_path / "lp_a")
    lp_b = str(tmp_path / "lp_b")

    body_template = (
        "import json, time\n"
        "from pathlib import Path\n"
        "from algotrader_api.ingestion.writer_lock import writer_lock\n"
        "\n"
        f"path = Path({counter_path_str!r})\n"
        f"local = Path({lp_a!r})\n"
        "\n"
        f"with writer_lock(Path({db!r}),\n"
        "                  role=\"bar-writer\", phase=\"bars\",\n"
        "                  timeout_seconds=10.0):\n"
        "    data = json.loads(path.read_text())\n"
        "    data['current'] += 1\n"
        "    if data['current'] > data['peak']:\n"
        "        data['peak'] = data['current']\n"
        "    path.write_text(json.dumps(data))\n"
        "    time.sleep(0.15)\n"
        "    data = json.loads(path.read_text())\n"
        "    data['current'] -= 1\n"
        "    path.write_text(json.dumps(data))\n"
        "    local.write_text(str(json.loads(path.read_text())['peak']))\n"
    )

    counter_path = Path(counter_path_str)
    counter_path.write_text(json.dumps({"current": 0, "peak": 0}))
    procs = []
    for lp in (lp_a, lp_b):
        script = body_template.replace(f"Path({lp_a!r})", f"Path({lp!r})")
        procs.append(subprocess.Popen(
            [sys.executable, "-c", script],
            env={"PATH": os.environ["PATH"],
                 "PYTHONPATH": str(_API_SRC),
                 "PYTHONHOME": ""},
        ))
    for p in procs:
        rc = p.wait(timeout=10.0)
        assert rc == 0
    peak = json.loads(counter_path.read_text())["peak"]
    assert peak == 1


def test_writer_lock_independent_dbs_in_subprocess(tmp_path):
    """Two subprocesses, two distinct DBs, both succeed without waiting."""
    a = str(tmp_path / "a.db")
    b = str(tmp_path / "b.db")
    a_lock = str(tmp_path / "a.lock")
    b_lock = str(tmp_path / "b.lock")

    def script_for(db, flag):
        return (
            "import time\n"
            "from pathlib import Path\n"
            "from algotrader_api.ingestion.writer_lock import writer_lock\n"
            "start = time.monotonic()\n"
            f"with writer_lock(Path({db!r}),\n"
            "                  role=\"bar-writer\", phase=\"bars\",\n"
            "                  timeout_seconds=0.5):\n"
            f"    Path({flag!r}).write_text(\"acquired\")\n"
            "    time.sleep(0.2)\n"
            f"Path({flag!r} + '.t').write_text(str(time.monotonic() - start))\n"
        )

    procs = []
    for db, flag in ((a, a_lock), (b, b_lock)):
        procs.append(subprocess.Popen(
            [sys.executable, "-c", script_for(db, flag)],
            env={"PATH": os.environ["PATH"],
                 "PYTHONPATH": str(_API_SRC),
                 "PYTHONHOME": ""},
        ))
    rc_codes = [p.wait(timeout=5.0) for p in procs]
    for p in procs:
        assert p.returncode == 0
    # Inside the lock each subprocess sleeps 0.2s. Sequential would be
    # ~0.4s+startup; parallel ~0.2s+startup. Read each subprocess's
    # elapsed time from a sentinel file written before sleep.
    times = []
    for flag in (a_lock, b_lock):
        times.append(float(Path(flag + ".t").read_text()))
    assert max(times) < 0.4, (
        f"subprocesses took {times}s — did not run in parallel"
    )


# ---------------------------------------------------------------------------
# Forked child inherits descriptor; parent leaves context; third acquires
# ---------------------------------------------------------------------------


def test_writer_lock_fork_child_keeps_fd(tmp_path):
    """``flock`` locks are owned by the open file description, not the
    process. After ``os.fork`` both parent and child share the same
    file description. When the parent leaves the context it does
    ``LOCK_UN`` and the kernel-level lock is released even though the
    child still holds its fd. A third process can therefore acquire
    immediately after the parent exits — the child staying alive
    does not extend the lock.

    Test-only kernel semantics: production critical sections still
    prohibit process creation. The subprocess below forks inside the
    held context in an isolated process so the test process itself
    never calls ``os.fork`` while holding a lock.
    """
    db = str(tmp_path / "x.db")
    parent_done = str(tmp_path / "parent_done")
    child_alive = str(tmp_path / "child_alive")
    third_acquired = str(tmp_path / "third_acquired")

    parent_script = (
        "import os, time\n"
        "from pathlib import Path\n"
        "from algotrader_api.ingestion.writer_lock import writer_lock\n"
        f"db = {db!r}\n"
        f"parent_done = Path({parent_done!r})\n"
        f"child_alive = Path({child_alive!r})\n"
        # Enter the held context first, then fork so the child
        # inherits the lock fd.
        f"with writer_lock(Path(db), role=\"bar-writer\", phase=\"bars\",\n"
        "                  timeout_seconds=5.0):\n"
        "    pid = os.fork()\n"
        "    if pid == 0:\n"
        "        # Child: signals it's alive, sleeps holding the\n"
        "        # inherited fd, then exits cleanly.\n"
        "        child_alive.write_text(str(os.getpid()))\n"
        "        time.sleep(1.5)\n"
        "        os._exit(0)\n"
        "    # Parent: leave the context (releases flock on the\n"
        "    # shared file description), signal completion, then\n"
        "    # reap the child cleanly so no zombie remains.\n"
        "parent_done.write_text(\"parent-exited\")\n"
        "_, status = os.waitpid(pid, 0)\n"
        "if status != 0:\n"
        "    raise SystemExit(f\"child exited with {status}\")\n"
    )
    third_script = (
        "from pathlib import Path\n"
        "from algotrader_api.ingestion.writer_lock import writer_lock\n"
        f"with writer_lock(Path({db!r}),\n"
        "                  role=\"bar-writer\", phase=\"bars\",\n"
        "                  timeout_seconds=2.0):\n"
        f"    Path({third_acquired!r}).write_text(\"ok\")\n"
    )
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(_API_SRC),
        "PYTHONHOME": "",
    }
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_script],
        env=env,
    )
    # Wait for the parent to enter the with-block, fork, and then
    # exit. The child (still alive) holds the inherited fd.
    for _ in range(500):
        time.sleep(0.02)
        if Path(parent_done).exists():
            break
    else:
        parent.kill()
        parent.wait(timeout=5.0)
        pytest.fail("parent never finished")
    # The parent has released the flock on the shared file
    # description; the child still has its inherited fd but the
    # kernel-level lock is on the description, not the process. A
    # third process must therefore be able to acquire while the
    # child is still alive.
    try:
        third_after = subprocess.run(
            [sys.executable, "-c", third_script],
            env=env,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    finally:
        parent.wait(timeout=5.0)
    assert third_after.returncode == 0, (
        f"third returned {third_after.returncode}, "
        f"stderr={third_after.stderr!r}"
    )
    assert Path(third_acquired).exists()


# ---------------------------------------------------------------------------
# assert_process_creation_allowed
# ---------------------------------------------------------------------------


def test_assert_process_creation_allowed_outside_lock(tmp_path):
    # Outside any lock: must be a no-op.
    assert_process_creation_allowed()


def test_assert_process_creation_allowed_inside_lock_raises(tmp_path):
    db = tmp_path / "x.db"
    with writer_lock(db, role="bar-writer", phase="bars",
                      timeout_seconds=0.5):
        with pytest.raises(WriterLockError):
            assert_process_creation_allowed()


# ---------------------------------------------------------------------------
# Path canonicalization: aliased same-DB reentry is a process-local conflict
# ---------------------------------------------------------------------------


def test_writer_lock_aliased_same_db_is_reentrant(tmp_path):
    """``./x.db`` and ``subdir/../x.db`` resolve to the same file, so
    the process-local guard treats them as a single canonical path and
    raises :class:`WriterLockReentrant` on the second acquire.
    """
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    rel = tmp_path / "x.db"
    aliased = subdir / ".." / "x.db"
    with writer_lock(rel, role="bar-writer", phase="bars",
                      timeout_seconds=0.5):
        with pytest.raises(WriterLockReentrant):
            with writer_lock(aliased, role="bar-writer", phase="bars",
                              timeout_seconds=0.0):
                pass


def test_writer_lock_absolute_path_canonical_lock_name(tmp_path):
    """``writer_lock_path`` returns ``<absolute-db>.writer.lock`` when
    given an absolute path. Existing absolute temp-path callers (the
    contention tests above) rely on this; preserve the behaviour.
    """
    db = tmp_path / "y.db"
    lock = writer_lock_path(db)
    assert lock.name == f"{db.name}.writer.lock"
    assert lock.is_absolute() == db.is_absolute()


def test_writer_lock_path_canonicalizes_symlinked_db(tmp_path):
    """``writer_lock_path`` collapses a symlinked DB to the same lock
    path as the real DB so the two aliases cannot create two distinct
    kernel lock files.
    """
    real = tmp_path / "real.db"
    real.write_text("")
    link = tmp_path / "link.db"
    os.symlink(real, link)
    real_lock = writer_lock_path(real)
    link_lock = writer_lock_path(link)
    assert real_lock == link_lock, (
        f"symlinked DB produced two lock paths: {real_lock} vs {link_lock}"
    )
    # The canonical lock must be the one derived from the real DB
    # (not the alias), so they share the same inode.
    assert real_lock == Path(f"{real}.writer.lock")


def test_writer_lock_canonicalizes_symlinked_db_under_caller(tmp_path):
    """Even when the caller passes a relative or ``..``-aliased DB
    path, ``writer_lock_path`` returns the canonical (absolute) lock
    path. Two different call sites that resolve to the same real file
    therefore produce the same lock path.
    """
    real = tmp_path / "real.db"
    real.write_text("")
    sub = tmp_path / "sub"
    sub.mkdir()
    aliased = sub / ".." / "real.db"
    via_cwd = tmp_path.name  # used only as an opaque token
    p_real = writer_lock_path(real)
    p_aliased = writer_lock_path(aliased)
    assert p_real == p_aliased
    # And the lock file we would open is the canonical one.
    assert str(p_real).endswith("real.db.writer.lock")


@pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW"),
    reason="platform lacks os.O_NOFOLLOW",
)
def test_writer_lock_symlinked_db_reentrant(tmp_path):
    """When two callers use different aliases of the same DB in the
    same process, the canonical lock path makes them collide and the
    second raises :class:`WriterLockReentrant` (not a silently
    acquired second kernel lock).
    """
    real = tmp_path / "real.db"
    real.write_text("")
    link = tmp_path / "link.db"
    os.symlink(real, link)
    with writer_lock(real, role="bar-writer", phase="bars",
                      timeout_seconds=0.5):
        with pytest.raises(WriterLockReentrant):
            with writer_lock(link, role="bar-writer", phase="bars",
                              timeout_seconds=0.0):
                pass


@pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW"),
    reason="platform lacks os.O_NOFOLLOW",
)
def test_writer_lock_symlinked_db_cross_process_contention(tmp_path):
    """A holder taking the lock through the real DB and a contender
    taking it through a symlink alias must contend on the same kernel
    lock file. We assert that by holding on the real path and timing
    out on the alias.
    """
    real = str(tmp_path / "real.db")
    link = str(tmp_path / "link.db")
    os.symlink(real, link)
    ready = tmp_path / "holder_ready"
    holder_script = (
        "import time\n"
        "from pathlib import Path\n"
        "from algotrader_api.ingestion.writer_lock import writer_lock\n"
        f"ready = Path({str(ready)!r})\n"
        f"with writer_lock(Path({real!r}),\n"
        "                  role=\"bar-writer\", phase=\"bars\",\n"
        "                  timeout_seconds=5.0):\n"
        "    ready.write_text(\"ready\")\n"
        "    time.sleep(1.0)\n"
    )
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(_API_SRC),
        "PYTHONHOME": "",
    }
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_script], env=env
    )
    _wait_for_holder_ready(ready)
    try:
        with pytest.raises(WriterLockBusy):
            with writer_lock(link, role="bar-writer", phase="bars",
                              timeout_seconds=0.05):
                pass
    finally:
        holder.wait(timeout=5.0)


def test_writer_lock_path_resolve_error_is_lockerror(tmp_path):
    """If path resolution itself raises (e.g. on a non-existent
    parent with strict=True), ``writer_lock_path`` must surface the
    failure as :class:`WriterLockError` rather than ``OSError`` so the
    public contract stays narrow.
    """
    # A relative path that contains a non-existent ``..``-escape
    # is harmless to resolve(); instead we drive the strict path
    # via monkeypatching ``Path.resolve`` on a temporary subclass.
    from algotrader_api.ingestion import writer_lock as wl_mod

    def _boom(self, *args, **kwargs):
        raise OSError(2, "synthetic resolve failure")

    db = tmp_path / "x.db"
    original = wl_mod.Path.resolve
    wl_mod.Path.resolve = _boom  # type: ignore[assignment]
    try:
        with pytest.raises(WriterLockError):
            writer_lock_path(db)
    finally:
        wl_mod.Path.resolve = original  # type: ignore[assignment]


def test_writer_lock_aliased_path_metadata_uses_canonical(tmp_path):
    """The :class:`WriterLockBusy` exception must report the canonical
    lock path even when the caller passed a relative/aliased form.
    """
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    rel = tmp_path / "x.db"
    aliased = subdir / ".." / "x.db"
    holder = subprocess.Popen(
        [sys.executable, "-c",
         f"from pathlib import Path\n"
         f"from algotrader_api.ingestion.writer_lock import writer_lock\n"
         f"with writer_lock(Path({str(aliased)!r}), "
         f"role='bar-writer', phase='bars', timeout_seconds=5.0):\n"
         f"    import time; time.sleep(1.0)\n"],
        env={"PATH": os.environ["PATH"],
             "PYTHONPATH": str(_API_SRC),
             "PYTHONHOME": ""},
    )
    try:
        _wait_for_holder_ready_using_lockfile(Path(str(rel) + ".writer.lock"))
        try:
            with pytest.raises(WriterLockBusy) as excinfo:
                with writer_lock(rel, role="bar-writer", phase="bars",
                                  timeout_seconds=0.05):
                    pass
        finally:
            holder.wait(timeout=5.0)
        busy = excinfo.value
        # Both paths must be the canonical (absolute) form.
        assert os.path.isabs(busy.database_path)
        assert os.path.isabs(busy.lock_path)
        assert busy.lock_path.endswith(".writer.lock")
        assert os.path.realpath(busy.database_path) == os.path.realpath(str(rel))
    finally:
        holder.wait(timeout=5.0)


def _wait_for_holder_ready_using_lockfile(lock_path: Path, *, timeout: float = 5.0) -> None:
    """For canonical-path tests: wait for the lock file to exist (the
    alias is gone, so we can rely on the kernel-visible file)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if lock_path.exists():
            return
        time.sleep(0.01)
    pytest.fail(f"lock file {lock_path} never appeared within {timeout}s")


# ---------------------------------------------------------------------------
# Timeout validation: fail-closed on non-finite / non-numeric / bool
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        True,                 # bool is a subclass of int — reject explicitly
        False,
        "0.5",                # str — even when it parses
        None,
        float("nan"),
        float("inf"),
        float("-inf"),
        [0.5],                # any other type
        {"timeout": 0.5},
    ],
    ids=[
        "bool_true", "bool_false", "str", "none",
        "nan", "pos_inf", "neg_inf", "list", "dict",
    ],
)
def test_writer_lock_rejects_non_finite_or_non_numeric_timeout(tmp_path, bad):
    db = tmp_path / "x.db"
    with pytest.raises(WriterLockError):
        with writer_lock(db, role="bar-writer", phase="bars",
                          timeout_seconds=bad):
            pass


@pytest.mark.parametrize("neg", [-0.5, -1, -1e9])
def test_writer_lock_rejects_negative_timeout(tmp_path, neg):
    db = tmp_path / "x.db"
    with pytest.raises(WriterLockError):
        with writer_lock(db, role="bar-writer", phase="bars",
                          timeout_seconds=neg):
            pass


# ---------------------------------------------------------------------------
# WriterLockBusy: complete metadata + sanitization / length cap
# ---------------------------------------------------------------------------


def test_writer_lock_busy_metadata_is_complete(tmp_path):
    """All advertised fields are populated, correctly typed, and the
    lock path / database path are the canonical (absolute) forms.
    """
    db = str(tmp_path / "x.db")
    db_path = Path(db)
    holder = subprocess.Popen(
        [sys.executable, "-c",
         f"from pathlib import Path\n"
         f"from algotrader_api.ingestion.writer_lock import writer_lock\n"
         f"with writer_lock(Path({db!r}), "
         f"role='bar-writer', phase='bars', timeout_seconds=5.0):\n"
         f"    import time; time.sleep(1.0)\n"],
        env={"PATH": os.environ["PATH"],
             "PYTHONPATH": str(_API_SRC),
             "PYTHONHOME": ""},
    )
    try:
        _wait_for_holder_ready_using_lockfile(
            db_path.parent / f"{db_path.name}.writer.lock"
        )
        with pytest.raises(WriterLockBusy) as excinfo:
            with writer_lock(db, role="bar-writer", phase="bars",
                              timeout_seconds=0.05):
                pass
        busy = excinfo.value
        assert busy.role == "bar-writer"
        assert busy.phase == "bars"
        assert busy.pid == os.getpid()
        assert os.path.isabs(busy.database_path)
        assert os.path.isabs(busy.lock_path)
        assert busy.database_path == str(db_path.resolve(strict=False))
        assert busy.lock_path == str(
            (db_path.parent / f"{db_path.name}.writer.lock").resolve(strict=False)
        )
        assert busy.timeout_seconds == 0.05
        assert busy.reason == "flock-timeout"
        assert busy.result == "deferred"
        # str(exc) is safe for logs.
        assert "writer lock busy" in str(busy)
    finally:
        holder.wait(timeout=5.0)


def test_writer_lock_busy_sanitizes_control_chars():
    """Control characters in caller-supplied strings are stripped
    before reaching the diagnostic stream.
    """
    busy = WriterLockBusy(
        role="bar-writer",
        phase="bars",
        database_path="/tmp/x.db\x1b[31m",
        lock_path="/tmp/x.db.writer.lock\x07",
        timeout_seconds=0.5,
        reason="flock-timeout\nnext-line",
        result="deferred",
    )
    assert "\x1b" not in busy.database_path
    assert "\x07" not in busy.lock_path
    assert "\n" not in busy.reason
    # No raw <ESC>, no bell, no newline survives.
    for field in (busy.database_path, busy.lock_path, busy.reason):
        for ch in field:
            assert ord(ch) >= 0x20, f"control char leaked: {field!r}"


def test_writer_lock_busy_caps_field_length():
    """Each field is capped at ``_MAX_FIELD`` chars."""
    huge = "x" * 1000
    busy = WriterLockBusy(
        role=huge,
        phase=huge,
        database_path=huge,
        lock_path=huge,
        timeout_seconds=0.5,
        reason=huge,
        result=huge,
    )
    assert len(busy.role) == WriterLockBusy._MAX_FIELD
    assert len(busy.phase) == WriterLockBusy._MAX_FIELD
    assert len(busy.database_path) == WriterLockBusy._MAX_FIELD
    assert len(busy.lock_path) == WriterLockBusy._MAX_FIELD
    assert len(busy.reason) == WriterLockBusy._MAX_FIELD
    assert len(busy.result) == WriterLockBusy._MAX_FIELD
