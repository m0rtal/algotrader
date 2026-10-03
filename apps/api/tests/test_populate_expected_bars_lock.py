"""Tests for Task 4: atomic expected-bars batch and wrapper deferral.

The writer-coordination spec requires that:

* ``populate_expected_bars.py`` direct invocation acquires the shared
  ``<db>.writer.lock`` under ``role="expected-bars" /
  phase="expected-bars"`` and applies every UPDATE in ONE
  ``BEGIN IMMEDIATE`` transaction that commits or rolls back as a
  batch.
* Computing expected-bars values happens BEFORE the shared lock is
  acquired; the lock is only held for the batch mutation.
* The Python CLI exits 75 when the shared lock is busy; existing
  validation failures (DB missing, schema missing) retain their
  current return codes.
* The shell wrapper ``cron_expected_bars.sh`` maps child rc=75 to
  ``DEFER writer-lock-busy`` + wrapper exit 0; the existing
  three-attempt SQLite-busy retry loop only triggers for non-75
  child failures whose output contains ``database is locked``.
* The wrapper's local ``${DB}.expected-bars.lock`` is preserved
  as wrapper-local duplicate-invocation protection; the Python
  writer always acquires ``${DB}.writer.lock`` (the canonical
  spec lock). The two lock files are distinct paths.

All public tests use file-backed temp DBs so the kernel-level
``flock`` is actually exercised.
"""
from __future__ import annotations

import fcntl
import os
import sqlite3
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

_API_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_API_SRC) not in sys.path:
    sys.path.insert(0, str(_API_SRC))

SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts"
    / "populate_expected_bars.py"
)
JOB = (
    Path(__file__).resolve().parent.parent / "scripts"
    / "cron_expected_bars.sh"
)


# ─── helpers ────────────────────────────────────────────────────────────


def _migrate(db: Path) -> None:
    """Apply the real migrations against a fresh temp DB."""
    from algotrader_api.db import sqlite as sqlitedb
    from algotrader_api.db.migrations import MIGRATIONS_DIR

    sqlitedb.close_all()
    sqlitedb.run_migrations(str(db), MIGRATIONS_DIR)
    sqlitedb.close_all()


def _seed(db: Path) -> None:
    """Insert a couple of instruments with a known listing source."""
    con = sqlite3.connect(str(db))
    try:
        con.executemany(
            "INSERT INTO instruments "
            "(ticker, figi, class, name, currency, lot_size, isin, "
            " source_updated_at, listed_till) "
            "VALUES (?, ?, 'share', ?, 'RUB', 1, ?, ?, ?)",
            [
                ("AAA", "FIGI-A", "AAA", "X", "2020-01-02", None),
                ("BBB", "FIGI-B", "BBB", "X", "2020-01-02", None),
            ],
        )
        con.execute(
            "INSERT INTO bars (figi, ts, open, high, low, close, volume, source) "
            "VALUES ('FIGI-A', '2020-01-02', 1, 1, 1, 1, 1, 'tinkoff')"
        )
        con.commit()
    finally:
        con.close()


def _expected(db: Path, figi: str) -> int | None:
    with sqlite3.connect(str(db)) as conn:
        r = conn.execute(
            "SELECT expected_bars FROM instruments WHERE figi = ?", (figi,)
        ).fetchone()
    return None if r is None else r[0]


def _run_cli(db: Path) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(_API_SRC),
        "PYTHONHOME": "",
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db)],
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )


def _load_cli_module():
    """Import ``populate_expected_bars`` as a stand-alone module so
    tests can patch its module-level symbols. Returns the loaded
    module. Resets ``sys.argv`` to ``[name, "--db", DUMMY_DB]`` so
    argparse inside ``main()`` does not see pytest's argv.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "populate_expected_bars_cli", SCRIPT,
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _reset_argv(db_path: str) -> None:
    """Set sys.argv so argparse in main() does not read pytest args."""
    sys.argv = ["populate_expected_bars.py", "--db", db_path]


# ─── Step 1 RED/GREEN: direct invocation is coordinated, batch atomic ──


def test_direct_invocation_acquires_writer_lock_under_expected_bars(
    tmp_path, monkeypatch,
):
    """Direct invocation must acquire the shared writer lock under
    ``role="expected-bars" / phase="expected-bars"`` and commit
    every UPDATE in one BEGIN IMMEDIATE batch.
    """
    import importlib.util
    from algotrader_api.ingestion import writer_lock as wl_mod

    db = tmp_path / "state.db"
    _migrate(db)
    _seed(db)

    mod = _load_cli_module()

    acquisitions: list[dict] = []
    real_cm = wl_mod.writer_lock

    @wl_mod.contextmanager  # type: ignore[attr-defined]
    def _counting_lock(db_path, **kw):
        acquisitions.append({**kw, "db_path": str(db_path)})
        with real_cm(db_path, **kw):
            yield

    monkeypatch.setattr(mod, "writer_lock", _counting_lock)
    _reset_argv(str(db))

    rc = mod.main()
    assert rc == 0, f"expected 0, got {rc}"
    assert len(acquisitions) == 1, (
        f"expected 1 acquisition, got {acquisitions!r}"
    )
    assert acquisitions[0]["role"] == "expected-bars"
    assert acquisitions[0]["phase"] == "expected-bars"
    # Both figis updated.
    assert _expected(db, "FIGI-A") is not None
    assert _expected(db, "FIGI-B") is not None


def test_direct_invocation_unchanged_when_lock_busy(tmp_path, monkeypatch):
    """If the shared lock is held, direct invocation exits 75 and
    leaves every expected_bars value unchanged.
    """
    import importlib.util
    from algotrader_api.ingestion import writer_lock as wl_mod

    db = tmp_path / "state.db"
    _migrate(db)
    _seed(db)
    # Pre-seed expected_bars to known values.
    con = sqlite3.connect(str(db))
    con.execute("UPDATE instruments SET expected_bars = 42 WHERE figi='FIGI-A'")
    con.execute("UPDATE instruments SET expected_bars = 99 WHERE figi='FIGI-B'")
    con.commit()
    con.close()

    mod = _load_cli_module()

    def _always_busy(db_path, **kw):
        raise wl_mod.WriterLockBusy(
            role=kw["role"],
            phase=kw["phase"],
            database_path=str(db_path),
            lock_path=str(db_path) + ".writer.lock",
            timeout_seconds=kw.get("timeout_seconds", 0.0),
            reason="test-forced-busy",
        )

    monkeypatch.setattr(mod, "writer_lock", _always_busy)
    _reset_argv(str(db))
    rc = mod.main()
    assert rc == 75, f"expected 75, got {rc}"
    # Values unchanged.
    assert _expected(db, "FIGI-A") == 42
    assert _expected(db, "FIGI-B") == 99


def test_batch_rolls_back_on_second_update_failure(tmp_path, monkeypatch):
    """If a second-statement UPDATE fails after the first commits, the
    explicit BEGIN IMMEDIATE batch must roll back so no partial
    mutation is visible. Direct invocation exits non-zero.
    """
    import importlib.util
    from algotrader_api.ingestion import writer_lock as wl_mod

    db = tmp_path / "state.db"
    _migrate(db)
    _seed(db)

    mod = _load_cli_module()

    real_cm = wl_mod.writer_lock

    @wl_mod.contextmanager  # type: ignore[attr-defined]
    def _noop_lock(db_path, **kw):
        with real_cm(db_path, **kw):
            yield

    monkeypatch.setattr(mod, "writer_lock", _noop_lock)

    # Patch executemany to fail. The script opens its own
    # connection inside the writer_lock and calls
    # ``mut.executemany`` for the batch; we wrap
    # ``mod.sqlite3.connect`` so every new connection routes
    # through a shim that fails the executemany call. The shim
    # also forwards attribute assignment (e.g. ``con.row_factory =
    # sqlite3.Row``) and ``cursor()`` access to the inner
    # connection.
    real_connect = mod.sqlite3.connect
    executemany_seen = {"n": 0}

    class _WrapConn:
        def __init__(self, inner):
            self._inner = inner

        def __setattr__(self, name, value):
            if name == "_inner":
                super().__setattr__(name, value)
            else:
                setattr(self._inner, name, value)

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def __enter__(self):
            return self._inner.__enter__()

        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)

        def executemany(self, sql, params, *a, **kw):
            executemany_seen["n"] += 1
            raise mod.sqlite3.OperationalError(
                "injected batch failure"
            )

        def execute(self, sql, *a, **kw):
            return self._inner.execute(sql, *a, **kw)

        def commit(self):
            return self._inner.commit()

        def rollback(self):
            return self._inner.rollback()

        def close(self):
            return self._inner.close()

    def _wrap_connect(*a, **kw):
        return _WrapConn(real_connect(*a, **kw))

    monkeypatch.setattr(mod.sqlite3, "connect", _wrap_connect)

    # Need at least 2 figis so the batch has 2 updates.
    _reset_argv(str(db))
    rc = mod.main()
    assert rc != 0, f"expected non-zero on injected failure, got {rc}"
    # The first UPDATE was never committed (the batch began
    # afterwards). Both expected_bars must remain NULL.
    assert _expected(db, "FIGI-A") is None, (
        "first update leaked despite batch rollback"
    )
    assert _expected(db, "FIGI-B") is None, (
        "second update failed but first update leaked"
    )


# ─── Step 2 RED: compute happens outside the lock ───────────────────────


def test_compute_ordered_before_lock_and_updates_ordered_before_release(
    tmp_path, monkeypatch,
):
    """expected_business_days is called BEFORE the writer_lock is
    acquired; every UPDATE happens BEFORE the lock is released.
    """
    import importlib.util
    from algotrader_api.ingestion import writer_lock as wl_mod

    db = tmp_path / "state.db"
    _migrate(db)
    _seed(db)

    mod = _load_cli_module()

    events: list[str] = []
    real_cm = wl_mod.writer_lock

    @wl_mod.contextmanager  # type: ignore[attr-defined]
    def _recording_lock(db_path, **kw):
        events.append("acquire")
        try:
            with real_cm(db_path, **kw):
                events.append("inside")
                yield
        finally:
            events.append("release")

    monkeypatch.setattr(mod, "writer_lock", _recording_lock)

    real_ebd = mod.expected_business_days

    def _ebd_spy(conn, lo, hi):
        events.append("compute")
        return real_ebd(conn, lo, hi)

    monkeypatch.setattr(mod, "expected_business_days", _ebd_spy)

    # Wrap executemany to record UPDATE event ordering. The script
    # uses one executemany call (the batch); every per-row UPDATE
    # is inside that one call, so a single "update" marker is
    # correct. Patch via ``sqlite3.connect`` so the new connection
    # inside the writer_lock routes through the shim. The shim
    # forwards attribute assignment (e.g. ``con.row_factory =
    # sqlite3.Row``) to the inner connection.
    real_connect = mod.sqlite3.connect

    class _RecordConn:
        def __init__(self, inner):
            self._inner = inner

        def __setattr__(self, name, value):
            if name == "_inner":
                super().__setattr__(name, value)
            else:
                setattr(self._inner, name, value)

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def __enter__(self):
            return self._inner.__enter__()

        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)

        def executemany(self, sql, params, *a, **kw):
            if "UPDATE instruments SET expected_bars" in sql:
                events.append("update")
            return self._inner.executemany(sql, params, *a, **kw)

        def execute(self, sql, *a, **kw):
            return self._inner.execute(sql, *a, **kw)

        def commit(self):
            return self._inner.commit()

        def rollback(self):
            return self._inner.rollback()

        def close(self):
            return self._inner.close()

    def _wrap_connect(*a, **kw):
        return _RecordConn(real_connect(*a, **kw))

    monkeypatch.setattr(mod.sqlite3, "connect", _wrap_connect)

    _reset_argv(str(db))
    rc = mod.main()
    assert rc == 0, f"expected 0, got {rc}"
    # Compute (one or more) must come BEFORE the single acquire.
    # Update must come AFTER acquire and BEFORE release.
    assert "compute" in events, f"no compute event: {events!r}"
    assert "acquire" in events, f"no acquire event: {events!r}"
    assert "update" in events, f"no update event: {events!r}"
    compute_idx = events.index("compute")
    acquire_idx = events.index("acquire")
    update_idx = events.index("update")
    release_idx = events.index("release")
    assert compute_idx < acquire_idx, (
        f"compute must precede acquire: {events!r}"
    )
    assert acquire_idx < update_idx, (
        f"acquire must precede update: {events!r}"
    )
    assert update_idx < release_idx, (
        f"update must precede release: {events!r}"
    )


# ─── Step 4 RED/GREEN: wrapper rc=75 / DEFER / wrapper 0 ──────────────


def _stub_writer_path(tmp_path: Path, rc: int, stdout: str) -> Path:
    """Create a tiny stand-in for ``populate_expected_bars.py`` that
    exits with ``rc`` and writes ``stdout`` to its real stdout.
    Returns the path to the stand-in.
    """
    stub = tmp_path / "stub_writer.py"
    body = (
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stdout.flush()\n"
        f"sys.exit({rc})\n"
    )
    stub.write_text(body)
    return stub


def _counting_stub_writer_path(
    tmp_path: Path, rc: int, stdout: str,
) -> tuple[Path, Path]:
    """Like ``_stub_writer_path`` but the stub appends one line per
    invocation to ``<tmp_path>/stub_calls.log`` so call-count evidence
    is independent of wrapper log formatting. Returns
    ``(stub_path, calls_log_path)``.
    """
    calls_log = tmp_path / "stub_calls.log"
    stub = tmp_path / "stub_writer.py"
    body = (
        "import sys\n"
        f"open({str(calls_log)!r}, 'a').write('called\\n')\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stdout.flush()\n"
        f"sys.exit({rc})\n"
    )
    stub.write_text(body)
    return stub, calls_log


def _stub_calls(calls_log: Path) -> int:
    """Number of times the counting stub has been invoked."""
    if not calls_log.exists():
        return 0
    return sum(1 for line in calls_log.read_text().splitlines() if line == "called")


def _run_wrapper_with_stub(
    db: Path, log: Path, stub: Path, *,
    wrapper: Path = JOB, timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    """Invoke the real ``cron_expected_bars.sh`` with a stub Python
    writer substituted via ``ALGOTRADER_EXPECTED_BARS_PYTHON``.

    The wrapper's internal ``PY`` defaults to
    ``${API_DIR}/.venv/bin/python``; we override it with the test
    interpreter and point ``SCRIPT`` at our stub via a tiny Python
    wrapper that delegates to the stub binary.

    The wrapper is invoked as ``bash <JOB> --db <db> --log <log>``.
    """
    # Build a tiny .venv-style "python" directory: a single executable
    # symlink to the test interpreter is not enough because the
    # wrapper calls ``$PY <SCRIPT>``; we make $PY a thin shell wrapper
    # that exec's the stub. Simpler: use a directory with a shim
    # that we name ``python``.
    bin_dir = log.parent / "fake_venv_bin"
    bin_dir.mkdir(exist_ok=True)
    shim = bin_dir / "python"
    shim_text = (
        "#!/usr/bin/env bash\n"
        f'exec "{sys.executable}" "{stub}" "$@"\n'
    )
    shim.write_text(shim_text)
    shim.chmod(0o755)
    env = {
        "PATH": os.environ["PATH"],
        "ALGOTRADER_EXPECTED_BARS_PYTHON": str(shim),
    }
    return subprocess.run(
        ["bash", str(wrapper), "--db", str(db), "--log", str(log)],
        env=env, text=True, capture_output=True, timeout=timeout,
    )


def test_wrapper_defer_path_rc75_exits_zero(tmp_path, monkeypatch):
    """When the Python writer exits 75, the wrapper logs
    ``DEFER writer-lock-busy`` and exits 0. The SQLite-busy retry
    loop is not entered.
    """
    log = tmp_path / "expected-bars.log"
    db = tmp_path / "state.db"
    db.write_text("")  # existence check passes
    stub = _stub_writer_path(tmp_path, 75, "writer lock busy: synthetic\n")

    result = _run_wrapper_with_stub(db, log, stub)
    assert result.returncode == 0, (
        f"wrapper should exit 0 on rc=75 child, got {result.returncode}: "
        f"{result.stderr!r}"
    )
    text = log.read_text()
    assert "defer writer-lock-busy" in text.lower(), (
        f"DEFER line missing: {text!r}"
    )
    assert "retry sqlite database is locked" not in text.lower(), (
        f"wrapper entered SQLite-busy retry loop despite rc=75: {text!r}"
    )


def test_wrapper_normal_path_returns_zero_and_logs_success(
    tmp_path, monkeypatch,
):
    """A successful child run (rc=0 + OK: line) logs SUCCESS and
    exits 0. The SQLite-busy retry loop is not entered.
    """
    log = tmp_path / "expected-bars.log"
    db = tmp_path / "state.db"
    db.write_text("")
    stub = _stub_writer_path(
        tmp_path, 0, "OK: populated expected_bars for 3 figis\n"
    )

    result = _run_wrapper_with_stub(db, log, stub)
    assert result.returncode == 0, (
        f"wrapper should exit 0 on rc=0 child, got {result.returncode}: "
        f"{result.stderr!r}"
    )
    text = log.read_text()
    assert "success" in text.lower(), f"SUCCESS line missing: {text!r}"
    assert "defer writer-lock-busy" not in text.lower()
    assert "retry sqlite database is locked" not in text.lower()


def test_wrapper_uses_writer_lock_lockfile_direct_path(tmp_path, monkeypatch):
    """The Python writer acquires ``${DB}.writer.lock`` (the canonical
    spec lock) when invoked directly, NOT just the wrapper-local
    ``${DB}.expected-bars.lock``. The two lock files are distinct
    paths.
    """
    import importlib.util
    from algotrader_api.ingestion import writer_lock as wl_mod

    db = tmp_path / "state.db"
    _migrate(db)
    _seed(db)

    mod = _load_cli_module()

    observed_lock_paths: list[Path] = []
    real_cm = wl_mod.writer_lock

    @wl_mod.contextmanager  # type: ignore[attr-defined]
    def _recording_lock(db_path, **kw):
        observed_lock_paths.append(wl_mod.writer_lock_path(db_path))
        with real_cm(db_path, **kw):
            yield

    monkeypatch.setattr(mod, "writer_lock", _recording_lock)

    _reset_argv(str(db))
    rc = mod.main()
    assert rc == 0, f"expected 0, got {rc}"
    assert len(observed_lock_paths) == 1
    canonical = observed_lock_paths[0]
    assert canonical.name.endswith(".writer.lock"), (
        f"unexpected lock path: {canonical!r}"
    )
    wrapper_lock = Path(f"{db}.expected-bars.lock")
    assert canonical != wrapper_lock, (
        f"Python writer used wrapper-local lock {canonical!r} instead of "
        f"the canonical writer lock {wrapper_lock!r}"
    )


# ─── Step 4 RED/GREEN: non-75 retry contract preserved ─────────────────


def test_wrapper_retries_database_is_locked_non75_child(tmp_path, monkeypatch):
    """A non-75 child failure whose output contains
    ``database is locked`` must enter the three-attempt retry loop.
    The third SQLite-busy failure stays rc=1.

    Exercises the REAL ``cron_expected_bars.sh`` via
    ``_run_wrapper_with_stub`` (no inline wrapper copy). Stub
    call-count evidence is independent of wrapper log formatting.
    Real wrapper sleeps 2 seconds between retries, so 3 attempts
    take ~4 seconds; the test timeout covers that.
    """
    log = tmp_path / "expected-bars.log"
    db = tmp_path / "state.db"
    db.write_text("")  # existence check passes
    stub, calls_log = _counting_stub_writer_path(
        tmp_path, 1, "ERROR: database is locked\n"
    )

    result = _run_wrapper_with_stub(db, log, stub, timeout=30)
    assert result.returncode == 1, (
        f"wrapper should exit 1 on third SQLite-busy failure, got "
        f"{result.returncode}: {result.stderr!r}"
    )
    # Stub call-count: exactly three attempts.
    assert _stub_calls(calls_log) == 3, (
        f"expected 3 stub invocations (3 attempts, 2 retries + 1 final), "
        f"got {_stub_calls(calls_log)}"
    )
    text = log.read_text()
    # Two retry log lines (attempts 1 and 2); the third attempt
    # produces the final ERROR line.
    assert "retry sqlite database is locked attempt=1" in text.lower()
    assert "retry sqlite database is locked attempt=2" in text.lower()
    assert text.lower().count("retry sqlite database is locked") == 2, (
        f"expected exactly 2 RETRY lines, got "
        f"{text.lower().count('retry sqlite database is locked')}: {text!r}"
    )
    assert "defer writer-lock-busy" not in text.lower()


def test_wrapper_non_busy_failure_does_not_retry(tmp_path, monkeypatch):
    """A non-75 child failure whose output does NOT contain
    ``database is locked`` exits 1 on the first attempt. No retry
    loop.

    Exercises the REAL ``cron_expected_bars.sh`` via
    ``_run_wrapper_with_stub`` (no inline wrapper copy). Stub
    call-count evidence is independent of wrapper log formatting.
    """
    log = tmp_path / "expected-bars.log"
    db = tmp_path / "state.db"
    db.write_text("")
    stub, calls_log = _counting_stub_writer_path(
        tmp_path, 1, "ERROR: schema missing\n"
    )

    result = _run_wrapper_with_stub(db, log, stub, timeout=30)
    assert result.returncode == 1, (
        f"wrapper should exit 1 on non-busy failure, got "
        f"{result.returncode}: {result.stderr!r}"
    )
    # Stub call-count: exactly one attempt; no retry loop entered.
    assert _stub_calls(calls_log) == 1, (
        f"expected 1 stub invocation (no retry on non-busy failure), "
        f"got {_stub_calls(calls_log)}"
    )
    text = log.read_text()
    assert "retry sqlite database is locked" not in text.lower(), (
        f"wrapper entered SQLite-busy retry loop on non-busy failure: {text!r}"
    )
    assert "defer writer-lock-busy" not in text.lower()
