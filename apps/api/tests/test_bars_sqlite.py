"""Tests for the `bars` SQLite table: write-path helpers and read path."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest

# Make src/ importable when pytest runs from the repo root.
_API_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_API_SRC) not in sys.path:
    sys.path.insert(0, str(_API_SRC))

from algotrader_api.db.bars_sqlite import (  # noqa: E402
    LOCK_TIMEOUT_SECONDS,
    replace_bars_for_figi,
    resolve_figi_for_ticker,
)


@pytest.fixture
def bars_db(fresh_db):
    """`fresh_db` already ran every migration including 005_bars_table."""
    return fresh_db


# ─── write path ────────────────────────────────────────────────────────


def test_replace_bars_for_figi_inserts_new_rows(bars_db):
    """A fresh figi with N candles results in N rows in the bars table."""
    candles = [
        {"ts": "2026-09-01", "open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 1000},
        {"ts": "2026-09-02", "open": 105.0, "high": 112.0, "low": 100.0, "close": 110.0, "volume": 1100},
    ]
    written = replace_bars_for_figi(bars_db, "FIGI-1", candles)
    assert written == 2

    con = sqlite3.connect(bars_db)
    rows = con.execute(
        "SELECT ts, open, high, low, close, volume FROM bars WHERE figi = ? ORDER BY ts",
        ("FIGI-1",),
    ).fetchall()
    assert len(rows) == 2
    assert rows[0] == ("2026-09-01", 100.0, 110.0, 95.0, 105.0, 1000)
    assert rows[1] == ("2026-09-02", 105.0, 112.0, 100.0, 110.0, 1100)
    con.close()


def test_replace_bars_for_figi_replaces_existing_rows(bars_db):
    """Calling replace twice for the same figi keeps the latest candles only."""
    old = [{"ts": "2026-09-01", "open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 1000}]
    new = [{"ts": "2026-09-05", "open": 200.0, "high": 220.0, "low": 195.0, "close": 215.0, "volume": 2000}]

    replace_bars_for_figi(bars_db, "FIGI-1", old)
    replace_bars_for_figi(bars_db, "FIGI-1", new)

    con = sqlite3.connect(bars_db)
    rows = con.execute(
        "SELECT ts FROM bars WHERE figi = ? ORDER BY ts",
        ("FIGI-1",),
    ).fetchall()
    assert rows == [("2026-09-05",)]
    con.close()


def test_replace_bars_for_figi_accepts_date_objects(bars_db):
    """`ts` may come in as a `datetime.date` instead of an ISO string."""
    candles = [
        {"ts": date(2026, 9, 1), "open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 1000},
    ]
    replace_bars_for_figi(bars_db, "FIGI-1", candles)

    con = sqlite3.connect(bars_db)
    row = con.execute("SELECT ts FROM bars WHERE figi = ?", ("FIGI-1",)).fetchone()
    assert row == ("2026-09-01",)
    con.close()


def test_replace_bars_for_figi_empty_list_noop(bars_db):
    """An empty candle list leaves the bars table untouched for that figi."""
    con = sqlite3.connect(bars_db)
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES ('FIGI-1', '2026-09-01', 100, 110, 95, 105, 1000)"
    )
    con.commit()
    con.close()

    written = replace_bars_for_figi(bars_db, "FIGI-1", [])
    assert written == 0

    con = sqlite3.connect(bars_db)
    rows = con.execute("SELECT COUNT(*) FROM bars WHERE figi = ?", ("FIGI-1",)).fetchone()
    assert rows[0] == 1
    con.close()


def test_replace_bars_for_figi_skips_rows_with_none_ohlc(bars_db):
    """Rows with None open/high/low/close are skipped (MOEX occasionally returns
    trading sessions with no price data for illiquid instruments). The chain must
    not abort — drop the bad row and continue with the rest.
    """
    candles = [
        # valid row
        {"ts": "2026-09-01", "open": 100, "high": 110, "low": 95, "close": 105, "volume": 1000},
        # row with None close — must be skipped
        {"ts": "2026-09-02", "open": 100, "high": 110, "low": 95, "close": None, "volume": 0},
        # valid row
        {"ts": "2026-09-03", "open": 101, "high": 112, "low": 96, "close": 106, "volume": 800},
    ]
    written = replace_bars_for_figi(bars_db, "FIGI-1", candles, replace=False)
    assert written == 2, f"expected 2 rows inserted, got {written}"

    con = sqlite3.connect(bars_db)
    rows = con.execute(
        "SELECT ts FROM bars WHERE figi = ? ORDER BY ts", ("FIGI-1",)
    ).fetchall()
    con.close()
    assert [r[0] for r in rows] == ["2026-09-01", "2026-09-03"]


# ─── read path ────────────────────────────────────────────────────────


def test_resolve_figi_for_ticker_finds_by_ticker(bars_db):
    """`symbol` parameter is a ticker, not a figi."""
    con = sqlite3.connect(bars_db)
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("SBER", "FIGI-SBER", "share", "Sber", "rub", 10),
    )
    con.commit()
    con.close()

    assert resolve_figi_for_ticker(bars_db, "SBER") == "FIGI-SBER"


def test_resolve_figi_for_ticker_falls_back_to_figi_match(bars_db):
    """`symbol` may be passed as the figi directly (no figi-resolution step)."""
    con = sqlite3.connect(bars_db)
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("SBER", "FIGI-SBER", "share", "Sber", "rub", 10),
    )
    con.commit()
    con.close()

    assert resolve_figi_for_ticker(bars_db, "FIGI-SBER") == "FIGI-SBER"


def test_replace_bars_for_figi_rolls_back_on_error(bars_db):
    """If the transaction raises (e.g. instrument_metadata missing), the
    bars rows from this call must not leak into the table."""
    import sqlite3 as _sqlite

    # Drop the instrument_metadata row that replace_bars_for_figi wants
    # to UPDATE. This raises `no such column: ...` because the
    # subquery references an empty result — no, actually the columns
    # are present in the schema but the row's UPDATE matches nothing.
    # We need a real schema failure: drop the instrument_metadata
    # table to provoke an UPDATE failure mid-transaction.
    con = _sqlite.connect(bars_db)
    con.execute("DROP TABLE instrument_metadata")
    con.commit()
    con.close()

    candles = [
        {"ts": "2026-09-01", "open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 1000},
    ]
    with pytest.raises(_sqlite.OperationalError):
        replace_bars_for_figi(bars_db, "FIGI-1", candles)

    # After rollback, the bars row should not be persisted.
    con = _sqlite.connect(bars_db)
    cnt = con.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    con.close()
    assert cnt == 0, "rollback failed: bars row leaked after UPDATE error"


def test_list_bars_returns_ordered_dicts(bars_db):
    """list_bars returns one dict per row in ts-ascending order."""
    from algotrader_api.db.bars_sqlite import list_bars

    replace_bars_for_figi(
        bars_db,
        "FIGI-1",
        [
            {"ts": "2026-09-02", "open": 105.0, "high": 112.0, "low": 100.0, "close": 110.0, "volume": 1100},
            {"ts": "2026-09-01", "open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 1000},
        ],
    )
    rows = list_bars(bars_db, "FIGI-1")
    assert [r["ts"] for r in rows] == ["2026-09-01", "2026-09-02"]


def test_count_bars_returns_total_rows(bars_db):
    """count_bars sums every figi's row count."""
    from algotrader_api.db.bars_sqlite import count_bars

    replace_bars_for_figi(
        bars_db,
        "FIGI-1",
        [{"ts": "2026-09-01", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 1}],
    )
    replace_bars_for_figi(
        bars_db,
        "FIGI-2",
        [
            {"ts": "2026-09-01", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 1},
            {"ts": "2026-09-02", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 1},
        ],
    )
    assert count_bars(bars_db) == 3


