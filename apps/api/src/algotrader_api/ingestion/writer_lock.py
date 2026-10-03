"""Shared writer lock primitive for in-scope market-data mutations.

The lock serializes critical sections across processes that mutate the
same file-backed SQLite database. It does NOT hold across network I/O,
calculations, sleeps, or process lifetimes — that's a per-call concern,
audited statically in ``tests/test_writer_inventory.py``.

Public surface:

* :func:`writer_lock_path` — derive the lock file path.
* :func:`writer_lock` — context manager that acquires the lock or times
  out with :class:`WriterLockBusy`.
* :func:`assert_process_creation_allowed` — guard helper that raises
  inside the critical section. Production critical sections call this
  before any future ``os.fork``/``subprocess``/``multiprocessing`` use.
* :class:`WriterLockError`, :class:`WriterLockBusy`,
  :class:`WriterLockReentrant`.
* :data:`WriterRole`, :data:`WriterPhase` — Literal aliases; the
  underlying frozensets ``VALID_ROLES / VALID_PHASES`` are the runtime
  gate.
"""
from __future__ import annotations

import fcntl
import math
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Final, Literal

# ---------------------------------------------------------------------------
# Roles and phases
# ---------------------------------------------------------------------------

WriterRole = Literal[
    "bar-writer",
    "foreign-bars",
    "same-day",
    "no-trade-evidence",
    "expected-bars",
    "evidence-reconcile",
]

WriterPhase = Literal[
    "bars",
    "listed-till",
    "evidence",
    "expected-bars",
    "reconcile",
]

VALID_ROLES: Final[frozenset[str]] = frozenset({
    "bar-writer",
    "foreign-bars",
    "same-day",
    "no-trade-evidence",
    "expected-bars",
    "evidence-reconcile",
})

VALID_PHASES: Final[frozenset[str]] = frozenset({
    "bars",
    "listed-till",
    "evidence",
    "expected-bars",
    "reconcile",
})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WriterLockError(Exception):
    """Base class for all writer-lock failures."""


class WriterLockBusy(WriterLockError):
    """Raised when ``flock(LOCK_EX|LOCK_NB)`` times out.

    Carries bounded safe metadata for the CLI to render. Field lengths
    are capped and control characters are sanitized so a malformed
    caller cannot smuggle secrets or terminal escapes into the
    diagnostic stream.
    """

    _MAX_FIELD = 200

    def __init__(
        self,
        *,
        role: str,
        phase: str,
        database_path: str,
        lock_path: str,
        timeout_seconds: float,
        reason: str,
        result: str = "deferred",
    ):
        self.role = _sanitize(role)
        self.phase = _sanitize(phase)
        self.database_path = _sanitize(database_path)
        self.lock_path = _sanitize(lock_path)
        self.timeout_seconds = float(timeout_seconds)
        self.reason = _sanitize(reason)
        self.result = _sanitize(result)
        self.pid = os.getpid()
        super().__init__(
            f"writer lock busy: role={self.role} phase={self.phase} "
            f"reason={self.reason}"
        )


class WriterLockReentrant(WriterLockError):
    """Raised when the same Python process tries to nest the lock on
    the same canonical path. Process-local guard only — kernel flock
    would also catch this if the process held the fd open, but the
    explicit guard gives a clear error before we hit the kernel.
    """


# ---------------------------------------------------------------------------
# Path derivation
# ---------------------------------------------------------------------------


