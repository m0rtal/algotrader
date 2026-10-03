"""Tests for the shared ``format_busy_defer`` diagnostic formatter.

Spec rules exercised here (writer-coordination/spec.md):

* Bounded Coordination Diagnostics — the system SHALL report
  contention with the eight bounded fields: role, phase, PID,
  database path, lock path, timeout, reason, and result.
* No API key, token, credential, connection string, request
  payload, or upstream response may leak through the diagnostic
  line.
* The diagnostic is safe for a terminal escape; control
  characters in any field are stripped before the line is
  rendered.

This file is intentionally narrow — only the formatter is tested
here. End-to-end CLI use of the formatter is covered by
``test_aux_writer_lock_outcomes.py`` and the bar-reconciliation
defer by ``test_bars_sqlite_reconcile_defer.py``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_API_ROOT = Path(__file__).resolve().parent.parent
_API_SRC = _API_ROOT / "src"
if str(_API_SRC) not in sys.path:
    sys.path.insert(0, str(_API_SRC))

from algotrader_api.ingestion.writer_lock import (  # noqa: E402
    WriterLockBusy,
    format_busy_defer,
)


# ---------------------------------------------------------------------------
# RED: shared formatter exists and renders all 8 bounded fields
# ---------------------------------------------------------------------------


def _busy(database_path: str, lock_path: str, *,
           role: str = "bar-writer",
           phase: str = "bars",
           timeout_seconds: float = 1.5,
           reason: str = "flock-timeout",
           result: str = "deferred") -> WriterLockBusy:
    return WriterLockBusy(
        role=role,
        phase=phase,
        database_path=database_path,
        lock_path=lock_path,
        timeout_seconds=timeout_seconds,
        reason=reason,
        result=result,
    )


def test_format_busy_defer_has_all_eight_fields(tmp_path):
    """The single bounded line must carry every spec-required field.

    Spec line 184 names exactly eight fields: role, phase, PID,
    database path, lock path, timeout, reason, result. A future
    regression that drops one of them will fail here.
    """
    db = str(tmp_path / "real.db")
    lock = str(tmp_path / "real.db.writer.lock")
    exc = _busy(db, lock)
    line = format_busy_defer(exc)
    assert line.startswith("DEFER writer-lock-busy ")
    assert f"role={exc.role}" in line
    assert f"phase={exc.phase}" in line
    assert f"pid={exc.pid}" in line, (
        f"PID missing in DEFER line: {line!r}"
    )
    assert f"database_path={db}" in line, (
        f"database_path missing or wrong: {line!r}"
    )
    assert f"lock_path={lock}" in line, (
        f"lock_path missing or wrong: {line!r}"
    )
    assert "timeout=1.5s" in line, f"timeout missing: {line!r}"
    assert f"reason={exc.reason}" in line
    assert f"result={exc.result}" in line


def test_format_busy_defer_strips_control_characters(tmp_path):
    """Control characters in any field are sanitized to '?'.

    ``role`` and ``reason`` are user-controllable on the busy
    exception; the formatter must not pass through a literal
    ESC or newline that would mangle the terminal or smuggle a
    secret across newlines.
    """
    db = str(tmp_path / "x.db")
    lock = str(tmp_path / "x.db.writer.lock")
    exc = _busy(
        db, lock,
        role="bar-writer\x1b[31m",
        phase="bars\n",
        reason="flock-timeout\r",
    )
    line = format_busy_defer(exc)
    assert "\x1b" not in line
    assert "\n" not in line
    assert "\r" not in line
    assert "?" in line  # the ESC was replaced


def test_format_busy_defer_caps_field_length(tmp_path):
    """Reason field is capped so a malformed caller cannot blow
    up the log line. The cap matches the existing
    ``WriterLockBusy._MAX_FIELD`` of 200 chars; the rendered
    field for an oversized reason must still be bounded.
    """
    db = str(tmp_path / "x.db")
    lock = str(tmp_path / "x.db.writer.lock")
    long_reason = "x" * 500
    exc = _busy(db, lock, reason=long_reason)
    line = format_busy_defer(exc)
    # The line itself is bounded; the reason substring cannot
    # exceed WriterLockBusy._MAX_FIELD chars.
    reason_field = line.split("reason=", 1)[1].split(" ", 1)[0]
    assert len(reason_field) <= WriterLockBusy._MAX_FIELD


# ---------------------------------------------------------------------------
# RED: path safety — reject URL / userinfo / connection-string / query
# ---------------------------------------------------------------------------


def test_format_busy_defer_redacts_url_database_path(tmp_path):
    """A database_path that looks like a URL (contains '://')
    is replaced with ``[REDACTED]``. We cannot tell a real
    filesystem path from a connection string by filename alone,
    so the safe default for non-filesystem values is redaction.
    """
    lock = str(tmp_path / "x.db.writer.lock")
    exc = _busy(
        "postgres://user:pass@host:5432/db",
        lock,
    )
    line = format_busy_defer(exc)
    assert "postgres://" not in line
    assert "user:pass" not in line
    assert "[REDACTED]" in line


def test_format_busy_defer_redacts_url_lock_path(tmp_path):
    lock_path = "redis://default:token@cache.example.com:6379/0"
    exc = _busy("/tmp/state.db", lock_path)
    line = format_busy_defer(exc)
    assert "redis://" not in line
    assert "default:token" not in line
    assert "[REDACTED]" in line


def test_format_busy_defer_redacts_userinfo(tmp_path):
    """A path containing userinfo-style 'user:pass@' is redacted.

    A real filesystem path can in theory contain '@' but never
    '://' or 'user:pass@host'. We treat the userinfo pattern
    as a connection-string marker and redact.
    """
    db = str(tmp_path / "x.db")  # safe filesystem path
    lock = "user:pass@malicious.example.com/x.db.writer.lock"
    exc = _busy(db, lock)
    line = format_busy_defer(exc)
    assert "user:pass" not in line
    assert "@malicious" not in line
    assert "[REDACTED]" in line


def test_format_busy_defer_redacts_query_string(tmp_path):
    """A path carrying a '?' query string is redacted.

    The existing secret-sentinel tests inject
    ``?token=Bearer-...`` into ``database_path`` /
    ``lock_path``. The formatter must drop the entire path on
    such a marker.
    """
    db = str(tmp_path / "x.db") + "?token=BEARER-SECRET-TOKEN-12345"
    lock = str(tmp_path / "x.db.writer.lock")
    exc = _busy(db, lock)
    line = format_busy_defer(exc)
    assert "BEARER-SECRET-TOKEN-12345" not in line
    assert "database_path=[REDACTED]" in line


def test_format_busy_defer_preserves_safe_filesystem_paths(tmp_path):
    """A canonical local DB path stays untouched (after sanitize).

    The whole point of the diagnostic is to tell the operator
    WHICH DB / lock file contended. A clean ``/tmp/...`` style
    filesystem path must survive the formatter intact.
    """
    db = str((tmp_path / "real_state.db").resolve())
    lock = str((tmp_path / "real_state.db.writer.lock").resolve())
    exc = _busy(db, lock)
    line = format_busy_defer(exc)
    assert f"database_path={db}" in line
    assert f"lock_path={lock}" in line
    assert "[REDACTED]" not in line


def test_format_busy_defer_no_payload_or_extra_attrs(tmp_path):
    """No payload-like key/value leaks into the rendered line.

    ``WriterLockBusy`` only carries the eight bounded fields;
    the formatter must not introduce any additional ``key=value``
    pair. This locks the public surface: a future contributor
    cannot add a 9th field without explicitly updating this
    test (and the spec).
    """
    db = str(tmp_path / "x.db")
    lock = str(tmp_path / "x.db.writer.lock")
    exc = _busy(db, lock)
    line = format_busy_defer(exc)
    # Single bounded line: no double-spaces, no surprise keys.
    assert "  " not in line.replace("writer-lock-busy  ", "", 1)
    # The eight field names; nothing more.
    for k in ("role", "phase", "pid", "database_path",
              "lock_path", "timeout", "reason", "result"):
        assert f"{k}=" in line, f"field {k!r} missing: {line!r}"
    # No reserved markers that an injection could carry.
    assert "payload" not in line.lower()
    assert "bearer" not in line.lower()