def test_replace_bars_for_figi_accepts_candle_with_year_month_day_attrs(bars_db):
    """Candles without a `ts` field but with `time.year/month/day` attrs
    extract a date from those attrs (raw SDK output shape)."""
    from types import SimpleNamespace

    closed = SimpleNamespace(
        time=SimpleNamespace(year=2026, month=9, day=1),
        open=SimpleNamespace(units=100, nano=0),
        high=SimpleNamespace(units=110, nano=0),
        low=SimpleNamespace(units=95, nano=0),
        close=SimpleNamespace(units=105, nano=0),
        volume=1000,
    )
    replace_bars_for_figi(bars_db, "FIGI-NATIVE", [closed])

    con = sqlite3.connect(bars_db)
    row = con.execute(
        "SELECT ts FROM bars WHERE figi = ?", ("FIGI-NATIVE",)
    ).fetchone()
    con.close()
    assert row == ("2026-09-01",)


def test_replace_bars_for_figi_accepts_nested_time_dict(bars_db):
    """Raw gRPC responses serialise as dicts with a nested `time` dict."""
    closed = {
        "time": {"year": 2026, "month": 9, "day": 1},
        "open": {"units": 100, "nano": 0},
        "high": {"units": 110, "nano": 0},
        "low": {"units": 95, "nano": 0},
        "close": {"units": 105, "nano": 0},
        "volume": 1000,
    }
    replace_bars_for_figi(bars_db, "FIGI-NESTED", [closed])

    con = sqlite3.connect(bars_db)
    row = con.execute(
        "SELECT open, high, low, close FROM bars WHERE figi = ?",
        ("FIGI-NESTED",),
    ).fetchone()
    con.close()
    assert row == (100.0, 110.0, 95.0, 105.0)


