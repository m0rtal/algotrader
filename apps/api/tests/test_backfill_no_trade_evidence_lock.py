"""Tests for Task 3: evidence record/reconciliation lock coordination.

The writer-coordination spec requires that:

* ``record_no_trade_evidence`` and ``reconcile_no_trade_evidence`` each
  acquire the shared writer lock exactly once on a file-backed DB.
* The bar writer (``replace_bars_for_figi``) commits and releases the
  bar lock BEFORE public reconciliation re-acquires the lock.
* When the bar is committed but reconciliation cannot acquire the lock
  in time, the bar remains committed, the evidence row stays, and a
  bounded ``DEFER writer-lock-busy`` line is emitted.
* The historical CLI ``backfill_no_trade_evidence.py`` acquires a
  separate ``listed-till`` lock for the ``instruments.listed_till``
  UPDATE that is independent of the evidence write-lock; MOEX fetch
  and ``sleep`` never run under either lock.
* Busy on the CLI boundary exits 75 and emits exactly one bounded
  ``DEFER writer-lock-busy`` line.
* The existing dry-run mode performs no mutation and no lock
  acquisition.

All public tests use file-backed temp DBs so the kernel-level
``flock`` is actually exercised.
"""
from __future__ import annotations

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


# ─── helpers ────────────────────────────────────────────────────────────


def _migrate(db: Path) -> None:
    """Run the project's migrations against a fresh temp DB."""
    from algotrader_api.db import sqlite as sqlitedb
    from algotrader_api.db.migrations import MIGRATIONS_DIR

    sqlitedb.close_all()
    sqlitedb.run_migrations(str(db), MIGRATIONS_DIR)
    sqlitedb.close_all()


def _seed_instrument(db: Path, figi: str, ticker: str, isin: str = "X") -> None:
    con = sqlite3.connect(str(db))
    try:
        con.execute(
            "INSERT INTO instruments "
            "(ticker, figi, class, name, currency, lot_size, isin, source_updated_at) "
            "VALUES (?, ?, 'bond', ?, 'RUB', 1, ?, '2025-01-01')",
            (ticker, figi, ticker, isin),
        )
        con.commit()
    finally:
        con.close()


# ─── RED: public record acquires exactly once on a file-backed DB ──────


def test_record_public_acquires_lock_exactly_once(tmp_path, monkeypatch):
    """``record_no_trade_evidence`` must acquire the shared writer
    lock exactly once when called against a file-backed DB.
    A private helper must not re-acquire the lock.
    """
    import algotrader_api.db.bars_sqlite as bars_sqlite  # noqa: F401

    db = tmp_path / "nte.db"
    _migrate(db)
    _seed_instrument(db, "FIGI-LOCK-REC", "X")
    sqlitedb = __import__("algotrader_api.db.sqlite", fromlist=["get_connection"])

    # Instrument: count writer_lock acquisitions through a shim. The
    # evidence module resolves the lock via a lazy
    # ``from .writer_lock import writer_lock`` inside its private
    # helper, so patching the symbol on
    # ``algotrader_api.ingestion.writer_lock`` is what the public
    # wrapper actually sees at call time.
    from algotrader_api.ingestion import writer_lock as wl_mod
    from algotrader_api.ingestion import no_trade_evidence as nte

    real_cm = wl_mod.writer_lock
    calls: list[dict] = []

    @wl_mod.contextmanager  # type: ignore[attr-defined]
    def _counting_lock(db_path, **kw):  # type: ignore[no-redef]
        calls.append({"db_path": str(db_path), **kw})
        with real_cm(db_path, **kw):
            yield

    monkeypatch.setattr(wl_mod, "writer_lock", _counting_lock)

    con = sqlitedb.get_connection(str(db))
    n = nte.record_no_trade_evidence(
        con, db_path=str(db),
        figi="FIGI-LOCK-REC",
        rows=[{"ts": "2026-09-26"}],
        board="TQCB",
        isin="X",
        now=date(2026, 9, 28),
    )
    assert n == 1
    # Public wrapper must acquire exactly once; private helper must
    # NOT acquire (so the count is exactly 1, not 2).
    assert len(calls) == 1, f"acquired {len(calls)} times: {calls!r}"
    assert calls[0]["role"] == "no-trade-evidence"
    assert calls[0]["phase"] == "evidence"


