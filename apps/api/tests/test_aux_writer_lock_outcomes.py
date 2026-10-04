"""Task 5: auxiliary CLI lock-deferral + dry-run contract.

Spec rules exercised here:

* Each auxiliary CLI (foreign-bars, same-day, no-trade-evidence)
  translates ``WriterLockBusy`` raised inside its first pending
  mutation to a single bounded ``DEFER writer-lock-busy`` line
  and exits 75. Counters (``bars``, ``moex_no_trade_evidence``,
  ``instruments.listed_till``) must not change.
* ``WriterLockError`` (invalid role/phase/path) is a real failure
  and must NOT be swallowed.
* The existing ``--dry-run`` modes never acquire the shared writer
  lock and never mutate SQLite, even when another process is
  actually holding the shared lock for longer than the CLI's
  injected timeout.
* The bounded diagnostic line carries ONLY the safe metadata
  fields (``role``, ``phase``, ``pid`` mirror via timeout/reason,
  ``database_path``, ``lock_path``, ``timeout_seconds``,
  ``reason``, ``result``); no upstream payload, token, or
  URL userinfo can leak through it.

Test design:

* One parametrized harness drives each CLI in turn with a
  real-subprocess lock holder and monkeypatched fetch helpers.
* The CLI's ``main()`` runs in-process so we can pin the
  monkeypatched ``writer_lock`` shim AND capture stdout/stderr
  via ``contextlib.redirect_stdout``.
* HTTP-bound fetchers are stubbed; no real network, no real
  Tinkoff, no real MOEX.
* The DB is a fresh file-backed temp file per test, migrated
  via the project's migration runner, so ``flock`` is real.
* Counters (bars, evidence, listed_till) are snapshotted
  before and after every CLI invocation and asserted equal.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sqlite3
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

_API_ROOT = Path(__file__).resolve().parent.parent
_API_SRC = _API_ROOT / "src"
if str(_API_SRC) not in sys.path:
    sys.path.insert(0, str(_API_SRC))


# ---------------------------------------------------------------------------
# Parametrization: per-CLI fixtures
# ---------------------------------------------------------------------------
#
# Each entry names the script under test, the modules whose HTTP
# helpers must be stubbed, the call site that must observe
# ``WriterLockBusy`` (a callable label the production code is
# expected to wrap so the test can decide where the busy occurs),
# the role the lock is acquired with, the phase, the lock
# timeout that the test monkeypatches, and the seeded figi /
# ticker that the script must drive through fetch/evaluation to
# the first pending mutation.


SCRIPTS = {
    "foreign_bars": {
        "script": _API_ROOT / "scripts" / "backfill_foreign_bars.py",
        "module_name": "fb_script_under_test",
        "seed_figi": "FBG00FB00001",
        "seed_ticker": "FB1",
        "seed_isin": "US0000FB0001",
        "args": ["--limit", "1", "--sleep", "0", "--reopen-every", "1"],
        # We need a fresh bar that lands after the last trading day
        # so the figi is "stale" and enters the todo list, then the
        # MOEX-board prefilter rejects it (no primary board → goes
        # to the Tinkoff path → reaches the writer lock).
        "last_bar": True,
    },
    "same_day": {
        "script": _API_ROOT / "scripts" / "catchup_same_day.py",
        "module_name": "sd_script_under_test",
        "seed_figi": "BBG00SD000A1",
        "seed_ticker": "SD1",
        "seed_isin": "RU000A0SD0A1",
        "args": ["--force", "--sleep", "0", "--limit", "1"],
        # Need a recent bar AND a session that looks newer than the
        # last trading day; we stub ``_last_trading_day`` so the
        # script's session date is "today" and yesterday is the
        # last completed session.
        "last_bar": True,
    },
    "no_trade_evidence": {
        "script": _API_ROOT / "scripts" / "backfill_no_trade_evidence.py",
        "module_name": "nte_script_under_test",
        "seed_figi": "BBG00NT000A1",
        "seed_ticker": "NT1",
        "seed_isin": "RU000A0NT0A1",
        "args": ["--limit", "1", "--days", "60", "--sleep", "0"],
        # Stale bar dated BEFORE the delisted listed_till
        # (``2026-09-10``) so the script enters the
        # ``listed_till`` lock branch instead of the
        # ``no_meta`` skip. The dry-run path uses a different
        # meta stub (a still-listed board) so the dry marker
        # fires through the in-window path.
        "last_bar_iso": "2026-08-15",
    },
}


_STALE_BAR_ISO = {
    # For the busy + secrets tests we want a bar OLDER than
    # the delisted listed_till (``2026-09-10``) so the
    # no-trade-evidence CLI enters the ``listed_till`` lock
    # branch.
    "no_trade_evidence": "2026-08-15",
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _migrate(db: Path) -> None:
    from algotrader_api.db import sqlite as sqlitedb
    from algotrader_api.db.migrations import MIGRATIONS_DIR

    sqlitedb.close_all()
    sqlitedb.run_migrations(str(db), MIGRATIONS_DIR)
    sqlitedb.close_all()


def _seed(db: Path, figi: str, ticker: str, isin: str, *,
          class_: str = "bond", bar_iso: str | None = None) -> None:
    """Seed one instrument and (optionally) a stale bar.

    ``bar_iso`` controls the date of the bar; ``None`` means
    "use the script's default stale-bar heuristic
    (today - 10 days)". The no-trade-evidence busy + secrets
    tests need a bar OLDER than the delisted listed_till so
    the script enters the listed-till lock branch.
    """
    con = sqlite3.connect(str(db))
    try:
        con.execute(
            "INSERT OR REPLACE INTO instruments "
            "(ticker, figi, class, name, currency, lot_size, isin, source_updated_at) "
            "VALUES (?, ?, ?, ?, 'RUB', 1, ?, '2025-01-01')",
            (ticker, figi, class_, ticker, isin),
        )
        if bar_iso is not None:
            con.execute(
                "INSERT INTO bars (figi, ts, open, high, low, close, volume, source) "
                "VALUES (?, ?, 1, 1, 1, 1, 1, 'tinkoff')",
                (figi, bar_iso),
            )
        con.commit()
    finally:
        con.close()


def _counters(db: Path) -> dict:
    """Snapshot every counter the spec mentions."""
    con = sqlite3.connect(str(db))
    try:
        bars = con.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
        ev = con.execute(
            "SELECT COUNT(*) FROM moex_no_trade_evidence"
        ).fetchone()[0]
        lt = con.execute(
            "SELECT COUNT(*) FROM instruments WHERE listed_till IS NOT NULL"
        ).fetchone()[0]
        return {"bars": bars, "evidence": ev, "listed_till": lt}
    finally:
        con.close()


def _holder_script(db_path: str, ready_sentinel: str) -> str:
    """Real-subprocess lock holder: takes the shared writer lock
    on ``<db>.writer.lock`` and writes a ready sentinel strictly
    INSIDE the held context, then sleeps long enough for any
    short-timeout contender to time out. The lock file is
    created by ``os.open`` before ``flock`` returns, so we cannot
    rely on file existence alone to detect "holder is ready"
    (a contender might race the kernel). The sentinel is the
    authoritative signal.
    """
    return (
        "import time\n"
        "from pathlib import Path\n"
        "from algotrader_api.ingestion.writer_lock import writer_lock\n"
        f"ready = Path({ready_sentinel!r})\n"
        f"with writer_lock(Path({db_path!r}),\n"
        "                  role='bar-writer', phase='bars',\n"
        "                  timeout_seconds=10.0):\n"
        "    ready.write_text('ready')\n"
        "    time.sleep(2.0)\n"
    )


def _spawn_holder(db_path: str, ready_sentinel: Path) -> subprocess.Popen:
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(_API_SRC),
        "PYTHONHOME": "",
    }
    proc = subprocess.Popen(
        [sys.executable, "-c", _holder_script(db_path, str(ready_sentinel))],
        env=env,
    )
    # Wait for the ready sentinel with a generous timeout — the
    # holder opens the lock file then takes flock, so the sentinel
    # race window is the time between ``os.open`` returning and
    # the body of the ``with`` statement. 5s is enough on any
    # realistic host.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if ready_sentinel.exists():
            return proc
        time.sleep(0.01)
    proc.kill()
    proc.wait(timeout=5.0)
    pytest.fail(
        f"holder never wrote ready sentinel {ready_sentinel}"
    )
    return proc  # unreachable


def _kill_holder(proc: subprocess.Popen) -> None:
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5.0)


def _load_script(name: str):
    """Import the CLI as a fresh module and return it.

    The CLI is a script with no ``algotrader_api.scripts.``
    package, so we load it by file path. Re-imports per test
    keep monkeypatching per-test (the
    ``backfill_no_trade_evidence_lock`` precedent uses the same
    pattern).
    """
    spec = importlib.util.spec_from_file_location(
        SCRIPTS[name]["module_name"],
        str(SCRIPTS[name]["script"]),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _stub_http_for(name: str, mod, monkeypatch, *, figi: str, ticker: str,
                    isin: str, dry_run: bool) -> None:
    """Stub every HTTP-bound helper each CLI uses.

    Every test runs against ``--db`` pointing at a temp DB; the
    stub returns one in-memory candidate that the script will
    accept, so the script reaches its first pending mutation /
    its first lock acquisition / its dry-run print. No real
    network requests.

    ``dry_run`` controls which branch the script should walk:

    * ``dry_run=True`` — the script's existing print-only branch
      must produce a ``[dry]`` line and never reach the lock
      acquisition. We set up meta + fetch so the script enters
      the still-listed (or delisted-with-no-bars-after-listed)
      path and prints the dry marker.
    * ``dry_run=False`` — busy-test branch. We want the script
      to reach the FIRST pending mutation (the lock
      acquisition) as fast as possible. The lock shim is
      responsible for raising ``WriterLockBusy``; this helper
      only configures the upstream fakes.
    """
    from algotrader_api.ingestion import backfill as backfill_mod
    from algotrader_api.ingestion import no_trade_evidence as nte_mod

    # The three CLIs all share the same backfill helpers.
    if name == "foreign_bars":
        # No primary board: figi is "broker-only" so it goes
        # through the Tinkoff path → reaches the writer lock.
        # For dry-run, we still want the same flow (the script
        # prints the dry marker and returns; ``replace_bars_for_figi``
        # is never called).
        monkeypatch.setattr(
            backfill_mod, "_get_meta_moex", lambda *a, **kw: None,
        )
        monkeypatch.setattr(mod, "_get_meta_moex", lambda *a, **kw: None)

        # Return one bar so the script is "fetched + non-empty"
        # and proceeds to the pending ``replace_bars_for_figi``
        # call.
        async def _fake_fetch(client, retry_mod, figi_arg, ticker_arg,
                               lo, hi):
            return [{
                "ts": date.today().isoformat(),
                "open": 100, "high": 110, "low": 95, "close": 105,
                "volume": 1000,
                "figi": figi_arg, "source": "tinkoff",
            }]
        monkeypatch.setattr(
            backfill_mod, "_fetch_tinkoff_fallback_impl", _fake_fetch,
        )
        monkeypatch.setattr(
            mod, "_fetch_tinkoff_fallback_impl", _fake_fetch,
        )
        # The foreign-bars script uses a Tinkoff client; force
        # the in-memory fake so the real SDK is never loaded.
        monkeypatch.setenv("ALGOTRADER_INGEST_FAKE", "1")
    elif name == "same_day":
        # The script's session is "today", so we need:
        #   - _last_trading_day returns yesterday
        #   - meta returns a board
        #   - identity returns a matching ISIN
        #   - _fetch_moex_range returns a same-day bar
        prev = date.today() - timedelta(days=1)

        def _ltd(today, db_path, **_):
            return prev
        monkeypatch.setattr(backfill_mod, "_last_trading_day", _ltd)
        monkeypatch.setattr(mod, "_last_trading_day", _ltd)

        def _meta(ticker_arg, last_session, *, meta_cache, meta_lock):
            return {
                "market": "shares",
                "board": "TQBR",
                "listed_from": "2020-01-01",
                "listed_till": "2099-12-31",
            }
        monkeypatch.setattr(backfill_mod, "_get_meta_moex", _meta)
        monkeypatch.setattr(mod, "_get_meta_moex", _meta)

        def _identity(ticker_arg):
            return {"board": "TQBR", "isin": isin}
        monkeypatch.setattr(nte_mod, "fetch_issuer_identity", _identity)
        monkeypatch.setattr(mod, "fetch_issuer_identity", _identity)

        def _fetch(market, board, ticker_arg, from_d, to_d, *,
                    last_trading_day=None):
            return [{
                "ts": date.today().isoformat(),
                "open": 100, "high": 110, "low": 95, "close": 105,
                "volume": 1000,
                "_secid": ticker_arg, "_boardid": "TQBR",
                "_numtrades": 10, "_value": 105000,
                "figi": figi, "source": "moex",
            }]
        monkeypatch.setattr(backfill_mod, "_fetch_moex_range", _fetch)
        monkeypatch.setattr(mod, "_fetch_moex_range", _fetch)
    else:  # no_trade_evidence
        if dry_run:
            # Still-listed path: meta returns a board so the
            # script enters the non-delisted branch and the
            # dry-run print fires. ``_fetch_moex_range`` returns
            # a zero-trade row so the print has something to
            # announce.
            def _meta(ticker_arg, last_session, *, meta_cache, meta_lock):
                return {
                    "market": "bonds",
                    "board": "TQCB",
                    "listed_from": "2020-01-01",
                    "listed_till": "2099-12-31",
                }
            monkeypatch.setattr(backfill_mod, "_get_meta_moex", _meta)
            monkeypatch.setattr(mod, "_get_meta_moex", _meta)
        else:
            # Busy-test path: no primary board (delisted branch)
            # so the script reaches the ``listed_till`` lock
            # acquisition on the first pending mutation.
            monkeypatch.setattr(
                backfill_mod, "_get_meta_moex", lambda *a, **kw: None,
            )
            monkeypatch.setattr(mod, "_get_meta_moex", lambda *a, **kw: None)
            monkeypatch.setattr(
                mod, "_probe_board_last", lambda t: ("TQCB", "2026-09-10", isin),
            )
        # Return one zero-trade row so the second lock
        # (``evidence``) would also be exercised if not for the
        # busy stub on the first one.
        def _fetch(market, board, ticker_arg, from_d, to_d, *,
                    last_trading_day=None):
            return [{
                "ts": "2026-09-08",
                "open": None, "high": None, "low": None, "close": None,
                "volume": 0,
                "_secid": ticker_arg, "_boardid": board,
                "_numtrades": 0, "_value": 0,
                "figi": figi, "source": "moex",
            }]
        monkeypatch.setattr(backfill_mod, "_fetch_moex_range", _fetch)
        monkeypatch.setattr(mod, "_fetch_moex_range", _fetch)


def _short_timeout(*, name: str) -> float:
    """Per-CLI explicit short timeout for the lock acquisition.

    The production CLIs call ``writer_lock`` without a custom
    ``timeout_seconds`` (so they get the 30s default). For the
    busy tests we monkeypatch ``writer_lock`` to ALWAYS raise
    ``WriterLockBusy`` on the first acquisition — the timeout
    argument is therefore irrelevant for those tests. For the
    dry-run tests the production default is irrelevant too
    because the dry-run path must not enter the lock at all
    (the brief is explicit: "no lock acquisition").
    """
    return 0.05


# ---------------------------------------------------------------------------
# Step 1 + Step 2 RED: rc=75 translation and dry-run lock-free
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SCRIPTS.keys()))
def test_cli_busy_emits_one_defer_line_and_exits_75(
    tmp_path, monkeypatch, name,
):
    """Step 1: each CLI exits 75 with a bounded DEFER line when its
    first pending mutation cannot acquire the shared writer lock.

    Counters (bars / evidence / listed_till) must remain
    unchanged; the CLI must NOT have performed any pending
    mutation.
    """
    cfg = SCRIPTS[name]
    db = tmp_path / f"{name}.db"
    _migrate(db)
    _seed(db, cfg["seed_figi"], cfg["seed_ticker"], cfg["seed_isin"],
          bar_iso=_STALE_BAR_ISO.get(name,
              (date.today() - timedelta(days=10)).isoformat()))
    before = _counters(db)

    mod = _load_script(name)
    _stub_http_for(name, mod, monkeypatch,
                   figi=cfg["seed_figi"], ticker=cfg["seed_ticker"],
                   isin=cfg["seed_isin"], dry_run=False)

    # Force EVERY writer_lock acquisition to raise busy at the
    # boundary. The test's job is to prove the CLI catches the
    # busy and returns 75 with bounded diagnostics; the FIRST
    # acquisition is the one we want to observe.
    #
    # Patch points: the CLI's own module (only when it
    # imports ``writer_lock`` directly — no-trade-evidence
    # does, foreign-bars and same-day do NOT because they go
    # through ``replace_bars_for_figi``), the writer_lock
    # source module, and ``bars_sqlite`` (the module that
    # foreign-bars / same-day actually call through).
    from algotrader_api.ingestion import writer_lock as wl_mod
    from algotrader_api.db import bars_sqlite
    busy_calls: list[dict] = []

    def _busy(db_path, **kw):
        busy_calls.append({"db_path": str(db_path), **kw})
        raise wl_mod.WriterLockBusy(
            role=kw.get("role", "?"),
            phase=kw.get("phase", "?"),
            database_path=str(db_path),
            lock_path=str(db_path) + ".writer.lock",
            timeout_seconds=kw.get("timeout_seconds", 0.0),
            reason="test-forced-busy-task5",
            result="deferred",
        )
    if hasattr(mod, "writer_lock"):
        monkeypatch.setattr(mod, "writer_lock", _busy)
    monkeypatch.setattr(wl_mod, "writer_lock", _busy)
    monkeypatch.setattr(bars_sqlite, "writer_lock", _busy)

    buf_out = io.StringIO()
    buf_err = io.StringIO()
    monkeypatch.setattr(sys, "argv", [
        cfg["module_name"], "--db", str(db), *cfg["args"],
    ])
    with contextlib.redirect_stdout(buf_out), \
         contextlib.redirect_stderr(buf_err):
        rc = mod.main()
    output = buf_out.getvalue() + buf_err.getvalue()

    assert rc == 75, (
        f"{name}: expected rc=75, got {rc}, output={output!r}"
    )
    defer_lines = [
        ln for ln in output.splitlines()
        if ln.startswith("DEFER writer-lock-busy")
    ]
    assert len(defer_lines) == 1, (
        f"{name}: expected exactly one DEFER line, got "
        f"{defer_lines!r} in output={output!r}"
    )
    line = defer_lines[0]
    # Bounded fields only. No payload / token / URL userinfo.
    for sentinel in ("BEARER-SECRET-TOKEN-12345",
                      "user:pass@example.com",
                      "X-PAYLOAD-FOR-LEAK-TEST"):
        assert sentinel not in line, (
            f"{name}: DEFER line leaks sentinel {sentinel!r}: {line!r}"
        )
    # Bounded fields: the role/phase must match the first
    # acquisition. Each CLI uses its own role.
    expected_role = {
        "foreign_bars": "bar-writer",
        "same_day": "bar-writer",
        "no_trade_evidence": "no-trade-evidence",
    }[name]
    assert f"role={expected_role}" in line, (
        f"{name}: role missing/wrong in DEFER line: {line!r}"
    )
    # All 8 spec-required fields must be present (writer-coordination
    # spec line 184: role, phase, PID, database path, lock path,
    # timeout, reason, result).
    for k in ("role=", "phase=", "pid=", "database_path=",
              "lock_path=", "timeout=", "reason=", "result="):
        assert k in line, f"{name}: field {k!r} missing: {line!r}"
    assert "timeout=" in line, f"{name}: timeout missing: {line!r}"
    assert "reason=test-forced-busy-task5" in line, (
        f"{name}: reason missing: {line!r}"
    )
    assert "result=deferred" in line, f"{name}: result missing: {line!r}"

    # No pending mutation. Counters must equal the snapshot.
    after = _counters(db)
    assert after == before, (
        f"{name}: counters changed despite busy: "
        f"before={before} after={after}"
    )


# ---------------------------------------------------------------------------
# Step 2 RED: dry-run must not acquire the lock
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SCRIPTS.keys()))
def test_cli_dry_run_does_not_acquire_lock_or_mutate(
    tmp_path, monkeypatch, name,
):
    """Step 2: ``--dry-run`` must not acquire the shared writer
    lock and must not mutate SQLite, even when a real
    subprocess is holding the lock for longer than the CLI's
    internal timeout.
    """
    cfg = SCRIPTS[name]
    db = tmp_path / f"{name}_dry.db"
    _migrate(db)
    _seed(db, cfg["seed_figi"], cfg["seed_ticker"], cfg["seed_isin"],
          bar_iso=_STALE_BAR_ISO.get(name,
              (date.today() - timedelta(days=10)).isoformat()))
    before = _counters(db)

    # Real-subprocess holder. The CLI must NOT wait for the
    # holder: ``--dry-run`` short-circuits before any lock
    # acquisition.
    ready = tmp_path / f"{name}_holder_ready"
    holder = _spawn_holder(str(db), ready)
    try:
        mod = _load_script(name)
        _stub_http_for(name, mod, monkeypatch,
                       figi=cfg["seed_figi"], ticker=cfg["seed_ticker"],
                       isin=cfg["seed_isin"], dry_run=True)

        # Track acquisitions via a counting shim that DOES
        # pass through to the real (held) lock — but the
        # production code is responsible for never entering
        # the lock during dry-run, so the shim should record
        # zero calls. Patch the CLI's own module AND the
        # module the CLI goes through (bars_sqlite for
        # foreign-bars / same-day).
        from algotrader_api.ingestion import writer_lock as wl_mod
        from algotrader_api.db import bars_sqlite
        acquisitions: list[dict] = []
        real_cm = wl_mod.writer_lock

        from contextlib import contextmanager

        @contextmanager
        def _tracking(db_path, **kw):
            acquisitions.append({"db_path": str(db_path), **kw})
            with real_cm(db_path, **kw):
                yield
        if hasattr(mod, "writer_lock"):
            monkeypatch.setattr(mod, "writer_lock", _tracking)
        monkeypatch.setattr(wl_mod, "writer_lock", _tracking)
        monkeypatch.setattr(bars_sqlite, "writer_lock", _tracking)

        buf_out = io.StringIO()
        buf_err = io.StringIO()
        monkeypatch.setattr(sys, "argv", [
            cfg["module_name"], "--db", str(db), "--dry-run",
            *cfg["args"],
        ])
        t0 = time.monotonic()
        with contextlib.redirect_stdout(buf_out), \
             contextlib.redirect_stderr(buf_err):
            rc = mod.main()
        elapsed = time.monotonic() - t0

        output = buf_out.getvalue() + buf_err.getvalue()
        assert rc == 0, (
            f"{name} --dry-run: expected rc=0, got {rc}, "
            f"output={output!r}"
        )
        # The dry-run must NOT have waited for the holder. The
        # holder holds the lock for 2.0s; if the CLI waited for
        # it the elapsed time would be > 1s. 1.0s is a generous
        # ceiling that still catches "the CLI did try to take
        # the lock and got busy" — busy would take the full
        # 30s default timeout.
        assert elapsed < 1.0, (
            f"{name} --dry-run: CLI took {elapsed:.2f}s; "
            f"appears to have waited for the holder"
        )
        assert acquisitions == [], (
            f"{name} --dry-run: writer lock was acquired: "
            f"{acquisitions!r}"
        )
        # The dry-run marker should appear in output (the
        # no-trade-evidence CLI uses "[dry]"; foreign-bars and
        # same-day use "[dry]" too).
        assert "[dry]" in output, (
            f"{name} --dry-run: dry marker missing: {output!r}"
        )
    finally:
        _kill_holder(holder)

    after = _counters(db)
    assert after == before, (
        f"{name} --dry-run: counters changed: "
        f"before={before} after={after}"
    )


# ---------------------------------------------------------------------------
# Step 4: secret-safe diagnostic test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SCRIPTS.keys()))
def test_cli_defer_line_has_no_payload_token_or_userinfo(
    tmp_path, monkeypatch, name,
):
    """Step 4: the bounded DEFER line must contain only the
    approved safe metadata fields. Test sentinel values
    planted in token / password / URL-userinfo inputs must
    NOT appear in the DEFER line.
    """
    cfg = SCRIPTS[name]
    db = tmp_path / f"{name}_secrets.db"
    _migrate(db)
    _seed(db, cfg["seed_figi"], cfg["seed_ticker"], cfg["seed_isin"],
          bar_iso=_STALE_BAR_ISO.get(name,
              (date.today() - timedelta(days=10)).isoformat()))

    # Plant sentinels in the most likely upstream-payload /
    # token / URL-userinfo inputs. We do this by overriding the
    # stubbed fetchers to RETURN dicts whose values include the
    # sentinel tokens, AND by overriding the WriterLockBusy
    # exception fields the helper exposes to carry sentinels
    # from a caller's perspective. The CLI's DEFER line is
    # built from the exception's sanitized fields, so any leak
    # from there into the line is a regression.
    SECRET_TOKEN = "BEARER-SECRET-TOKEN-12345"
    SECRET_USERINFO = "user:pass@example.com"
    SECRET_PAYLOAD = "X-PAYLOAD-FOR-LEAK-TEST"

    from algotrader_api.ingestion import backfill as backfill_mod
    from algotrader_api.ingestion import no_trade_evidence as nte_mod

    mod = _load_script(name)
    # Reuse the same stubbing machinery but plant sentinels in
    # the returned data.
    if name == "foreign_bars":
        async def _leaky_fetch(client, retry_mod, figi_arg, ticker_arg,
                                lo, hi):
            return [{
                "ts": date.today().isoformat(),
                "open": 100, "high": 110, "low": 95, "close": 105,
                "volume": 1000,
                "figi": figi_arg, "source": "tinkoff",
                # Sentinels inside the payload — must NOT leak.
                "comment": SECRET_PAYLOAD,
                "auth": SECRET_TOKEN,
            }]
        monkeypatch.setattr(backfill_mod, "_get_meta_moex",
                             lambda *a, **kw: None)
        monkeypatch.setattr(mod, "_get_meta_moex", lambda *a, **kw: None)
        monkeypatch.setattr(backfill_mod, "_fetch_tinkoff_fallback_impl",
                             _leaky_fetch)
        monkeypatch.setattr(mod, "_fetch_tinkoff_fallback_impl",
                             _leaky_fetch)
        monkeypatch.setenv("ALGOTRADER_INGEST_FAKE", "1")
    elif name == "same_day":
        prev = date.today() - timedelta(days=1)
        monkeypatch.setattr(backfill_mod, "_last_trading_day",
                             lambda *a, **kw: prev)
        monkeypatch.setattr(mod, "_last_trading_day",
                             lambda *a, **kw: prev)
        monkeypatch.setattr(backfill_mod, "_get_meta_moex",
                             lambda *a, **kw: {
                                 "market": "shares", "board": "TQBR",
                                 "listed_from": "2020-01-01",
                                 "listed_till": "2099-12-31"})
        monkeypatch.setattr(mod, "_get_meta_moex",
                             lambda *a, **kw: {
                                 "market": "shares", "board": "TQBR",
                                 "listed_from": "2020-01-01",
                                 "listed_till": "2099-12-31"})
        monkeypatch.setattr(nte_mod, "fetch_issuer_identity",
                             lambda t: {"board": "TQBR",
                                         "isin": cfg["seed_isin"]})
        monkeypatch.setattr(mod, "fetch_issuer_identity",
                             lambda t: {"board": "TQBR",
                                         "isin": cfg["seed_isin"]})
        def _leaky_fetch(market, board, ticker_arg, from_d, to_d, *,
                          last_trading_day=None):
            return [{
                "ts": date.today().isoformat(),
                "open": 100, "high": 110, "low": 95, "close": 105,
                "volume": 1000,
                "_secid": ticker_arg, "_boardid": "TQBR",
                "_numtrades": 10, "_value": 105000,
                "figi": cfg["seed_figi"], "source": "moex",
                "comment": SECRET_PAYLOAD,
                "auth": SECRET_TOKEN,
            }]
        monkeypatch.setattr(backfill_mod, "_fetch_moex_range",
                             _leaky_fetch)
        monkeypatch.setattr(mod, "_fetch_moex_range", _leaky_fetch)
    else:  # no_trade_evidence
        monkeypatch.setattr(backfill_mod, "_get_meta_moex",
                             lambda *a, **kw: None)
        monkeypatch.setattr(mod, "_get_meta_moex", lambda *a, **kw: None)
        monkeypatch.setattr(mod, "_probe_board_last",
                             lambda t: ("TQCB", "2026-09-10", cfg["seed_isin"]))
        def _leaky_fetch(market, board, ticker_arg, from_d, to_d, *,
                          last_trading_day=None):
            return [{
                "ts": "2026-09-08",
                "open": None, "high": None, "low": None, "close": None,
                "volume": 0,
                "_secid": ticker_arg, "_boardid": board,
                "_numtrades": 0, "_value": 0,
                "figi": cfg["seed_figi"], "source": "moex",
                "comment": SECRET_PAYLOAD,
                "auth": SECRET_TOKEN,
            }]
        monkeypatch.setattr(backfill_mod, "_fetch_moex_range",
                             _leaky_fetch)
        monkeypatch.setattr(mod, "_fetch_moex_range", _leaky_fetch)

    # Force busy AND plant sentinels in the busy exception's
    # safe-looking fields. The CLI's DEFER renderer must not
    # echo these back unchanged.
    from algotrader_api.ingestion import writer_lock as wl_mod
    from algotrader_api.db import bars_sqlite
    def _busy(db_path, **kw):
        raise wl_mod.WriterLockBusy(
            role=kw.get("role", "?"),
            phase=kw.get("phase", "?"),
            database_path=str(db_path) + f"?token={SECRET_TOKEN}",
            lock_path=str(db_path) + f".writer.lock?u={SECRET_USERINFO}",
            timeout_seconds=kw.get("timeout_seconds", 0.0),
            reason="test-forced-busy-secrets",
            result="deferred",
        )
    if hasattr(mod, "writer_lock"):
        monkeypatch.setattr(mod, "writer_lock", _busy)
    monkeypatch.setattr(wl_mod, "writer_lock", _busy)
    monkeypatch.setattr(bars_sqlite, "writer_lock", _busy)

    buf_out = io.StringIO()
    buf_err = io.StringIO()
    monkeypatch.setattr(sys, "argv", [
        cfg["module_name"], "--db", str(db), *cfg["args"],
    ])
    with contextlib.redirect_stdout(buf_out), \
         contextlib.redirect_stderr(buf_err):
        rc = mod.main()
    output = buf_out.getvalue() + buf_err.getvalue()

    assert rc == 75, f"{name}: expected rc=75, got {rc}"
    for sentinel in (SECRET_TOKEN, SECRET_USERINFO, SECRET_PAYLOAD):
        assert sentinel not in output, (
            f"{name}: {sentinel!r} leaked through DEFER line"
        )
    # All 8 spec-required fields must be present even when the
    # exception's database_path / lock_path were tampered with —
    # the unsafe-path branches render [REDACTED] in place of the
    # path values, never omit the field entirely.
    defer_lines = [
        ln for ln in output.splitlines()
        if ln.startswith("DEFER writer-lock-busy")
    ]
    assert len(defer_lines) == 1, (
        f"{name}: expected exactly one DEFER line, got "
        f"{defer_lines!r} in output={output!r}"
    )
    line = defer_lines[0]
    for k in ("role=", "phase=", "pid=", "database_path=",
              "lock_path=", "timeout=", "reason=", "result="):
        assert k in line, (
            f"{name}: {k!r} missing under secrets-test path: {line!r}"
        )
    # database_path carried ?token=Bearer-... — the path must
    # have been redacted, NOT echoed verbatim.
    assert "database_path=[REDACTED]" in line, (
        f"{name}: tampered database_path not redacted: {line!r}"
    )
    assert f"lock_path=[REDACTED]" in line, (
        f"{name}: tampered lock_path not redacted: {line!r}"
    )