def test_replace_bars_for_figi_writes_source_column(bars_db):
    """Explicit source override — must be written into bars.source.

    The function used to rely on the column default for `source`,
    which made attribution fragile. Callers must be able to pass an
    explicit source string and have it persisted.
    """
    candles = [
        {"ts": "2026-09-01", "open": 100, "high": 110, "low": 95, "close": 105, "volume": 1000},
    ]
    replace_bars_for_figi(bars_db, "FIGI-1", candles, replace=False, source="custom")

    con = sqlite3.connect(bars_db)
    row = con.execute("SELECT source FROM bars WHERE figi=?", ("FIGI-1",)).fetchone()
    con.close()
    assert row[0] == "custom"


def test_replace_bars_for_figi_raises_on_unparseable_ts(bars_db):
    """A candle with neither `ts` nor `time.year/month/day` raises so the
    runner can fall back to a warn-log and skip the figi."""
    with pytest.raises(ValueError):
        replace_bars_for_figi(bars_db, "FIGI-BAD", [{"open": 1, "high": 2, "low": 1, "close": 2, "volume": 1}])


def test_resolve_figi_for_ticker_unknown_returns_none(bars_db):
    assert resolve_figi_for_ticker(bars_db, "ZZZZ") is None


# ─── writer-coordination: shared market-data lock ───────────────────
#
# Step 1 / Step 2 of the SDD brief: the public
# ``replace_bars_for_figi`` must own ``<db>.writer.lock`` for the bar
# write section only. The contention tests below drive the lock
# primitive across processes so we exercise the kernel-visible
# serialization (not just the process-local guard). Tests rely on
# ``bars_sqlite.LOCK_TIMEOUT_SECONDS`` — a module-level monkeypatchable
# constant that the public function passes to ``writer_lock``. The
# brief forbids relying on a mutable default argument for the
# timeout, so the constant is the only knob.


def test_lock_timeout_seconds_constant_is_module_level():
    """The public function's lock timeout is a module attribute that
    tests can monkeypatch. The brief explicitly forbids relying on
    a mutable default argument.
    """
    from algotrader_api.db import bars_sqlite

    assert hasattr(bars_sqlite, "LOCK_TIMEOUT_SECONDS")
    assert isinstance(bars_sqlite.LOCK_TIMEOUT_SECONDS, (int, float))
    assert bars_sqlite.LOCK_TIMEOUT_SECONDS > 0