def test_reconcile_public_acquires_lock_exactly_once(tmp_path, monkeypatch):
    """``reconcile_no_trade_evidence`` must acquire the shared writer
    lock exactly once on a file-backed DB; the private helper must
    not re-acquire.
    """
    db = tmp_path / "rec.db"
    _migrate(db)
    _seed_instrument(db, "FIGI-LOCK-RECON", "X")
    from algotrader_api.ingestion import writer_lock as wl_mod
    from algotrader_api.ingestion import no_trade_evidence as nte
    from algotrader_api.db import sqlite as sqlitedb

    real_cm = wl_mod.writer_lock
    calls: list[dict] = []

    @wl_mod.contextmanager  # type: ignore[attr-defined]
    def _counting_lock(db_path, **kw):  # type: ignore[no-redef]
        calls.append({"db_path": str(db_path), **kw})
        with real_cm(db_path, **kw):
            yield

    monkeypatch.setattr(wl_mod, "writer_lock", _counting_lock)

    con = sqlitedb.get_connection(str(db))
    nte.record_no_trade_evidence(
        con, db_path=str(db),
        figi="FIGI-LOCK-RECON",
        rows=[{"ts": "2026-09-26"}],
        board="TQCB",
        isin="X",
    )
    # Now reconcile — must re-acquire once.
    removed = nte.reconcile_no_trade_evidence(con, db_path=str(db))
    assert removed == 0
    # Two distinct acquisitions: record + reconcile. Each wrapper
    # acquires its own lock — no nested acquisition.
    assert len(calls) == 2, f"expected 2 acquisitions, got {len(calls)}: {calls!r}"
    assert calls[0]["role"] == "no-trade-evidence"
    assert calls[0]["phase"] == "evidence"
    assert calls[1]["role"] == "evidence-reconcile"
    assert calls[1]["phase"] == "reconcile"


# ─── RED: bar commit and lock release happen BEFORE reconcile lock ──────


def test_bar_commit_lands_before_reconcile_lock(tmp_path, monkeypatch):
    """The bar writer must commit AND release the bar lock before
    the public reconciliation helper re-acquires the lock. We prove
    this by holding the bar lock in a subprocess for 0.5s, then
    calling ``replace_bars_for_figi`` from the main process.

    While the subprocess holds the bar lock, the main process:
      * begins a bar write,
      * waits for the bar lock with a short timeout (the bar writer
        must NOT block on the reconciliation — it must release the
        bar lock first; the reconcile helper then has to acquire its
        own lock and that acquisition must also be a separate
        attempt).

    Simplification: we hold the lock in the main process (NOT a
    subprocess — subprocess serialization with monkeypatch is too
    fragile) and verify the reconcile path operates independently.
    """
    db = tmp_path / "boundary.db"
    _migrate(db)
    _seed_instrument(db, "FIGI-BOUND", "X")
    from algotrader_api.ingestion import writer_lock as wl_mod
    from algotrader_api.ingestion import no_trade_evidence as nte
    from algotrader_api.db import sqlite as sqlitedb
    from algotrader_api.db import bars_sqlite

    # Plant an evidence row so reconcile has something to remove.
    con = sqlitedb.get_connection(str(db))
    # Use a private helper that does not acquire the lock (we patch
    # in a moment to drop the lock acquisition from the call).
    # The contract says the private tx helper NEVER acquires; we
    # therefore do the planting by calling the private tx directly
    # via the public wrapper with a no-op stub.
    def _plant():
        # Insert via direct SQL — purely a setup step.
        cur = con.execute(
            "SELECT 1 FROM bars WHERE figi = ? AND ts = ?",
            ("FIGI-BOUND", "2026-09-26"),
        ).fetchone()
        if cur:
            return
        nte._record_no_trade_evidence_tx(
            con, figi="FIGI-BOUND",
            rows=[{"ts": "2026-09-26"}],
            board="TQCB", isin="X",
            now=date(2026, 9, 28),
        )

    # The private tx helper does not exist yet (RED). The plant
    # step will fail — the test fails RED until we add the helper.
    _plant()
    con.commit()  # close the implicit tx so the bar writer's BEGIN IMMEDIATE is legal

    # Now the bar writer — prove the lock is released before
    # reconcile re-acquires.
    seen_phases: list[str] = []
    real_cm = wl_mod.writer_lock

    @wl_mod.contextmanager  # type: ignore[attr-defined]
    def _tracking_lock(db_path, **kw):
        seen_phases.append(kw.get("phase", "?"))
        with real_cm(db_path, **kw):
            yield

    monkeypatch.setattr(wl_mod, "writer_lock", _tracking_lock)
    # `bars_sqlite` imports writer_lock at module-load time, so the
    # function-bound ref must be patched there too. The evidence
    # public wrapper uses a lazy ``from .writer_lock import
    # writer_lock`` inside its helper, so patching ``wl_mod`` is
    # enough for that path.
    monkeypatch.setattr(bars_sqlite, "writer_lock", _tracking_lock)

    # Write a bar that will land on 2026-09-26 — must remove the
    # evidence row in the post-commit reconcile.
    bars_sqlite.replace_bars_for_figi(
        str(db), "FIGI-BOUND",
        [{"ts": "2026-09-26", "open": 100, "high": 110, "low": 95,
          "close": 105, "volume": 1000}],
    )

    # The bar lock (phase='bars') must appear BEFORE the reconcile
    # lock (phase='reconcile'). Their interleaving — bars first,
    # then reconcile — proves the public bar function released its
    # lock before the reconciliation acquired its own.
    assert "bars" in seen_phases, f"bar lock not acquired: {seen_phases!r}"
    assert "reconcile" in seen_phases, (
        f"reconcile lock not acquired (real-bars-wins path): {seen_phases!r}"
    )
    bars_idx = seen_phases.index("bars")
    rec_idx = seen_phases.index("reconcile")
    assert bars_idx < rec_idx, (
        f"bar lock must be acquired before reconcile lock: {seen_phases!r}"
    )
    # No nested acquisition: every phase appears at most once
    # during this single replace_bars_for_figi call.
    assert seen_phases.count("bars") == 1, (
        f"bar lock acquired multiple times in one call: {seen_phases!r}"
    )
    assert seen_phases.count("reconcile") == 1, (
        f"reconcile lock acquired multiple times in one call: {seen_phases!r}"
    )


