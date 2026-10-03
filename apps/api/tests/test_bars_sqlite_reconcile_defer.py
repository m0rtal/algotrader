"""Tests for the bar-write post-commit reconciliation deferral.

Spec rules exercised here (writer-coordination/spec.md):

* Bounded Coordination Diagnostics — every deferral renders the
  eight bounded fields.
* The post-commit ``reconcile_no_trade_evidence`` hook in
  :mod:`algotrader_api.db.bars_sqlite` MUST report its own busy
  state instead of swallowing the exception silently. Both
  ``replace_bars_for_figi`` and ``replace_bars_for_figi_with_rowcount``
  route through the same post-commit path.

The bar write itself is committed BEFORE the reconciliation hook
runs, so a busy reconcile never rolls back the bar insert — the
diagnostic is the only required output, and the caller sees a
successful return value.
"""
from __future__ import annotations

import io
import sqlite3
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

_API_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_API_SRC) not in sys.path:
    sys.path.insert(0, str(_API_SRC))

from algotrader_api.db.bars_sqlite import (  # noqa: E402
    replace_bars_for_figi,
    replace_bars_for_figi_with_rowcount,
)
from algotrader_api.ingestion import no_trade_evidence as nte_mod  # noqa: E402
from algotrader_api.ingestion import writer_lock as wl_mod  # noqa: E402


def _busy(db_path, *, role="evidence-reconcile", phase="reconcile"):
    """Force the post-commit reconciliation hook to raise busy."""
    raise wl_mod.WriterLockBusy(
        role=role,
        phase=phase,
        database_path=str(db_path),
        lock_path=str(db_path) + ".writer.lock",
        timeout_seconds=0.5,
        reason="flock-timeout",
        result="deferred",
    )


def _seed_instrument(db_path, figi):
    """Insert a minimal instruments row so the writer lock namespace
    and the reconciliation hook can find the figi.
    """
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT OR IGNORE INTO instruments "
            "(figi, ticker, class, expected_bars) "
            "VALUES (?, ?, 'share', 0)",
            (figi, figi),
        )
        con.commit()
    finally:
        con.close()


def _candles(figi):
    return [{
        "ts": "2026-09-01",
        "open": 100.0, "high": 110.0, "low": 95.0,
        "close": 105.0, "volume": 1000,
        "figi": figi,
    }]


def test_replace_bars_emits_defer_on_postcommit_reconcile_busy(
    fresh_db, monkeypatch, capsys,
):
    """A busy reconcile after a successful bar write must surface
    a single bounded DEFER line — never silently pass.
    """
    figi = "RFB-FIGI-1"
    _seed_instrument(fresh_db, figi)
    monkeypatch.setattr(nte_mod, "reconcile_no_trade_evidence",
                        lambda conn, *, db_path: _busy(db_path))

    # The bar write itself must succeed (the reconcile ran AFTER
    # commit). The busy exception is logged, not raised.
    written = replace_bars_for_figi(fresh_db, figi, _candles(figi))
    assert written == 1

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    defer_lines = [ln for ln in combined.splitlines()
                   if ln.startswith("DEFER writer-lock-busy")]
    assert len(defer_lines) == 1, (
        f"expected exactly one DEFER line, got {defer_lines!r} "
        f"in {combined!r}"
    )
    line = defer_lines[0]
    # All 8 spec fields.
    for k in ("role=", "phase=", "pid=", "database_path=",
              "lock_path=", "timeout=", "reason=", "result="):
        assert k in line, f"field {k!r} missing in {line!r}"
    assert "role=evidence-reconcile" in line
    assert "phase=reconcile" in line
    assert "result=deferred" in line


def test_replace_bars_with_rowcount_emits_defer_on_reconcile_busy(
    fresh_db, monkeypatch, capsys,
):
    """The ``_with_rowcount`` variant must emit the same DEFER
    line — both public bar-write entrypoints share the
    post-commit reconciliation hook.
    """
    figi = "RFC-FIGI-1"
    _seed_instrument(fresh_db, figi)
    monkeypatch.setattr(nte_mod, "reconcile_no_trade_evidence",
                        lambda conn, *, db_path: _busy(db_path))

    written = replace_bars_for_figi_with_rowcount(
        fresh_db, figi, _candles(figi),
    )
    assert written == 1

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    defer_lines = [ln for ln in combined.splitlines()
                   if ln.startswith("DEFER writer-lock-busy")]
    assert len(defer_lines) == 1, (
        f"expected exactly one DEFER line in rowcount variant, "
        f"got {defer_lines!r} in {combined!r}"
    )
    assert "role=evidence-reconcile" in defer_lines[0]


def test_replace_bars_no_defer_on_normal_reconcile(
    fresh_db, monkeypatch, capsys,
):
    """A normal reconcile that returns 0 must NOT emit a DEFER
    line. The diagnostic is contention-only — a quiet hook is
    the common path and must stay quiet.
    """
    figi = "OK-FIGI-1"
    _seed_instrument(fresh_db, figi)

    def _quiet(conn, *, db_path):
        return 0

    monkeypatch.setattr(nte_mod, "reconcile_no_trade_evidence", _quiet)
    replace_bars_for_figi(fresh_db, figi, _candles(figi))

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "DEFER writer-lock-busy" not in combined, (
        f"unexpected DEFER line on quiet path: {combined!r}"
    )


def test_replace_bars_no_defer_when_reconcile_raises_other_error(
    fresh_db, monkeypatch, capsys,
):
    """A non-busy error from reconcile is still swallowed silently
    (the existing contract: snapshot/hook failures must not roll
    back the bar write). The DEFER diagnostic is exclusive to
    ``WriterLockBusy``.
    """
    figi = "ERR-FIGI-1"
    _seed_instrument(fresh_db, figi)

    def _boom(conn, *, db_path):
        raise RuntimeError("upstream flake")

    monkeypatch.setattr(nte_mod, "reconcile_no_trade_evidence", _boom)
    # Bar write still succeeds.
    written = replace_bars_for_figi(fresh_db, figi, _candles(figi))
    assert written == 1

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "DEFER writer-lock-busy" not in combined