def _wait_for_holder_ready(sentinel: Path, *, timeout: float = 5.0) -> None:
    """Wait for a holder subprocess to write its inside-the-context
    sentinel so the loser process can be sure the lock is held.

    Mirrors the helper in test_writer_lock.py — the lock file
    appears *before* ``flock`` returns, so a kernel-visible file
    alone races the holder's entry into the critical section.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sentinel.exists():
            return
        time.sleep(0.01)
    pytest.fail(
        f"holder never wrote ready sentinel {sentinel} within {timeout}s"
    )


def _holder_script(
    db: str, ready: str, hold_seconds: float = 1.0,
) -> str:
    return (
        "import time\n"
        "from pathlib import Path\n"
        "from algotrader_api.ingestion.writer_lock import writer_lock\n"
        f"ready = Path({ready!r})\n"
        f"db = {db!r}\n"
        f"with writer_lock(Path(db), role=\"bar-writer\", phase=\"bars\",\n"
        "                  timeout_seconds=5.0):\n"
        "    ready.write_text(\"ready\")\n"
        f"    time.sleep({hold_seconds})\n"
    )


def test_replace_bars_for_figi_writer_lock_busy_noop(bars_db, tmp_path, monkeypatch):
    """If another process holds the writer lock, ``replace_bars_for_figi``
    must fail closed: raise ``WriterLockBusy``, write zero rows, and
    leave ``instrument_metadata`` untouched.
    """
    from algotrader_api.ingestion.writer_lock import WriterLockBusy
    from algotrader_api.db import bars_sqlite

    # Tight timeout so the test runs in <1s even when contention fires.
    monkeypatch.setattr(bars_sqlite, "LOCK_TIMEOUT_SECONDS", 0.1)

    ready = tmp_path / "holder_ready"
    holder = subprocess.Popen(
        [sys.executable, "-c", _holder_script(bars_db, str(ready))],
        env={
            "PATH": os.environ["PATH"],
            "PYTHONPATH": str(_API_SRC),
            "PYTHONHOME": "",
        },
    )
    _wait_for_holder_ready(ready)
    try:
        candles = [
            {"ts": "2026-09-01", "open": 100.0, "high": 110.0, "low": 95.0,
             "close": 105.0, "volume": 1000},
        ]
        with pytest.raises(WriterLockBusy):
            replace_bars_for_figi(bars_db, "FIGI-LOCK", candles)
    finally:
        holder.wait(timeout=5.0)

    # Zero new rows in bars; no instrument_metadata row created.
    con = sqlite3.connect(bars_db)
    try:
        bar_count = con.execute(
            "SELECT COUNT(*) FROM bars WHERE figi = ?", ("FIGI-LOCK",),
        ).fetchone()[0]
        meta_row = con.execute(
            "SELECT total_bars, first_bar_ts, last_bar_ts "
            "FROM instrument_metadata WHERE figi = ?",
            ("FIGI-LOCK",),
        ).fetchone()
    finally:
        con.close()
    assert bar_count == 0, (
        "lock-busy call leaked bars; lock did not protect the mutation"
    )
    assert meta_row is None, (
        "lock-busy call created an instrument_metadata row; "
        "metadata UPDATE must not run when the lock is unavailable"
    )


def test_replace_bars_for_figi_rollback_releases_lock(bars_db, tmp_path, monkeypatch):
    """A SQL failure inside the locked transaction must roll back the
    bar rows AND release the writer lock so the next process can
    acquire it.
    """
    from algotrader_api.db import bars_sqlite

    # Force a SQL failure by removing ``instrument_metadata`` so the
    # UPDATE inside the transaction raises ``OperationalError``.
    con = sqlite3.connect(bars_db)
    con.execute("DROP TABLE instrument_metadata")
    con.commit()
    con.close()

    candles = [
        {"ts": "2026-09-01", "open": 100.0, "high": 110.0, "low": 95.0,
         "close": 105.0, "volume": 1000},
    ]
    with pytest.raises(sqlite3.OperationalError):
        replace_bars_for_figi(bars_db, "FIGI-RB", candles)

    # The transaction must have rolled back: zero rows for FIGI-RB.
    con = sqlite3.connect(bars_db)
    try:
        bar_count = con.execute(
            "SELECT COUNT(*) FROM bars WHERE figi = ?", ("FIGI-RB",),
        ).fetchone()[0]
    finally:
        con.close()
    assert bar_count == 0, "transaction did not roll back on error"

    # A subsequent in-process acquisition must succeed — the lock
    # was released in the finally clause of the writer_lock body.
    from algotrader_api.ingestion.writer_lock import writer_lock

    with writer_lock(bars_db, role="bar-writer", phase="bars",
                      timeout_seconds=0.5):
        pass


def test_replace_bars_for_figi_normalize_happens_before_lock(bars_db, monkeypatch):
    """Candle normalization (``_row``) must run BEFORE the lock is
    acquired. The brief is explicit: only the BEGIN IMMEDIATE..COMMIT
    window is inside the critical section; the per-candle
    ``_row()`` work happens up front, with no kernel lock held.
    """
    from algotrader_api.db import bars_sqlite

    # Track when ``_row`` runs vs when the lock body opens. We use
    # two sentinels: ``_row_seen`` is set by a wrapper around the
    # module-level ``_row``; ``inside_lock_seen`` is set inside a
    # monkeypatched ``writer_lock`` whose body always records the
    # moment of entry.
    row_seen: list[float] = []
    inside_lock: list[float] = []

    real_row = bars_sqlite._row

    def _row_tracking(c):
        row_seen.append(time.monotonic())
        return real_row(c)

    monkeypatch.setattr(bars_sqlite, "_row", _row_tracking)

    from algotrader_api.ingestion import writer_lock as wl_mod

    real_lock = wl_mod.writer_lock

    from contextlib import contextmanager

    @contextmanager
    def _tracking_lock(*args, **kwargs):
        inside_lock.append(time.monotonic())
        with real_lock(*args, **kwargs):
            yield

    monkeypatch.setattr(bars_sqlite, "writer_lock", _tracking_lock)
    # ``replace_bars_for_figi`` may import ``writer_lock`` lazily;
    # patch the symbol in the writer_lock module too so an internal
    # ``from .writer_lock import writer_lock`` picks it up.
    monkeypatch.setattr(wl_mod, "writer_lock", _tracking_lock)

    candles = [
        {"ts": "2026-09-01", "open": 100.0, "high": 110.0, "low": 95.0,
         "close": 105.0, "volume": 1000},
        {"ts": "2026-09-02", "open": 101.0, "high": 111.0, "low": 96.0,
         "close": 106.0, "volume": 1100},
    ]
    replace_bars_for_figi(bars_db, "FIGI-NORM", candles)

    assert row_seen, "_row was never called"
    assert inside_lock, "writer_lock was never entered"
    # Normalization must finish before the lock body starts. We
    # don't care about the *first* row vs lock entry (loop and lock
    # interleaving is implementation detail), only that the last
    # normalization call precedes the lock entry.
    assert max(row_seen) < min(inside_lock), (
        f"normalization must complete before the lock is acquired: "
        f"row_seen={row_seen!r} inside_lock={inside_lock!r}"
    )


def test_replace_bars_for_figi_maybe_refresh_runs_after_lock_release(
    bars_db, tmp_path, monkeypatch,
):
    """After ``replace_bars_for_figi`` returns, the writer lock is
    released. A second process can acquire it immediately.
    """
    from algotrader_api.db import bars_sqlite

    # Tight timeout so a leaked lock surfaces as a WriterLockBusy
    # rather than a long hang.
    monkeypatch.setattr(bars_sqlite, "LOCK_TIMEOUT_SECONDS", 0.1)

    candles = [
        {"ts": "2026-09-01", "open": 100.0, "high": 110.0, "low": 95.0,
         "close": 105.0, "volume": 1000},
    ]
    replace_bars_for_figi(bars_db, "FIGI-RELEASE", candles)

    # Subprocess acquires the lock with a 0.5s timeout. If
    # ``replace_bars_for_figi`` left the lock held (e.g. maybe_refresh
    # ran inside the critical section), the subprocess would
    # WriterLockBusy and fail.
    from algotrader_api.ingestion.writer_lock import writer_lock
    ready = tmp_path / "post_acquired"
    script = (
        "import time\n"
        "from pathlib import Path\n"
        "from algotrader_api.ingestion.writer_lock import writer_lock\n"
        f"db = {bars_db!r}\n"
        f"ready = Path({str(ready)!r})\n"
        "with writer_lock(Path(db), role=\"bar-writer\", phase=\"bars\",\n"
        "                  timeout_seconds=0.5):\n"
        "    ready.write_text(\"acquired\")\n"
        "    time.sleep(0.05)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env={
            "PATH": os.environ["PATH"],
            "PYTHONPATH": str(_API_SRC),
            "PYTHONHOME": "",
        },
        capture_output=True,
        text=True,
        timeout=5.0,
    )
    assert proc.returncode == 0, (
        f"post-call lock acquisition failed: rc={proc.returncode} "
        f"stderr={proc.stderr!r}"
    )
    assert ready.exists(), "post-call lock acquisition did not write sentinel"


def test_replace_bars_for_figi_reconcile_runs_outside_bar_lock(
    bars_db, tmp_path, monkeypatch,
):
    """The reconciliation hook (``reconcile_no_trade_evidence``)
    must run OUTSIDE the bar lock. We prove this by monkeypatching
    the hook to immediately fork a subprocess that attempts to
    acquire ``<db>.writer.lock``; if the bar lock is still held
    when the hook runs, the subprocess raises ``WriterLockBusy``;
    if the bar lock was released, the subprocess acquires it and
    writes a sentinel. The presence of the sentinel after the
    public function returns is the proof that the bar lock was
    released before the reconciliation hook ran.

    The test does NOT implement Task 3 evidence locking — it only
    pins the lock boundary.
    """
    from algotrader_api.db import bars_sqlite
    from algotrader_api.ingestion import no_trade_evidence as nte

    # Tight timeout so the test fails fast if the boundary
    # regresses (the bar lock is leaked → the subprocess times
    # out → sentinel missing → assert below fires).
    monkeypatch.setattr(bars_sqlite, "LOCK_TIMEOUT_SECONDS", 0.1)

    # Sentinel the subprocess writes when it acquires the lock.
    sentinel = tmp_path / "reconcile_era_lock_acquired"
    db = bars_db
    script = (
        "import time\n"
        "from pathlib import Path\n"
        "from algotrader_api.ingestion.writer_lock import writer_lock\n"
        f"db = {db!r}\n"
        f"sentinel = Path({str(sentinel)!r})\n"
        "with writer_lock(Path(db), role=\"bar-writer\", phase=\"bars\",\n"
        "                  timeout_seconds=2.0):\n"
        "    sentinel.write_text(\"acquired\")\n"
        "    time.sleep(0.05)\n"
    )

    def _fake_reconcile(_conn):
        # 1. Lock acquisition check: run a subprocess that must
        #    acquire the bar lock. If the bar lock is still held
        #    (regression), the subprocess raises ``WriterLockBusy``
        #    and the sentinel is never written.
        proc = subprocess.run(
            [sys.executable, "-c", script],
            env={
                "PATH": os.environ["PATH"],
                "PYTHONPATH": str(_API_SRC),
                "PYTHONHOME": "",
            },
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"subprocess could not acquire the bar lock while "
                f"reconcile ran — boundary regressed. "
                f"rc={proc.returncode} stderr={proc.stderr!r}"
            )
        # 2. Visibility check: the just-committed bar row AND the
        #    instrument_metadata aggregate must be visible to a
        #    fresh separate connection at hook entry. This proves
        #    the bar write COMMITTED before reconcile ran (not
        #    just that the lock was released). SQLite's WAL means
        #    a fresh connection on the same file sees the latest
        #    committed snapshot, so a new sqlite3.connect()
        #    immediately reads the committed row.
        viewer = sqlite3.connect(bars_db)
        try:
            row = viewer.execute(
                "SELECT ts FROM bars WHERE figi = ?",
                ("FIGI-RECONCILE",),
            ).fetchone()
            if row is None or row[0] != "2026-09-01":
                raise AssertionError(
                    f"newly committed bar row not visible to a "
                    f"separate connection at reconcile hook entry: "
                    f"got {row!r} (expected ('2026-09-01',))"
                )
            meta = viewer.execute(
                "SELECT total_bars, first_bar_ts, last_bar_ts "
                "FROM instrument_metadata WHERE figi = ?",
                ("FIGI-RECONCILE",),
            ).fetchone()
            if meta is None or meta[0] != 1:
                raise AssertionError(
                    f"instrument_metadata aggregate not visible to "
                    f"a separate connection at reconcile hook entry: "
                    f"got {meta!r}"
                )
        finally:
            viewer.close()
        return 0

    # The public function imports ``reconcile_no_trade_evidence``
    # lazily inside its body via
    # ``from ..ingestion.no_trade_evidence import reconcile_no_trade_evidence``.
    # That lazy import resolves ``reconcile_no_trade_evidence`` to
    # whatever ``algotrader_api.ingestion.no_trade_evidence`` exports
    # at call time, so patching the symbol on the source module is
    # what the public function sees.
    monkeypatch.setattr(nte, "reconcile_no_trade_evidence", _fake_reconcile)

    candles = [
        {"ts": "2026-09-01", "open": 100.0, "high": 110.0, "low": 95.0,
         "close": 105.0, "volume": 1000},
    ]
    replace_bars_for_figi(bars_db, "FIGI-RECONCILE", candles)

    # The bar write committed (the public function returned
    # without raising). Now the reconciliation hook must have
    # already executed; verify the subprocess got the lock.
    assert sentinel.exists(), (
        "reconciliation hook ran while the bar lock was still "
        "held — the lock boundary regressed. Expected the "
        "subprocess to acquire the lock while reconcile runs."
    )


def test_replace_bars_for_figi_metadata_aggregate_updates_atomically(bars_db):
    """Step 3: when the public function commits, bar rows AND the
    instrument_metadata aggregate (total_bars / first_bar_ts /
    last_bar_ts) must commit together. A SQL failure mid-transaction
    must roll back both — proven via the existing rollback test
    and a fresh aggregate check.
    """
    # Pre-seed the metadata row so the ``UPDATE ... WHERE figi = ?``
    # in the public function has a row to update. The first commit
    # bumps total_bars/first_bar_ts/last_bar_ts atomically with the
    # bar rows.
    con = sqlite3.connect(bars_db)
    con.execute(
        "INSERT INTO instrument_metadata (figi) VALUES (?)",
        ("FIGI-AGG",),
    )
    con.commit()
    con.close()

    candles_v1 = [
        {"ts": "2026-09-01", "open": 100.0, "high": 110.0, "low": 95.0,
         "close": 105.0, "volume": 1000},
    ]
    replace_bars_for_figi(bars_db, "FIGI-AGG", candles_v1)
    con = sqlite3.connect(bars_db)
    try:
        meta_before = con.execute(
            "SELECT total_bars, first_bar_ts, last_bar_ts "
            "FROM instrument_metadata WHERE figi = ?",
            ("FIGI-AGG",),
        ).fetchone()
    finally:
        con.close()
    assert meta_before == (1, "2026-09-01", "2026-09-01"), (
        f"first write did not produce correct aggregate: {meta_before!r}"
    )

    # Drop metadata to force a SQL failure on the next call; verify
    # the first write's aggregate is still consistent (no partial
    # overwrite from a leaked transaction).
    con = sqlite3.connect(bars_db)
    con.execute("DROP TABLE instrument_metadata")
    con.commit()
    con.close()
    with pytest.raises(sqlite3.OperationalError):
        replace_bars_for_figi(
            bars_db, "FIGI-AGG",
            [
                {"ts": "2026-09-02", "open": 100.0, "high": 110.0,
                 "low": 95.0, "close": 105.0, "volume": 1000},
            ],
        )
    # Bars from the failing call must NOT be visible.
    con = sqlite3.connect(bars_db)
    try:
        cnt = con.execute(
            "SELECT COUNT(*) FROM bars WHERE figi = ?", ("FIGI-AGG",),
        ).fetchone()[0]
    finally:
        con.close()
    assert cnt == 1, (
        f"second write leaked bars: {cnt} rows for FIGI-AGG (expected 1)"
    )


# ─── writer-coordination: foreign-FIGI fail-closed (Task 2 correction) ──
#
# The SDD brief requires the bar writer to reject any candle that
# names a figi different from the loop's figi argument. Otherwise
# the raw backfill path could silently relabel another instrument's
# data — the same class of bug as the T/DIOD/ROST cross-pollution.


def test_replace_bars_for_figi_rejects_candles_naming_other_figi(bars_db):
    """A candle whose figi attribute differs from the loop figi must
    be rejected fail-closed. The writer must NEVER relabel another
    instrument's data by stamping the loop figi on a candle that
    carries a different figi.
    """
    candles = [
        # Loop figi is FIGI-LOOP but this candle names FIGI-OTHER.
        {"figi": "FIGI-OTHER", "ts": "2026-09-01",
         "open": 100, "high": 110, "low": 95, "close": 105, "volume": 1000},
    ]
    with pytest.raises(ValueError):
        replace_bars_for_figi(bars_db, "FIGI-LOOP", candles)
    # Zero rows leaked for either figi.
    con = sqlite3.connect(bars_db)
    try:
        loop_count = con.execute(
            "SELECT COUNT(*) FROM bars WHERE figi = ?", ("FIGI-LOOP",),
        ).fetchone()[0]
        other_count = con.execute(
            "SELECT COUNT(*) FROM bars WHERE figi = ?", ("FIGI-OTHER",),
        ).fetchone()[0]
    finally:
        con.close()
    assert loop_count == 0
    assert other_count == 0


def test_replace_bars_for_figi_rejects_native_candle_naming_other_figi(bars_db):
    """A native gRPC-shaped candle whose `figi` attribute disagrees
    with the loop figi must also be rejected. The figi field can
    come either as a dict key (SDK output) or as a dataclass
    attribute (raw gRPC); both must be checked.
    """
    from types import SimpleNamespace
    bad = SimpleNamespace(
        figi="FIGI-OTHER",  # wrong figi
        time=SimpleNamespace(year=2026, month=9, day=1),
        open=SimpleNamespace(units=100, nano=0),
        high=SimpleNamespace(units=110, nano=0),
        low=SimpleNamespace(units=95, nano=0),
        close=SimpleNamespace(units=105, nano=0),
        volume=1000,
    )
    with pytest.raises(ValueError):
        replace_bars_for_figi(bars_db, "FIGI-LOOP", [bad])
    con = sqlite3.connect(bars_db)
    try:
        other_count = con.execute(
            "SELECT COUNT(*) FROM bars WHERE figi = ?", ("FIGI-OTHER",),
        ).fetchone()[0]
    finally:
        con.close()
    assert other_count == 0, (
        "foreign candle leaked into bars; the writer must not "
        "relabel FIGI-OTHER's data with FIGI-LOOP"
    )


def test_replace_bars_for_figi_accepts_candle_with_no_figi_attribute(bars_db):
    """Candles that omit the `figi` field are fine — the loop figi
    is used. The fail-closed check applies only when the candle
    explicitly names a DIFFERENT figi, never when it names the
    loop figi or no figi at all.
    """
    candles = [
        {"ts": "2026-09-01", "open": 100, "high": 110, "low": 95,
         "close": 105, "volume": 1000},
    ]
    written = replace_bars_for_figi(bars_db, "FIGI-OK", candles)
    assert written == 1


def test_replace_bars_for_figi_accepts_full_iso_timestamp(bars_db):
    """A candle with a full ISO timestamp ("2026-09-01T07:00:00Z")
    must be normalized to YYYY-MM-DD before INSERT, just like the
    common writer's _row. The pre-write dedup in
    _async_backfill_impl relies on this normalization to compare
    against the stored YYYY-MM-DD key.
    """
    from datetime import datetime
    candles = [
        # full ISO with time + tz
        {"ts": "2026-09-01T07:00:00+00:00",
         "open": 100, "high": 110, "low": 95, "close": 105, "volume": 1000},
        # datetime object
        {"ts": datetime(2026, 9, 2, 7, 0, 0),
         "open": 100, "high": 110, "low": 95, "close": 105, "volume": 1000},
        # date object
        {"ts": date(2026, 9, 3),
         "open": 100, "high": 110, "low": 95, "close": 105, "volume": 1000},
    ]
    written = replace_bars_for_figi(bars_db, "FIGI-ISO", candles)
    assert written == 3, f"all 3 timestamp shapes must normalize; got {written}"

    con = sqlite3.connect(bars_db)
    try:
        rows = con.execute(
            "SELECT ts FROM bars WHERE figi = ? ORDER BY ts",
            ("FIGI-ISO",),
        ).fetchall()
    finally:
        con.close()
    assert [r[0] for r in rows] == ["2026-09-01", "2026-09-02", "2026-09-03"]


def test_replace_bars_for_figi_with_rowcount_reports_actual_inserts(bars_db):
    """The with-rowcount variant must report the actual
    ``executemany`` cursor rowcount — the same as the public
    function when no races are involved, since all 3 rows are
    fresh.
    """
    from algotrader_api.db.bars_sqlite import replace_bars_for_figi_with_rowcount

    candles = [
        {"ts": "2026-09-01", "open": 100, "high": 110, "low": 95,
         "close": 105, "volume": 1000},
        {"ts": "2026-09-02", "open": 100, "high": 110, "low": 95,
         "close": 105, "volume": 1000},
        {"ts": "2026-09-03", "open": 100, "high": 110, "low": 95,
         "close": 105, "volume": 1000},
    ]
    added = replace_bars_for_figi_with_rowcount(
        bars_db, "FIGI-RC", candles, replace=False, source="tinkoff",
    )
    assert added == 3

    con = sqlite3.connect(bars_db)
    try:
        rows = con.execute(
            "SELECT ts FROM bars WHERE figi = ? ORDER BY ts", ("FIGI-RC",),
        ).fetchall()
    finally:
        con.close()
    assert [r[0] for r in rows] == ["2026-09-01", "2026-09-02", "2026-09-03"]


def test_replace_bars_for_figi_with_rowcount_under_race_reports_zero(bars_db, monkeypatch):
    """Deterministic race test: a concurrent writer inserts the
    same keys AFTER the pre-lock prefilter ran but BEFORE the
    locked mutation. The with-rowcount variant must report
    ``added == 0`` because every row collided with the racer's
    INSERT OR IGNORE (UNIQUE constraint on (figi, ts)).
    """
    from algotrader_api.db.bars_sqlite import replace_bars_for_figi_with_rowcount
    from algotrader_api.db import bars_sqlite
    from algotrader_api.ingestion import writer_lock as wl_mod

    # Patch the private transaction helper so the moment it is
    # invoked — after prefilter, after lock acquisition — the
    # racer inserts the same keys first. The public function
    # would then attempt INSERT OR IGNORE and get rowcount == 0.
    real_tx = bars_sqlite._replace_bars_for_figi_tx

    def _racing_tx(conn, figi, rows, **kw):
        # Pre-existing rows for every key. The locked mutation
        # then attempts INSERT OR IGNORE → all rows ignored →
        # rowcount == 0.
        existing = [
            (figi, r[1], 0, 0, 0, 0, 0, "racer")
            for r in rows
        ]
        cur = conn.executemany(
            "INSERT OR IGNORE INTO bars (figi, ts, open, high, low, close, volume, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            existing,
        )
        conn.commit()
        return real_tx(conn, figi, rows, **kw)

    # Use monkeypatch to ensure the patched helper is restored
    # even if the test fails — otherwise subsequent tests see a
    # global monkey-patched module attribute and silently change
    # the production path.
    monkeypatch.setattr(bars_sqlite, "_replace_bars_for_figi_tx", _racing_tx)

    candles = [
        {"ts": "2026-09-01", "open": 100, "high": 110, "low": 95,
         "close": 105, "volume": 1000},
        {"ts": "2026-09-02", "open": 100, "high": 110, "low": 95,
         "close": 105, "volume": 1000},
    ]
    added = replace_bars_for_figi_with_rowcount(
        bars_db, "FIGI-RACE", candles, replace=False, source="tinkoff",
    )
    assert added == 0, (
        f"with-rowcount variant must report 0 inserts when a "
        f"racer beat the locked mutation; got {added}"
    )
    # And the bars must belong to the racer, not us.
    con = sqlite3.connect(bars_db)
    try:
        rows = con.execute(
            "SELECT source FROM bars WHERE figi = ?", ("FIGI-RACE",),
        ).fetchall()
    finally:
        con.close()
    assert all(r[0] == "racer" for r in rows), (
        f"all rows for FIGI-RACE must belong to the racer; got {rows!r}"
    )