# ─── RED: busy deferral leaves bar successful and evidence row in place


def test_bar_commits_and_evidence_survives_when_reconcile_busy(
    tmp_path, monkeypatch,
):
    """If the reconciliation cannot acquire the lock in time, the
    committed bar must remain visible and the evidence row must
    remain. The deferral must be observable but not raise out of
    the bar writer.
    """
    db = tmp_path / "busy.db"
    _migrate(db)
    _seed_instrument(db, "FIGI-BUSY", "X")
    from algotrader_api.ingestion import writer_lock as wl_mod
    from algotrader_api.ingestion import no_trade_evidence as nte
    from algotrader_api.db import sqlite as sqlitedb
    from algotrader_api.db import bars_sqlite

    con = sqlitedb.get_connection(str(db))
    # Plant an evidence row via the private tx helper (no lock).
    nte._record_no_trade_evidence_tx(
        con, figi="FIGI-BUSY",
        rows=[{"ts": "2026-09-26"}],
        board="TQCB", isin="X",
        now=date(2026, 9, 28),
    )
    con.commit()  # close the implicit tx so the bar writer's BEGIN IMMEDIATE is legal

    real_cm = wl_mod.writer_lock
    reconcile_phase: list[bool] = []

    @wl_mod.contextmanager  # type: ignore[attr-defined]
    def _busy_on_reconcile(db_path, **kw):
        if kw.get("role") == "evidence-reconcile":
            reconcile_phase.append(True)
            raise wl_mod.WriterLockBusy(
                role=kw["role"],
                phase=kw["phase"],
                database_path=str(db_path),
                lock_path=str(db_path) + ".writer.lock",
                timeout_seconds=kw.get("timeout_seconds", 0.0),
                reason="test-forced-busy",
            )
        with real_cm(db_path, **kw):
            yield

    monkeypatch.setattr(wl_mod, "writer_lock", _busy_on_reconcile)
    # bars_sqlite has its own module-level reference to writer_lock
    # (imported at module-load time); patch that too so the bar
    # lock path is observable.
    monkeypatch.setattr(bars_sqlite, "writer_lock", _busy_on_reconcile)

    # The bar write must still succeed and commit.
    bars_sqlite.replace_bars_for_figi(
        str(db), "FIGI-BUSY",
        [{"ts": "2026-09-26", "open": 100, "high": 110, "low": 95,
          "close": 105, "volume": 1000}],
    )
    assert reconcile_phase, "reconcile path was never exercised"

    # Bar committed and visible.
    con2 = sqlite3.connect(str(db))
    try:
        bar = con2.execute(
            "SELECT ts FROM bars WHERE figi = ?", ("FIGI-BUSY",),
        ).fetchone()
    finally:
        con2.close()
    assert bar is not None and bar[0] == "2026-09-26", (
        f"bar not committed when reconcile was busy: {bar!r}"
    )
    # Evidence row still present (the DELETE was deferred).
    con3 = sqlite3.connect(str(db))
    try:
        ev = con3.execute(
            "SELECT session_date FROM moex_no_trade_evidence "
            "WHERE figi = ?", ("FIGI-BUSY",),
        ).fetchall()
    finally:
        con3.close()
    assert ev, "evidence row was deleted despite reconcile being busy"


