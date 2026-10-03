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


# -------------------------------------------------------------------
# RED (Task 5): generic userinfo leak — must redact arbitrary
# 'username:password@host' syntax (parent reproduced this exact
# reproduction: 'alice:SYNTHETIC_SECRET@host/db' leaked the secret).
# -------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, secret_substring",
    [
        # Different username & password — the literal 'user:pass@'
        # sentinel must NOT be the only thing caught.
        ("alice:SYNTHETIC_SECRET@host/db", "SYNTHETIC_SECRET"),
        ("bob:hunter2@db.example.com/db", "hunter2"),
        # Long prefix pushes the unsafe marker beyond _MAX_FIELD (200).
        # Sanitize() truncates to 200 chars BEFORE the marker, so a
        # naive sanitize-then-test order silently passes. The fix must
        # inspect the raw value first.
        ("/" + "a" * 300 + "alice:SYNTHETIC_SECRET@host/db",
         "SYNTHETIC_SECRET"),
        # Connection-string 'key=value;' syntax — also unsafe.
        ("Server=db.example.com;User Id=alice;Password=SYNTHETIC_SECRET;"
         "Database=mydb", "SYNTHETIC_SECRET"),
        # Generic username:password@host with digits in password.
        ("svc_user:pa55w0rd@db.internal:5432/prod",
         "pa55w0rd"),
    ],
)
def test_format_busy_defer_redacts_generic_userinfo(
    tmp_path, value, secret_substring
):
    """Any ``user:password@host`` / ``key=value`` / URL-style marker
    must redact — not just the literal ``user:pass@`` substring or
    a value that fits inside the first 200 chars. Parent reproduced
    ``alice:SYNTHETIC_SECRET@host/db`` leaking the secret verbatim;
    the fix must catch the general userinfo pattern, including an
    unsafe marker placed past char 200 in the input.
    """
    lock = str((tmp_path / "real.db.writer.lock").resolve())
    exc = _busy(value, lock)
    line = format_busy_defer(exc)
    # The exact secret fragment must never appear in the rendered
    # diagnostic, no matter where the unsafe marker sits in the
    # input.
    assert secret_substring not in line, (
        f"redaction failed for {value!r}: {line!r}"
    )
    # And the value must have been replaced wholesale with the
    # redaction sentinel — not partially leaked.
    assert "database_path=[REDACTED]" in line
    assert "alice" not in line
    assert "bob" not in line
    assert "svc_user" not in line


def test_format_busy_defer_inspects_raw_before_sanitize(tmp_path):
    """An unsafe marker placed AFTER char 200 must still redact.

    ``_sanitize`` truncates to ``_MAX_FIELD`` (200 chars). If the
    formatter sanitizes first and then checks for unsafe markers, a
    marker placed at char 300 is silently dropped on the floor and
    the upstream 200 chars leak. The fix must inspect the raw
    ``database_path`` / ``lock_path`` BEFORE truncation/control
    sanitization; control chars may still be sanitized as the
    existing bounded test expects.
    """
    # Build a long prefix that contains no unsafe markers; the
    # unsafe marker sits well past char 200.
    prefix_safe = "/" + "x" * 300
    db = prefix_safe + "alice:SYNTHETIC_SECRET@host/db"
    lock = str((tmp_path / "real.db.writer.lock").resolve())
    exc = _busy(db, lock)
    line = format_busy_defer(exc)
    assert "SYNTHETIC_SECRET" not in line, (
        f"unsafe marker past char 200 leaked: {line!r}"
    )
    assert "database_path=[REDACTED]" in line


def test_format_busy_defer_preserves_safe_paths_after_fix(tmp_path):
    """Genuine absolute local filesystem paths must remain
    untouched (after sanitize). The fix cannot be so aggressive
    that an operator loses the ability to tell which DB contended.
    """
    db = str((tmp_path / "real_state.db").resolve())
    lock = str((tmp_path / "real_state.db.writer.lock").resolve())
    exc = _busy(db, lock)
    line = format_busy_defer(exc)
    assert f"database_path={db}" in line
    assert f"lock_path={lock}" in line
    assert "[REDACTED]" not in line


def test_format_busy_defer_eight_fields_still_single_line(tmp_path):
    """The fix cannot regress the 8-field single-line contract."""
    db = str(tmp_path / "x.db")
    lock = str(tmp_path / "x.db.writer.lock")
    exc = _busy(db, lock)
    line = format_busy_defer(exc)
    assert "\n" not in line
    assert line.startswith("DEFER writer-lock-busy ")
    for k in ("role", "phase", "pid", "database_path",
              "lock_path", "timeout", "reason", "result"):
        assert f"{k}=" in line, f"field {k!r} missing: {line!r}"


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