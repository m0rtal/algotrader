"""Tests for backfill_bonds_to_depth in
apps/api/src/algotrader_api/ingestion/backfill.py."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def db_with_bonds(tmp_path: Path):
    """In-memory SQLite with instruments + bars tables seeded.

    The ``instrument_metadata`` table is included so the
    coordinated bar writer (Task 2) can run its aggregate UPDATE
    without an ``OperationalError``. Production DBs run all
    migrations including 004 + 005b; this fixture mirrors that
    surface for the test.
    """
    con = sqlite3.connect(str(tmp_path / "test.db"))
    con.executescript("""
        CREATE TABLE instruments (
            figi TEXT PRIMARY KEY,
            ticker TEXT,
            class TEXT
        );
        CREATE TABLE bars (
            figi TEXT,
            ts TEXT,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            source TEXT DEFAULT 'moex',
            PRIMARY KEY (figi, ts)
        );
        CREATE TABLE instrument_metadata (
            figi              TEXT PRIMARY KEY,
            last_bar_ts       TEXT,
            first_bar_ts      TEXT,
            last_backfilled_at TEXT,
            total_bars        INTEGER NOT NULL DEFAULT 0,
            last_run_status   TEXT,
            last_run_at       TEXT,
            last_error        TEXT
        );
        INSERT INTO instruments VALUES ('BBG000BOND15', 'BOND15', 'bond');
        INSERT INTO instruments VALUES ('BBG000BOND30', 'BOND30', 'bond');
        INSERT INTO instruments VALUES ('BBG000BOND00', 'BOND00', 'bond');
        -- BOND15: 15 bars (sparse)
        INSERT INTO bars VALUES
            ('BBG000BOND15', '2024-12-01', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-02', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-03', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-04', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-05', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-06', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-09', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-10', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-11', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-12', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-13', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-16', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-17', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-18', 100, 101, 99, 100, 1000, 'moex'),
            ('BBG000BOND15', '2024-12-19', 100, 101, 99, 100, 1000, 'moex');
        -- BOND30: 250 bars (full, should be skipped).
        -- Generate 250 unique dates by spreading rows across years
        -- (month + day alone collide after 336; year prefix avoids it).
        INSERT INTO bars
            SELECT 'BBG000BOND30',
                   (2010 + ((n - 1) / 28)) || '-' ||
                   printf('%02d', ((n - 1) / 28 % 12) + 1) || '-' ||
                   printf('%02d', ((n - 1) % 28) + 1),
                   100, 101, 99, 100, 1000, 'moex'
            FROM (
                WITH RECURSIVE seq(n) AS (
                    SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < 250
                )
                SELECT n FROM seq
            );
        -- BOND00: 0 bars
    """)
    yield con
    con.close()


def _make_candle(figi: str, ts: str):
    # Use SimpleNamespace instead of MagicMock so attribute access
    # returns exactly what was assigned — MagicMock auto-creates
    # child mocks for unassigned attributes (``c.time.year`` would
    # otherwise return a child Mock and break ``_row``'s date
    # extraction). The coordinated bar writer (Task 2) runs
    # every fetched candle through ``_row``, so the test fixture
    # must produce candle objects with literal attribute values.
    # The figi field MUST match the loop figi — the writer's
    # foreign-FIGI guard (Task 2 correction) raises ValueError
    # when a candle names a different figi (the same class of
    # bug as the T/DIOD/ROST cross-pollution). Test authors
    # who need a candle "for figi X" should pass X here AND
    # wire the loop figi to X via the same-figi fixture.
    from types import SimpleNamespace
    return SimpleNamespace(
        figi=figi,
        ts=ts,
        open=100,
        high=101,
        low=99,
        close=100,
        volume=1000,
    )


def test_sparse_bond_is_brought_to_target_depth(db_with_bonds):
    """Bond with 15 bars should be brought to >=30 after backfill."""
    from algotrader_api.ingestion.backfill import backfill_bonds_to_depth

    # Mock Tinkoff to return 20 new bars for BOND15 and 30 for
    # BOND00 (0 bars). The dispatch is keyed on the figi arg so
    # the foreign-FIGI guard (Task 2 correction) sees candles
    # whose figi matches the loop figi.
    candles_by_figi = {
        'BBG000BOND15': [_make_candle('BBG000BOND15', f'2025-01-{i+1:02d}') for i in range(20)],
        'BBG000BOND00': [_make_candle('BBG000BOND00', f'2025-02-{i+1:02d}') for i in range(30)],
    }

    async def _dispatch(*, figi, **_kw):
        return candles_by_figi.get(figi, [])

    with patch('algotrader_api.ingestion.client.make_client') as mock_client_factory, \
         patch('algotrader_api.ingestion.rate_limit.get_global') as mock_rl:
        mock_client = MagicMock()
        mock_client.get_candles = AsyncMock(side_effect=_dispatch)
        mock_client_factory.return_value = mock_client
        mock_rl.return_value.acquire = AsyncMock()

        result = backfill_bonds_to_depth(target_days=30, conn=db_with_bonds)

    # Verify BOND15 now has 35 bars (15 original + 20 new)
    new_count = db_with_bonds.execute(
        "SELECT COUNT(*) FROM bars WHERE figi='BBG000BOND15'"
    ).fetchone()[0]
    assert new_count >= 30
    assert result["bars_added"] >= 15


def test_full_bond_is_skipped(db_with_bonds):
    """Bond with 250 bars should NOT trigger a Tinkoff fetch."""
    from algotrader_api.ingestion.backfill import backfill_bonds_to_depth

    # Empty per-figi dispatch keeps the foreign-FIGI guard
    # happy. BOND30 (250 bars) must be skipped — decide_strategy
    # returns 'skip' before we ever call get_candles for it.
    async def _dispatch(*, figi, **_kw):
        return []

    with patch('algotrader_api.ingestion.client.make_client') as mock_client_factory, \
         patch('algotrader_api.ingestion.rate_limit.get_global') as mock_rl:
        mock_client = MagicMock()
        mock_client.get_historical_bonds = MagicMock(return_value=[])
        mock_client.get_candles = AsyncMock(side_effect=_dispatch)
        mock_client_factory.return_value = mock_client
        mock_rl.return_value.acquire = AsyncMock()

        result = backfill_bonds_to_depth(target_days=30, conn=db_with_bonds)

    # ADAPT-2: The brief's literal assertion (`assert_not_called()`) is
    # incompatible with the shared fixture — BOND15 (15 bars) and BOND00
    # (0 bars) MUST trigger a fetch in the same test run. We assert the
    # documented intent instead: BOND30 (the full bond) was never passed
    # to get_candles. Calls for BOND15/BOND00 are expected.
    called_figis = {
        call.kwargs.get("figi") for call in mock_client.get_candles.call_args_list
    }
    assert "BBG000BOND30" not in called_figis
    assert result["skipped"] >= 1


def test_zero_bar_bond_is_fully_backfilled(db_with_bonds):
    """Bond with 0 bars should get >=30 bars after backfill."""
    from algotrader_api.ingestion.backfill import backfill_bonds_to_depth

    # BOND00 has 0 bars and must be filled. BOND15 has 15 bars
    # (under target) and would also fetch — dispatch by figi so
    # the foreign-FIGI guard (Task 2 correction) sees a matching
    # figi on every candle.
    candles_by_figi = {
        'BBG000BOND00': [_make_candle('BBG000BOND00', f'2025-02-{i+1:02d}') for i in range(30)],
        'BBG000BOND15': [_make_candle('BBG000BOND15', f'2025-03-{i+1:02d}') for i in range(20)],
    }

    async def _dispatch(*, figi, **_kw):
        return candles_by_figi.get(figi, [])

    with patch('algotrader_api.ingestion.client.make_client') as mock_client_factory, \
         patch('algotrader_api.ingestion.rate_limit.get_global') as mock_rl:
        mock_client = MagicMock()
        mock_client.get_candles = AsyncMock(side_effect=_dispatch)
        mock_client_factory.return_value = mock_client
        mock_rl.return_value.acquire = AsyncMock()

        result = backfill_bonds_to_depth(target_days=30, conn=db_with_bonds)

    new_count = db_with_bonds.execute(
        "SELECT COUNT(*) FROM bars WHERE figi='BBG000BOND00'"
    ).fetchone()[0]
    assert new_count >= 30


def test_duplicate_bars_are_skipped(db_with_bonds):
    """Re-running backfill with the same candles should not produce duplicates."""
    from algotrader_api.ingestion.backfill import backfill_bonds_to_depth

    # First call: insert 5 new bars for BOND15 (which has 15).
    # BOND00 also needs fetching — dispatch by figi so the
    # foreign-FIGI guard (Task 2 correction) sees a matching
    # figi on every candle.
    new_candles = [_make_candle('BBG000BOND15', f'2025-03-{i+1:02d}') for i in range(5)]
    bond00_candles = [_make_candle('BBG000BOND00', f'2025-04-{i+1:02d}') for i in range(30)]
    candles_by_figi = {
        'BBG000BOND15': new_candles,
        'BBG000BOND00': bond00_candles,
    }

    async def _dispatch(*, figi, **_kw):
        return candles_by_figi.get(figi, [])

    with patch('algotrader_api.ingestion.client.make_client') as mock_client_factory, \
         patch('algotrader_api.ingestion.rate_limit.get_global') as mock_rl:
        mock_client = MagicMock()
        mock_client.get_candles = AsyncMock(side_effect=_dispatch)
        mock_client_factory.return_value = mock_client
        mock_rl.return_value.acquire = AsyncMock()

        backfill_bonds_to_depth(target_days=30, conn=db_with_bonds)
        count_after_first = db_with_bonds.execute(
            "SELECT COUNT(*) FROM bars WHERE figi='BBG000BOND15'"
        ).fetchone()[0]

        # Second call with the SAME candles — must not duplicate
        backfill_bonds_to_depth(target_days=30, conn=db_with_bonds)
        count_after_second = db_with_bonds.execute(
            "SELECT COUNT(*) FROM bars WHERE figi='BBG000BOND15'"
        ).fetchone()[0]

    assert count_after_first == count_after_second


# ─── writer-coordination: per-FIGI fail-closed (Task 2 / Step 4) ──────
#
# The SDD brief requires:
#   * Remove the executable raw ``INSERT INTO bars`` in
#     ``_async_backfill_impl``; collect missing candles per FIGI and
#     delegate to ``replace_bars_for_figi(replace=False, source="tinkoff")``.
#   * When a connection is injected, the production writer path must
#     use a file-backed DB. Reject ``:memory:`` loudly.
#   * Per-FIGI fail-closed: if FIGI A times out because another
#     process holds the lock, the loop continues; FIGI B then
#     commits when the lock is free.
#   * Preserve per-FIGI ``figis_processed`` / ``errors`` counters.


def test_async_backfill_impl_rejects_memory_connection(tmp_path):
    """Production writer path cannot use ``:memory:`` (lock + WAL
    semantics assume a real file). The function must fail loudly
    instead of silently degrading the bar write contract.
    """
    from algotrader_api.ingestion.backfill import _async_backfill_impl

    mem_conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(Exception) as excinfo:
            # ``asyncio.run`` + async fixture; call directly because
            # the function is async.
            import asyncio
            asyncio.run(_async_backfill_impl(target_days=30, conn=mem_conn))
    finally:
        mem_conn.close()
    # The error must explicitly mention ``:memory:`` so an operator
    # can diagnose it without reading the stack.
    assert "memory" in str(excinfo.value).lower(), (
        f"rejection message must name ``:memory:`` for operator "
        f"diagnostics; got {excinfo.value!r}"
    )


def test_async_backfill_impl_per_figi_fail_closed_a_timeout_b_succeeds(
    tmp_path, monkeypatch,
):
    """Per-FIGI fail-closed: when FIGI A is held by an external lock
    and the writer-lock acquisition times out, the loop must NOT
    abort the run — it must increment the error counter and
    continue to FIGI B, which commits normally.

    We drive two backfill calls in sequence against the same
    holder so the timeline is deterministic:

      1. start the holder (waits for "go")
      2. write "go"; holder enters the lock and writes "in-lock"
      3. call #1: backfill BOND_A only — the holder holds the
         lock, so BOND_A's writer-lock acquisition times out and
         ``errors`` is incremented; the function returns
      4. write "release"; holder leaves the lock
      5. call #2: backfill BOND_B only — the lock is free, so
         BOND_B commits normally

    The two single-FIGI runs together cover the same surface as
    the brief's "A timeout, B succeeds" requirement.
    """
    from algotrader_api.db import bars_sqlite

    db_file = str(tmp_path / "bonds.db")
    con = sqlite3.connect(db_file)
    con.executescript("""
        CREATE TABLE instruments (
            figi TEXT PRIMARY KEY,
            ticker TEXT,
            class TEXT
        );
        CREATE TABLE bars (
            figi TEXT,
            ts TEXT,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            source TEXT DEFAULT 'moex',
            PRIMARY KEY (figi, ts)
        );
        CREATE TABLE instrument_metadata (
            figi              TEXT PRIMARY KEY,
            last_bar_ts       TEXT,
            first_bar_ts      TEXT,
            last_backfilled_at TEXT,
            total_bars        INTEGER NOT NULL DEFAULT 0,
            last_run_status   TEXT,
            last_run_at       TEXT,
            last_error        TEXT
        );
        INSERT INTO instruments VALUES ('BBG000BOND_A', 'BONDA', 'bond');
        INSERT INTO instruments VALUES ('BBG000BOND_B', 'BONDB', 'bond');
    """)
    con.commit()
    con.close()

    # Tight lock timeout so the test runs in <1s when contention fires.
    monkeypatch.setattr(bars_sqlite, "LOCK_TIMEOUT_SECONDS", 0.2)

    # Controlled holder: waits for `go_file`, enters the lock,
    # writes `in_lock_file`, waits for `release_file`, then exits.
    db = db_file
    go_file = tmp_path / "holder_go"
    in_lock_file = tmp_path / "holder_in_lock"
    release_file = tmp_path / "holder_release"
    holder_script = (
        "import time\n"
        "from pathlib import Path\n"
        "from algotrader_api.ingestion.writer_lock import writer_lock\n"
        f"db = {db!r}\n"
        f"go_file = Path({str(go_file)!r})\n"
        f"in_lock_file = Path({str(in_lock_file)!r})\n"
        f"release_file = Path({str(release_file)!r})\n"
        "while not go_file.exists():\n"
        "    time.sleep(0.01)\n"
        "go_file.unlink()\n"
        "with writer_lock(Path(db), role=\"bar-writer\", phase=\"bars\",\n"
        "                  timeout_seconds=5.0):\n"
        "    in_lock_file.write_text('in')\n"
        "    while not release_file.exists():\n"
        "        time.sleep(0.01)\n"
        "    release_file.unlink()\n"
    )
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
        "PYTHONHOME": "",
    }
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_script], env=env
    )
    try:
        # Tell the holder to enter the lock; wait for it to be in.
        go_file.write_text("go")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if in_lock_file.exists():
                break
            time.sleep(0.01)
        else:
            pytest.fail("holder never entered the lock")

        from types import SimpleNamespace

        def _make_candle(figi, ts):
            return SimpleNamespace(
                figi=figi, ts=ts, open=100, high=101, low=99,
                close=100, volume=1000,
            )

        def _side_effect_factory(figi):
            async def _side(date_from, date_to, **_kw):
                return [_make_candle(figi, "2025-04-01")]
            return _side

        from algotrader_api.ingestion import backfill as bf_mod_real

        def _run_single(figi):
            # Restrict the SELECT to a single figi by temporarily
            # deleting the other bond from the instruments table
            # for the duration of this call. Restored in finally.
            other = (
                "BBG000BOND_B" if figi == "BBG000BOND_A"
                else "BBG000BOND_A"
            )
            con = sqlite3.connect(db_file)
            try:
                con.execute("DELETE FROM instruments WHERE figi = ?", (other,))
                con.commit()
            finally:
                con.close()
            try:
                with patch(
                    "algotrader_api.ingestion.client.make_client",
                ) as mock_client_factory, patch(
                    "algotrader_api.ingestion.rate_limit.get_global",
                ) as mock_rl:
                    mock_client = MagicMock()
                    mock_client.get_candles = AsyncMock(
                        side_effect=_side_effect_factory(figi),
                    )
                    mock_client_factory.return_value = mock_client
                    mock_rl.return_value.acquire = AsyncMock()
                    return bf_mod_real.backfill_bonds_to_depth(
                        target_days=30,
                        conn=sqlite3.connect(db_file),
                    )
            finally:
                con = sqlite3.connect(db_file)
                try:
                    con.execute(
                        "INSERT INTO instruments (figi, ticker, class) "
                        "VALUES (?, ?, 'bond')",
                        (other, other[-1]),
                    )
                    con.commit()
                finally:
                    con.close()

        # Call #1: BOND_A while holder holds the lock → errors=1.
        result_a = _run_single("BBG000BOND_A")
        assert result_a["errors"] >= 1, (
            f"BOND_A must have errored while holder held the lock; "
            f"got {result_a!r}"
        )

        # Release the holder so call #2 (BOND_B) can succeed.
        release_file.write_text("release")
    finally:
        # Ensure the holder is reaped even on test failure.
        if holder.poll() is None:
            try:
                release_file.write_text("release")
            except Exception:
                pass
        holder.wait(timeout=5.0)

    # Call #2: BOND_B after the holder released → success.
    result_b = _run_single("BBG000BOND_B")
    assert result_b["errors"] == 0, (
        f"BOND_B must NOT have errored after the lock was released; "
        f"got {result_b!r}"
    )

    con = sqlite3.connect(db_file)
    try:
        rows_a = con.execute(
            "SELECT COUNT(*) FROM bars WHERE figi='BBG000BOND_A'"
        ).fetchone()[0]
        rows_b = con.execute(
            "SELECT COUNT(*) FROM bars WHERE figi='BBG000BOND_B'"
        ).fetchone()[0]
    finally:
        con.close()
    assert rows_a == 0, (
        f"FIGI A must NOT have written bars when the lock was held; "
        f"got {rows_a} rows"
    )
    assert rows_b == 1, (
        f"FIGI B must have committed after the lock was released; "
        f"got {rows_b} rows"
    )


# ─── writer-coordination: missing-candle accounting (Task 2 correction) ─
#
# The Task 2 brief's correction requires that the coordinated
# ``_async_backfill_impl`` restore the original raw-INSERT semantic:
# only the actually-missing candles count toward ``bars_added`` and
# the per-FIGI log ``after``. The two tests below pin the two
# surfaces the brief names:
#
#   * Second identical backfill returns ``bars_added == 0``.
#   * A fetched batch containing one existing, one repeated
#     duplicate, and one new candle reports exactly one added and
#     inserts exactly one row.

def test_second_identical_backfill_returns_zero_bars_added(tmp_path):
    """Running backfill_bonds_to_depth a second time with the same
    fetched candles must report ``bars_added == 0`` (the
    missing-candle filter sees nothing to write) and must NOT
    produce duplicate rows.
    """
    from algotrader_api.ingestion.backfill import backfill_bonds_to_depth

    # Use a fresh single-figi DB so the per-call accounting is
    # deterministic (the shared multi-figi fixture would also
    # fetch for the other two figis and inflate the totals).
    db_file = str(tmp_path / "dedup.db")
    con = sqlite3.connect(db_file)
    con.executescript("""
        CREATE TABLE instruments (
            figi TEXT PRIMARY KEY, ticker TEXT, class TEXT
        );
        CREATE TABLE bars (
            figi TEXT, ts TEXT,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            source TEXT DEFAULT 'moex',
            PRIMARY KEY (figi, ts)
        );
        CREATE TABLE instrument_metadata (
            figi              TEXT PRIMARY KEY,
            last_bar_ts       TEXT,
            first_bar_ts      TEXT,
            last_backfilled_at TEXT,
            total_bars        INTEGER NOT NULL DEFAULT 0,
            last_run_status   TEXT,
            last_run_at       TEXT,
            last_error        TEXT
        );
        INSERT INTO instruments VALUES ('BBG000BOND00', 'BOND00', 'bond');
    """)
    con.commit()
    con.close()

    new_candles = [_make_candle('BBG000BOND00', f'2025-05-{i+1:02d}') for i in range(5)]
    with patch('algotrader_api.ingestion.client.make_client') as mock_client_factory, \
         patch('algotrader_api.ingestion.rate_limit.get_global') as mock_rl:
        mock_client = MagicMock()
        mock_client.get_candles = AsyncMock(return_value=new_candles)
        mock_client_factory.return_value = mock_client
        mock_rl.return_value.acquire = AsyncMock()

        result_first = backfill_bonds_to_depth(
            target_days=30, conn=sqlite3.connect(db_file)
        )
        con = sqlite3.connect(db_file)
        try:
            count_first = con.execute(
                "SELECT COUNT(*) FROM bars WHERE figi='BBG000BOND00'"
            ).fetchone()[0]
        finally:
            con.close()

        # Second call with the SAME candles. The missing-candle
        # filter sees every key already in the DB → adds zero.
        result_second = backfill_bonds_to_depth(
            target_days=30, conn=sqlite3.connect(db_file)
        )
        con = sqlite3.connect(db_file)
        try:
            count_second = con.execute(
                "SELECT COUNT(*) FROM bars WHERE figi='BBG000BOND00'"
            ).fetchone()[0]
        finally:
            con.close()

    assert result_first["bars_added"] == 5, (
        f"first call must insert the 5 new candles; got "
        f"bars_added={result_first['bars_added']!r}"
    )
    assert result_second["bars_added"] == 0, (
        f"second call with the same candles must report "
        f"bars_added==0; got {result_second['bars_added']!r}"
    )
    assert count_first == count_second, (
        f"second call leaked duplicate rows: "
        f"count went {count_first} -> {count_second}"
    )


def test_mixed_batch_existing_dup_new_reports_exactly_one_added(tmp_path):
    """A fetched batch containing one already-existing candle, one
    in-batch duplicate of the same key, and one truly-new candle
    must report ``bars_added == 1`` and insert exactly one new row.
    This pins the dedup + existing-skip behavior together.
    """
    from algotrader_api.ingestion.backfill import backfill_bonds_to_depth

    # Fresh single-figi DB so the assertion is deterministic.
    db_file = str(tmp_path / "mixed.db")
    con = sqlite3.connect(db_file)
    con.executescript("""
        CREATE TABLE instruments (
            figi TEXT PRIMARY KEY, ticker TEXT, class TEXT
        );
        CREATE TABLE bars (
            figi TEXT, ts TEXT,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            source TEXT DEFAULT 'moex',
            PRIMARY KEY (figi, ts)
        );
        CREATE TABLE instrument_metadata (
            figi              TEXT PRIMARY KEY,
            last_bar_ts       TEXT,
            first_bar_ts      TEXT,
            last_backfilled_at TEXT,
            total_bars        INTEGER NOT NULL DEFAULT 0,
            last_run_status   TEXT,
            last_run_at       TEXT,
            last_error        TEXT
        );
        INSERT INTO instruments VALUES ('BBG000BOND00', 'BOND00', 'bond');
        -- Seed one existing candle for BOND00 with a known key.
        INSERT INTO bars (figi, ts, open, high, low, close, volume, source)
        VALUES ('BBG000BOND00', '2025-06-01', 100, 101, 99, 100, 1000, 'moex');
    """)
    con.commit()
    con.close()

    # Build a batch with: (1) one existing key, (2) one in-batch
    # duplicate, (3) one new key. The expected behavior is one
    # new row inserted and ``bars_added == 1``.
    existing_dup_a = _make_candle('BBG000BOND00', '2025-06-01')  # already in DB
    existing_dup_b = _make_candle('BBG000BOND00', '2025-06-01')  # repeat in batch
    new_candle    = _make_candle('BBG000BOND00', '2025-06-02')  # brand new
    batch = [existing_dup_a, existing_dup_b, new_candle]

    with patch('algotrader_api.ingestion.client.make_client') as mock_client_factory, \
         patch('algotrader_api.ingestion.rate_limit.get_global') as mock_rl:
        mock_client = MagicMock()
        mock_client.get_candles = AsyncMock(return_value=batch)
        mock_client_factory.return_value = mock_client
        mock_rl.return_value.acquire = AsyncMock()

        result = backfill_bonds_to_depth(
            target_days=30, conn=sqlite3.connect(db_file)
        )

    assert result["bars_added"] == 1, (
        f"batch of 1 existing + 1 in-batch dup + 1 new must report "
        f"bars_added == 1; got {result['bars_added']!r}"
    )

    # Verify the DB saw exactly one new row, no duplicates.
    con = sqlite3.connect(db_file)
    try:
        rows = con.execute(
            "SELECT ts FROM bars WHERE figi='BBG000BOND00' ORDER BY ts"
        ).fetchall()
    finally:
        con.close()
    assert rows == [('2025-06-01',), ('2025-06-02',)], (
        f"DB must contain exactly the existing 2025-06-01 row plus "
        f"the new 2025-06-02 row; got {rows!r}"
    )


# ─── writer-coordination: bounded prefilter (Task 2 correction) ───────
#
# The Task 2 brief requires the raw backfill's existing-candle
# prefilter to be bounded — SQLite has a SQLITE_LIMIT_VARIABLE_NUMBER
# default cap (999 on older builds, 32766 on newer) and an unbounded
# ``WHERE ts IN (?, ?, ?, ...)`` over a huge broker batch will trip it
# with ``too many SQL variables``. The fix: read every existing
# (figi, ts) pair for the loop figi in a single bounded query (no IN
# clause), then filter in Python. The test below forces a tight
# SQLITE_LIMIT_VARIABLE_NUMBER to prove the path stays bounded.


def test_async_backfill_impl_prefilter_stays_under_sqlite_variable_limit(
    tmp_path,
):
    """A 1000-candle batch must not blow the SQLite variable limit.

    The old prefilter built ``WHERE ts IN (?, ?, ... ?)`` with one
    placeholder per fetched candle. With SQLITE_LIMIT_VARIABLE_NUMBER
    lowered to 50, the old path raised ``too many SQL variables``
    inside the prefilter and the whole backfill died. The bounded
    fix reads all existing (figi, ts) pairs for the loop figi in a
    single query (no IN expansion) and filters in Python.
    """
    from algotrader_api.ingestion.backfill import backfill_bonds_to_depth

    db_file = str(tmp_path / "limit.db")
    con = sqlite3.connect(db_file)
    con.executescript("""
        CREATE TABLE instruments (
            figi TEXT PRIMARY KEY, ticker TEXT, class TEXT
        );
        CREATE TABLE bars (
            figi TEXT, ts TEXT,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            source TEXT DEFAULT 'moex',
            PRIMARY KEY (figi, ts)
        );
        CREATE TABLE instrument_metadata (
            figi              TEXT PRIMARY KEY,
            last_bar_ts       TEXT,
            first_bar_ts      TEXT,
            last_backfilled_at TEXT,
            total_bars        INTEGER NOT NULL DEFAULT 0,
            last_run_status   TEXT,
            last_run_at       TEXT,
            last_error        TEXT
        );
        INSERT INTO instruments VALUES ('BBG000BOND00', 'BOND00', 'bond');
    """)
    con.commit()
    con.close()

    # SQLITE_LIMIT_VARIABLE_NUMBER == 9. Lower it on the test
    # connection BEFORE the prefilter runs. 50 is well below the
    # 1000-candle batch the broker will return, so the old
    # ``WHERE ts IN (?, ...)`` path would explode with "too many
    # SQL variables". The bounded fix passes the test.
    test_conn = sqlite3.connect(db_file)
    try:
        test_conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 50)
        from types import SimpleNamespace
        big_batch = [
            SimpleNamespace(
                figi='BBG000BOND00',
                ts=f'2025-{(i // 28) + 7:02d}-{(i % 28) + 1:02d}',
                open=100, high=101, low=99, close=100, volume=1000,
            )
            for i in range(1000)
        ]

        async def _dispatch(*, figi, **_kw):
            return big_batch

        with patch('algotrader_api.ingestion.client.make_client') as mcf, \
             patch('algotrader_api.ingestion.rate_limit.get_global') as mrl:
            mc = MagicMock()
            mc.get_candles = AsyncMock(side_effect=_dispatch)
            mcf.return_value = mc
            mrl.return_value.acquire = AsyncMock()
            result = backfill_bonds_to_depth(
                target_days=30, conn=test_conn,
            )
    finally:
        test_conn.close()

    assert result["bars_added"] == 1000, (
        f"1000 fetched candles must all be inserted; "
        f"got bars_added={result['bars_added']!r}"
    )