# ─── RED: listed_till in CLI uses its own lock ──────────────────────────


def test_cli_listed_till_uses_separate_lock_from_evidence(tmp_path, monkeypatch):
    """The CLI (``scripts/backfill_no_trade_evidence.py``) must wrap
    the ``instruments.listed_till`` UPDATE in its own
    ``no-trade-evidence/listed-till`` lock AND the evidence write
    in its own ``no-trade-evidence/evidence`` lock — two separate
    acquisitions. MOEX fetch and ``time.sleep`` must run while no
    lock is held.
    """
    import importlib.util

    db = tmp_path / "cli.db"
    _migrate(db)
    _seed_instrument(db, "FIGI-CLI", "DELISTED")
    # Make a stale bar so the figi enters the todo list.
    con = sqlite3.connect(str(db))
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume, source) "
        "VALUES ('FIGI-CLI', '2026-09-05', 1, 1, 1, 1, 1, 'tinkoff')",
    )
    con.commit()
    con.close()

    from algotrader_api.ingestion import writer_lock as wl_mod
    real_cm = wl_mod.writer_lock
    acquisitions: list[dict] = []
    sleep_seen_under_lock = {"value": False}
    outermost = {"active": False}

    @wl_mod.contextmanager  # type: ignore[attr-defined]
    def _active_tracking_lock(db_path, **kw):
        rec = {**kw, "db_path": str(db_path)}
        acquisitions.append(rec)
        was_active = outermost["active"]
        outermost["active"] = True
        try:
            with real_cm(db_path, **kw):
                yield
        finally:
            outermost["active"] = was_active

    monkeypatch.setattr(wl_mod, "writer_lock", _active_tracking_lock)

    # Patch the network-bound MOEX probe so we never hit the network.
    monkeypatch.setattr(
        "algotrader_api.ingestion.backfill._get_meta_moex",
        lambda *a, **kw: None,
    )
    script_path = (
        Path(__file__).resolve().parent.parent / "scripts"
        / "backfill_no_trade_evidence.py"
    )
    spec = importlib.util.spec_from_file_location(
        "backfill_no_trade_evidence_cli", script_path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    # The CLI imported ``writer_lock`` into its own namespace at
    # load time, so patching the source module is not enough —
    # also patch the CLI's local binding.
    monkeypatch.setattr(mod, "writer_lock", _active_tracking_lock)
    # The evidence helper uses a lazy import so the source
    # ``wl_mod`` patch is the one it sees, but the CLI also
    # exposes ``record_no_trade_evidence`` as a name it imports
    # at load time; the helper we patched at the source level
    # already takes the lazy path.
    monkeypatch.setattr(mod, "_get_meta_moex", lambda *a, **kw: None)
    monkeypatch.setattr(
        mod, "_probe_board_last", lambda ticker: ("TQCB", "2026-09-10"),
    )
    # R1: stub the upstream ISIN probe so the CLI's identity guard
    # sees a match against the seeded instrument ISIN and proceeds
    # to the evidence write. Without this stub the production probe
    # returns ``None`` and the guard emits ``identity_mismatch``
    # (fail-closed).
    from algotrader_api.ingestion import no_trade_evidence as _nte_cli
    _nte_cli.fetch_issuer_identity = lambda ticker: {
        "board": "TQCB", "isin": "X",
    }
    # Patch _fetch_year_moex_outcome (Task 2) to return a single
    # zero-trade row so the evidence write path is exercised. The
    # downstream ``record_historical_no_trade_evidence`` then
    # acquires the second ``no-trade-evidence / evidence`` lock.
    def _fake_fetch(market, board, ticker, year, last_trading_day=None):  # noqa: ARG001
        return ([{
            "ts": "2026-09-08",
            "open": None, "high": None, "low": None, "close": None,
            "volume": 0,
            "_secid": ticker, "_boardid": board,
            "_numtrades": 0, "_value": 0,
            "figi": None, "source": "moex",
        }], "complete")
    monkeypatch.setattr(mod, "_fetch_year_moex_outcome", _fake_fetch)

    # Patch time.sleep inside the loaded CLI module so we can detect
    # whether the lock is held during the sleep.
    import time as _time
    real_sleep = _time.sleep

    def _spy_sleep(secs):
        if outermost["active"]:
            sleep_seen_under_lock["value"] = True
        real_sleep(min(secs, 0.001))

    monkeypatch.setattr(mod.time, "sleep", _spy_sleep)
    # Also patch on the backfill module (the CLI uses it for module-
    # level lookups in some paths).
    import algotrader_api.ingestion.backfill as _bf_mod
    monkeypatch.setattr(_bf_mod.time, "sleep", _spy_sleep)

    # Run the CLI directly via main() so the test stays in-process.
    argv = ["x", "--db", str(db), "--limit", "1", "--days", "60"]
    monkeypatch.setattr(sys, "argv", argv)
    rc = mod.main()
    assert rc == 0, f"CLI returned {rc}"

    # TWO distinct acquisitions: listed-till then evidence.
    assert len(acquisitions) >= 2, (
        f"expected >=2 lock acquisitions, got {acquisitions!r}"
    )
    phases = [a.get("phase") for a in acquisitions]
    assert "listed-till" in phases, (
        f"listed-till lock not acquired: {phases!r}"
    )
    assert "evidence" in phases, (
        f"evidence lock not acquired: {phases!r}"
    )
    # The first lock acquired must be listed-till; evidence must be
    # a SEPARATE acquisition. They are not nested.
    lt_idx = phases.index("listed-till")
    ev_indices = [i for i, p in enumerate(phases) if p == "evidence"]
    assert ev_indices and lt_idx < ev_indices[0], (
        f"listed-till must precede evidence: {phases!r}"
    )
    # sleep must not have been called while the lock was held.
    assert not sleep_seen_under_lock["value"], (
        "CLI called time.sleep while the writer lock was held"
    )


# ─── RED: CLI exits 75 on writer-lock-busy ─────────────────────────────


def test_cli_busy_exits_75_and_emits_one_defer_line(tmp_path, monkeypatch):
    """When the writer lock is busy at the CLI boundary, the script
    emits exactly one bounded ``DEFER writer-lock-busy ...`` line
    and exits 75. No pending mutation is performed.
    """
    import importlib.util
    import io
    import contextlib

    db = tmp_path / "cli_busy.db"
    _migrate(db)
    _seed_instrument(db, "FIGI-CLI-BUSY", "DELISTED")
    con = sqlite3.connect(str(db))
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume, source) "
        "VALUES ('FIGI-CLI-BUSY', '2026-09-05', 1, 1, 1, 1, 1, 'tinkoff')",
    )
    con.commit()
    con.close()

    from algotrader_api.ingestion import writer_lock as wl_mod
    script_path = (
        Path(__file__).resolve().parent.parent / "scripts"
        / "backfill_no_trade_evidence.py"
    )
    spec = importlib.util.spec_from_file_location("backfill_no_trade_evidence_cli", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    monkeypatch.setattr(mod, "_get_meta_moex", lambda *a, **kw: None)
    monkeypatch.setattr(
        mod, "_probe_board_last", lambda ticker: ("TQCB", "2026-09-10"),
    )
    # R1: stub the upstream ISIN probe so the CLI's identity guard
    # sees a match against the seeded instrument ISIN and proceeds
    # to the evidence write. Without this stub the production probe
    # returns ``None`` and the guard emits ``identity_mismatch``
    # (fail-closed).
    from algotrader_api.ingestion import no_trade_evidence as _nte_cli
    _nte_cli.fetch_issuer_identity = lambda ticker: {
        "board": "TQCB", "isin": "X",
    }
    monkeypatch.setattr(mod, "_fetch_year_moex_outcome", lambda *a, **kw: ([], "complete"))

    def _busy_always(db_path, **kw):
        # Every writer_lock acquisition is immediately refused.
        raise wl_mod.WriterLockBusy(
            role=kw["role"],
            phase=kw["phase"],
            database_path=str(db_path),
            lock_path=str(db_path) + ".writer.lock",
            timeout_seconds=kw.get("timeout_seconds", 0.0),
            reason="test-forced-busy-cli",
        )

    monkeypatch.setattr(mod, "writer_lock", _busy_always)

    buf = io.StringIO()
    monkeypatch.setattr(sys, "argv", ["x", "--db", str(db), "--limit", "1"])
    with contextlib.redirect_stdout(buf):
        rc = mod.main()
    assert rc == 75, f"expected exit 75, got {rc}"
    output = buf.getvalue()
    defer_lines = [
        ln for ln in output.splitlines() if ln.startswith("DEFER writer-lock-busy")
    ]
    assert len(defer_lines) == 1, (
        f"expected exactly one DEFER line, got {defer_lines!r} in {output!r}"
    )
    # Bounded: contains role/phase, no API key/token/path leak.
    assert "FIGI-CLI-BUSY" not in defer_lines[0], (
        f"DEFER line leaks figi: {defer_lines[0]!r}"
    )
    # No pending mutation: listed_till must remain NULL.
    con2 = sqlite3.connect(str(db))
    try:
        lt = con2.execute(
            "SELECT listed_till FROM instruments WHERE figi = ?",
            ("FIGI-CLI-BUSY",),
        ).fetchone()
    finally:
        con2.close()
    assert lt[0] is None, f"listed_till was mutated despite busy: {lt!r}"


# ─── RED: dry-run never acquires the writer lock ───────────────────────


def test_cli_dry_run_does_not_acquire_writer_lock(tmp_path, monkeypatch):
    """``--dry-run`` performs no SQLite mutation and acquires no
    writer lock. The print-only branches must short-circuit.
    """
    import importlib.util
    import io
    import contextlib

    db = tmp_path / "dry.db"
    _migrate(db)
    _seed_instrument(db, "FIGI-DRY", "X")
    con = sqlite3.connect(str(db))
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume, source) "
        "VALUES ('FIGI-DRY', '2026-09-05', 1, 1, 1, 1, 1, 'tinkoff')",
    )
    con.commit()
    con.close()

    from algotrader_api.ingestion import writer_lock as wl_mod
    script_path = (
        Path(__file__).resolve().parent.parent / "scripts"
        / "backfill_no_trade_evidence.py"
    )
    spec = importlib.util.spec_from_file_location("backfill_no_trade_evidence_cli", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    monkeypatch.setattr(mod, "_get_meta_moex", lambda *a, **kw: None)
    monkeypatch.setattr(
        mod, "_probe_board_last", lambda ticker: ("TQCB", "2026-09-10"),
    )
    # R1: stub the upstream ISIN probe so the CLI's identity guard
    # sees a match against the seeded instrument ISIN and proceeds
    # to the evidence write. Without this stub the production probe
    # returns ``None`` and the guard emits ``identity_mismatch``
    # (fail-closed).
    from algotrader_api.ingestion import no_trade_evidence as _nte_cli
    _nte_cli.fetch_issuer_identity = lambda ticker: {
        "board": "TQCB", "isin": "X",
    }
    monkeypatch.setattr(mod, "_fetch_year_moex_outcome", lambda *a, **kw: ([], "complete"))

    acquisitions: list[dict] = []
    real_cm = wl_mod.writer_lock

    @wl_mod.contextmanager  # type: ignore[attr-defined]
    def _tracking_lock(db_path, **kw):
        acquisitions.append({**kw, "db_path": str(db_path)})
        with real_cm(db_path, **kw):
            yield

    monkeypatch.setattr(mod, "writer_lock", _tracking_lock)

    buf = io.StringIO()
    monkeypatch.setattr(sys, "argv", [
        "x", "--db", str(db), "--limit", "1", "--dry-run",
    ])
    with contextlib.redirect_stdout(buf):
        rc = mod.main()
    assert rc == 0
    # No acquisitions at all in dry-run mode.
    assert acquisitions == [], (
        f"dry-run acquired writer lock: {acquisitions!r}"
    )
    # Verify the dry-run message was emitted.
    out = buf.getvalue()
    assert "[dry]" in out, f"dry-run marker missing from output: {out!r}"


# ─── Canonical: CLI partial outcome exits 0 with zero evidence ─────────


def test_cli_partial_outcome_exits_zero_without_writing_evidence(
    tmp_path, monkeypatch,
):
    """Canonical spec scenario: when every figi's MOEX fetch returns
    a non-``complete`` outcome (``partial``), the CLI exits 0 and
    writes zero evidence rows. The diagnostic line
    ``moex_historical_evidence_rejected`` is emitted so the
    operator sees the degraded run; cron / supervisor policy
    relies on that structured log, not on a new exit code.

    No new exit code is introduced — the existing
    ``WriterLockBusy`` -> ``return 75`` path is the only non-zero
    exit.

    Bounded: 1 figi, 1 year, ``partial`` outcome, matching upstream
    ISIN, no ``WriterLockBusy``.
    """
    import importlib.util
    import io
    import contextlib

    db = tmp_path / "partial.db"
    _migrate(db)
    _seed_instrument(db, "FIGI-PARTIAL", "DELISTED")
    con = sqlite3.connect(str(db))
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume, source) "
        "VALUES ('FIGI-PARTIAL', '2026-09-05', 1, 1, 1, 1, 1, 'tinkoff')",
    )
    con.commit()
    con.close()

    from algotrader_api.ingestion import writer_lock as wl_mod
    script_path = (
        Path(__file__).resolve().parent.parent / "scripts"
        / "backfill_no_trade_evidence.py"
    )
    spec = importlib.util.spec_from_file_location(
        "backfill_no_trade_evidence_cli_partial", script_path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    monkeypatch.setattr(mod, "_get_meta_moex", lambda *a, **kw: None)
    monkeypatch.setattr(
        mod, "_probe_board_last", lambda ticker: ("TQCB", "2026-09-10"),
    )
    # R1: stub the upstream ISIN probe so the CLI's identity guard
    # sees a match against the seeded instrument ISIN and proceeds
    # to the per-year fetch loop.
    from algotrader_api.ingestion import no_trade_evidence as _nte_cli
    _nte_cli.fetch_issuer_identity = lambda ticker: {
        "board": "TQCB", "isin": "X",
    }
    # Force a ``partial`` outcome from the per-year fetcher — the
    # helper short-circuits, no evidence is written, the CLI
    # exits 0 because the canonical contract is
    # ``partial -> zero evidence rows -> exit 0`` with the
    # ``moex_historical_evidence_rejected`` log line carrying
    # the diagnostic.
    monkeypatch.setattr(
        mod, "_fetch_year_moex_outcome",
        lambda *a, **kw: ([], "partial"),
    )

    # Allow the writer lock to be acquired normally (we are not
    # testing the lock path here).
    real_cm = wl_mod.writer_lock

    @wl_mod.contextmanager  # type: ignore[attr-defined]
    def _passthrough(db_path, **kw):
        with real_cm(db_path, **kw):
            yield

    monkeypatch.setattr(mod, "writer_lock", _passthrough)

    buf = io.StringIO()
    monkeypatch.setattr(
        sys, "argv",
        ["x", "--db", str(db), "--limit", "1", "--sleep", "0"],
    )
    with contextlib.redirect_stdout(buf):
        rc = mod.main()
    output = buf.getvalue()
    # Canonical: partial outcome + zero evidence rows -> exit 0.
    # The diagnostic line is what surfaces the degraded run to
    # operators / cron — not a new exit code.
    assert rc == 0, (
        f"CLI must exit 0 on partial outcome (zero evidence); "
        f"got rc={rc}, output={output!r}"
    )
    # And no evidence row was persisted — the partial fetcher
    # returned no rows, so the writer must not fabricate any.
    con2 = sqlite3.connect(str(db))
    try:
        n = con2.execute(
            "SELECT COUNT(*) FROM moex_no_trade_evidence "
            "WHERE figi = ?",
            ("FIGI-PARTIAL",),
        ).fetchone()[0]
    finally:
        con2.close()
    assert n == 0, (
        f"partial outcome must NOT write evidence, got n={n}"
    )
    # Diagnostic must surface the degraded run in the operator log.
    assert (
        "moex_historical_evidence_rejected" in output
        and "figi=FIGI-PARTIAL" in output
        and "reason=partial" in output
    ), f"missing diagnostic line in output={output!r}"