def writer_lock_path(db_path: str | Path) -> Path:
    """Derive ``<db_path>.writer.lock`` from a database path.

    Accepts strings and ``Path``-likes so call sites don't have to wrap.

    The returned path is the canonical (symlink-resolved, ``..``-
    collapsed) form of the DB path with ``.writer.lock`` appended, so
    two callers that hold different aliases of the same file
    (e.g. ``link.db`` and the real ``real.db`` it points at) share one
    kernel lock file. Path-resolution failures are raised as
    :class:`WriterLockError` so the public contract stays narrow.
    """
    try:
        canonical = Path(db_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise WriterLockError(
            f"cannot resolve writer lock db path {db_path!r}: {exc}"
        ) from exc
    return Path(f"{canonical}.writer.lock")


# ---------------------------------------------------------------------------
# Process-local guard (re-entrancy + no-process-creation audit)
# ---------------------------------------------------------------------------


# Process-local set of canonical lock paths currently held.
_active_paths: set[str] = set()
_active_paths_mutex = threading.Lock()


def _validate_basename(lock_path: Path) -> None:
    """Reject overlong lock basenames before opening the file.

    Uses ``os.pathconf`` when available, otherwise the Linux NAME_MAX
    of 255 bytes. The body never runs if validation fails.
    """
    name = lock_path.name
    try:
        name_max = os.pathconf(lock_path.parent or ".", "PC_NAME_MAX")
    except (OSError, ValueError):
        name_max = 255
    if len(name.encode("utf-8")) > name_max:
        raise WriterLockError(
            f"writer lock basename exceeds NAME_MAX ({name_max}): "
            f"{name!r}"
        )


def _validate_timeout(timeout_seconds: object) -> None:
    """Fail-closed: reject any non-finite, non-numeric, or negative
    timeout before opening the lock file.

    ``bool`` is a subclass of ``int`` in Python; reject it explicitly so
    a caller passing ``True`` doesn't silently mean ``timeout=1``.
    Strings, ``None``, NaN, +/-inf are all rejected. Negative values
    are also rejected so the bounded-retry semantics stay bounded.
    """
    if isinstance(timeout_seconds, bool):
        raise WriterLockError(
            f"timeout_seconds must be a non-negative finite number, "
            f"got bool {timeout_seconds!r}"
        )
    if not isinstance(timeout_seconds, (int, float)):
        raise WriterLockError(
            f"timeout_seconds must be a non-negative finite number, "
            f"got {type(timeout_seconds).__name__} {timeout_seconds!r}"
        )
    value = float(timeout_seconds)
    if math.isnan(value) or math.isinf(value):
        raise WriterLockError(
            f"timeout_seconds must be a non-negative finite number, "
            f"got {timeout_seconds!r}"
        )
    if value < 0:
        raise WriterLockError(
            f"timeout_seconds must be non-negative, got {timeout_seconds!r}"
        )


def assert_process_creation_allowed() -> None:
    """Raise ``WriterLockError`` if the current process holds any
    writer-lock path.

    Production critical sections call this helper *before* any future
    ``os.fork`` / ``subprocess`` / ``multiprocessing`` use. It does
    not monkeypatch those primitives; it is a static guard that
    surfaces the contract violation.
    """
    with _active_paths_mutex:
        held = sorted(_active_paths)
    if held:
        raise WriterLockError(
            "process creation forbidden while writer lock is held: "
            + ", ".join(held)
        )


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


DEFAULT_TIMEOUT_SECONDS: Final[float] = 30.0


@contextmanager
def writer_lock(
    db_path: str | Path,
    *,
    role: WriterRole,
    phase: WriterPhase,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
):
    """Acquire ``<db>.writer.lock`` with ``flock(LOCK_EX|LOCK_NB)``.

    Bounded retry using ``time.monotonic()`` until ``timeout_seconds``
    elapses. Raises :class:`WriterLockBusy` on timeout, with safe
    metadata. Raises :class:`WriterLockError` on bad role/phase,
    overlong basename, or pre-existing symlink. Raises
    :class:`WriterLockReentrant` on same-process nesting.

    The lock is released and the fd is closed in ``finally``. The
    process-local guard is cleared before re-raising so the caller can
    continue without leaking state.
    """
    if role not in VALID_ROLES:
        raise WriterLockError(f"unknown writer role: {role!r}")
    if phase not in VALID_PHASES:
        raise WriterLockError(f"unknown writer phase: {phase!r}")
    _validate_timeout(timeout_seconds)

    # ``writer_lock_path`` already canonicalizes the DB so symlinked
    # aliases share one lock file. We derive both diagnostic paths
    # from the same canonical lock path here, and open that path
    # directly so ``flock`` runs on the real inode.
    lock_path = writer_lock_path(db_path)
    _validate_basename(lock_path)
    canonical_db = str(Path(db_path).expanduser().resolve(strict=False))
    canonical_lock = str(lock_path)

    with _active_paths_mutex:
        if canonical_lock in _active_paths:
            raise WriterLockReentrant(
                f"writer_lock reentrancy on {canonical_lock} (db={canonical_db})"
            )
        _active_paths.add(canonical_lock)

    fd: int | None = None
    acquired = False
    deadline = time.monotonic() + timeout_seconds

    try:
        # O_NOFOLLOW protects against a pre-existing symlink at the
        # lock path: ``os.open`` raises FileExistsError on Linux when
        # O_CREAT|O_NOFOLLOW targets a symlink. Translation happens in
        # the except clause.
        try:
            fd = os.open(
                str(lock_path),
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                0o600,
            )
        except FileExistsError as exc:
            raise WriterLockError(
                f"writer lock path exists and is not a regular file: "
                f"{lock_path}"
            ) from exc
        except OSError as exc:
            # Treat ENAMETOOLONG (and any overlong-name OS error) as
            # a safe fail-closed WriterLockError too.
            raise WriterLockError(
                f"cannot open writer lock path {lock_path}: {exc}"
            ) from exc

        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    # EAGAIN/EWOULDBLOCK on Linux.
                    if exc.errno not in (11, 35):  # EAGAIN, EWOULDBLOCK
                        raise
                    if time.monotonic() >= deadline:
                        raise WriterLockBusy(
                            role=role,
                            phase=phase,
                            database_path=canonical_db,
                            lock_path=canonical_lock,
                            timeout_seconds=timeout_seconds,
                            reason="flock-timeout",
                            result="deferred",
                        ) from exc
                    time.sleep(0.01)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            fd = None
            raise

        try:
            yield
        finally:
            if acquired:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(fd)
            except OSError:
                pass
            fd = None
    finally:
        with _active_paths_mutex:
            _active_paths.discard(canonical_lock)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize(value: str) -> str:
    """Strip control characters and cap length for diagnostic fields."""
    if not isinstance(value, str):
        value = str(value)
    # Replace control chars (including <ESC>) with '?'. We deliberately
    # do not raise on them — sanitization keeps the diagnostic stream
    # safe.
    cleaned = "".join("?" if (ord(c) < 0x20 or ord(c) == 0x7F) else c for c in value)
    return cleaned[: WriterLockBusy._MAX_FIELD]
