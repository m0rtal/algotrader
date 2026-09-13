"""Tests for issue #3: exhausted-marker reset path.

The guardian writes ``completeness_exhausted`` (completeness module)
or ``stale_recovery_exhausted`` (recovery module) to
``instrument_metadata.last_run_status`` when the broker returns zero
candles for three cycles in a row. Without an operator path to clear
the marker, a figi that was temporarily unreachable never resumes
processing — every subsequent guardian cycle skips it.

This module covers the three reset paths:

1. ``reset_exhausted_marker`` helper — direct unit test.
2. ``backfill_gaps`` auto-clear — the marker is cleared automatically
   when a broker call returns bars for a previously-exhausted figi.
   ``replace_bars_for_figi`` overwrites ``last_run_status`` to 'ok'
   in the SAME transaction as the bars insert, so the auto-clear
   must run BEFORE that helper (issue #3's root design constraint).
3. ``POST /api/admin/data-quality/reset-exhausted/{symbol}`` — the
   operator escape hatch for cases where the upstream cause is
   fixed but the broker hasn't been retried yet.

Tested RED-first: each test would fail before the corresponding
production change lands (the helper doesn't exist, the route returns
404, etc.).
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest


# ── reset_exhausted_marker (direct unit) ────────────────────────────


def test_reset_returns_none_when_no_metadata_row_exists(tmp_path):
    """No row -> no-op. The helper never creates metadata rows;
    a non-existent figi simply returns None."""
    from algotrader_api.db.sqlite import get_connection
    from algotrader_api.data_quality.completeness import reset_exhausted_marker

    db = str(tmp_path / "state.db")
    # Exercise on a freshly-created empty DB (no metadata table at all).
    # ``reset_exhausted_marker`` catches the OperationalError and returns None.
    con = get_connection(db)
    try:
        con.execute(
            "CREATE TABLE instrument_metadata ("
            "figi TEXT PRIMARY KEY, last_run_status TEXT)"
        )
        result = reset_exhausted_marker(db, "BBG000000000")
        assert result is None
    finally:
        con.close()


def test_reset_clears_completeness_exhausted_returns_previous_status(tmp_path):
    """Marker cleared; previous status returned to the caller."""
    from algotrader_api.data_quality.completeness import reset_exhausted_marker

    db = str(tmp_path / "state.db")
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE instrument_metadata ("
        "figi TEXT PRIMARY KEY, last_run_status TEXT, total_bars INTEGER)"
    )
    con.execute(
        "INSERT INTO instrument_metadata (figi, last_run_status, total_bars) "
        "VALUES ('FIGI-EXH', 'completeness_exhausted', 0)"
    )
    con.commit()
    con.close()

    prev = reset_exhausted_marker(db, "FIGI-EXH")
    assert prev == "completeness_exhausted"

    con = sqlite3.connect(db)
    try:
        status = con.execute(
            "SELECT last_run_status FROM instrument_metadata WHERE figi = ?",
            ("FIGI-EXH",),
        ).fetchone()[0]
        assert status == "ready"
    finally:
        con.close()


def test_reset_clears_stale_recovery_exhausted(tmp_path):
    """Both sentinel spellings must round-trip through the same helper."""
    from algotrader_api.data_quality.completeness import reset_exhausted_marker

    db = str(tmp_path / "state.db")
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE instrument_metadata ("
        "figi TEXT PRIMARY KEY, last_run_status TEXT)"
    )
    con.execute(
        "INSERT INTO instrument_metadata (figi, last_run_status) "
        "VALUES ('FIGI-RX', 'stale_recovery_exhausted')"
    )
    con.commit()
    con.close()

    prev = reset_exhausted_marker(db, "FIGI-RX")
    assert prev == "stale_recovery_exhausted"


def test_reset_is_noop_for_ready_row(tmp_path):
    """Non-exhausted rows stay unchanged — only sentinels get cleared."""
    from algotrader_api.data_quality.completeness import reset_exhausted_marker

    db = str(tmp_path / "state.db")
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE instrument_metadata ("
        "figi TEXT PRIMARY KEY, last_run_status TEXT)"
    )
    con.executemany(
        "INSERT INTO instrument_metadata (figi, last_run_status) VALUES (?, ?)",
        [("OK", "ready"), ("PENDING", "pending"), ("ERR", "error")],
    )
    con.commit()
    con.close()

    for figi in ("OK", "PENDING", "ERR"):
        assert reset_exhausted_marker(db, figi) is None


# ── backfill_gaps auto-clear (integration) ──────────────────────────


@pytest.mark.asyncio
async def test_backfill_gaps_clears_exhausted_on_broker_success(tmp_path):
    """Broker returns bars for a previously-exhausted figi ->
    the marker is auto-cleared so the next guardian cycle processes it.

    Critical sequencing note (issue #3 root constraint):
    ``replace_bars_for_figi`` overwrites ``last_run_status`` to 'ok'
    in its own transaction, so the auto-clear MUST happen BEFORE
    that call. If it happens after, the sentinel is already gone.
    """
    from algotrader_api.data_quality.completeness import backfill_gaps
    from algotrader_api.db.migrations import MIGRATIONS_DIR
    from algotrader_api.db.sqlite import run_migrations

    db = str(tmp_path / "state.db")
    run_migrations(db, str(MIGRATIONS_DIR))

    # Pre-populate: exhausted metadata + two bars framing a gap.
    figi = "FIGI-EXH-1"
    today = date.today()
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO instrument_metadata (figi, last_run_status, total_bars) "
        "VALUES (?, 'completeness_exhausted', 0)",
        (figi,),
    )
    for offset in (-10, -3):
        con.execute(
            "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
            "VALUES (?, ?, 1, 1, 1, 1, 1)",
            (figi, (today + timedelta(days=offset)).isoformat()),
        )
    con.commit()
    con.close()

    # Broker returns a candle in the gap window.
    gap_day = (today + timedelta(days=-7)).isoformat()
    fake_client = AsyncMock()
    fake_client.get_candles = AsyncMock(
        return_value=[{
            "ts": gap_day, "open": 1, "high": 1,
            "low": 1, "close": 1, "volume": 1,
        }]
    )

    gaps = [(today + timedelta(days=-10), today + timedelta(days=-3))]
    added = await backfill_gaps(fake_client, figi, gaps, db)
    assert added == 1

    con = sqlite3.connect(db)
    try:
        status = con.execute(
            "SELECT last_run_status FROM instrument_metadata WHERE figi = ?",
            (figi,),
        ).fetchone()[0]
        assert status == "ready", \
            f"exhausted marker not cleared: last_run_status={status!r}"
    finally:
        con.close()


@pytest.mark.asyncio
async def test_backfill_gaps_does_not_clear_when_broker_returns_nothing(tmp_path):
    """Broker returns zero candles -> the exhausted marker stays.

    Clearing the marker when the broker has nothing would silently
    hide a real upstream outage — operator would stop seeing the
    sentinel even though the figi is still unreachable.
    """
    from algotrader_api.data_quality.completeness import backfill_gaps
    from algotrader_api.db.migrations import MIGRATIONS_DIR
    from algotrader_api.db.sqlite import run_migrations

    db = str(tmp_path / "state.db")
    run_migrations(db, str(MIGRATIONS_DIR))

    figi = "FIGI-EXH-2"
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO instrument_metadata (figi, last_run_status, total_bars) "
        "VALUES (?, 'stale_recovery_exhausted', 0)",
        (figi,),
    )
    con.commit()
    con.close()

    fake_client = AsyncMock()
    fake_client.get_candles = AsyncMock(return_value=[])

    gaps = [(date.today() + timedelta(days=-10), date.today() + timedelta(days=-3))]
    added = await backfill_gaps(fake_client, figi, gaps, db)
    assert added == 0

    con = sqlite3.connect(db)
    try:
        status = con.execute(
            "SELECT last_run_status FROM instrument_metadata WHERE figi = ?",
            (figi,),
        ).fetchone()[0]
        assert status == "stale_recovery_exhausted", \
            "marker must NOT clear when broker returns zero candles"
    finally:
        con.close()


# ── admin route ─────────────────────────────────────────────────────


def test_admin_reset_route_clears_marker(client, fresh_db):
    """End-to-end: POST /api/admin/data-quality/reset-exhausted/SBER"""
    con = sqlite3.connect(fresh_db)
    con.execute(
        "INSERT OR IGNORE INTO instruments "
        "(ticker, figi, class, name, currency, lot_size) "
        "VALUES ('SBER', 'FIGI-EXH-1', 'share', 'Sber', 'RUB', 10)"
    )
    con.execute(
        "INSERT INTO instrument_metadata (figi, last_run_status, total_bars) "
        "VALUES ('FIGI-EXH-1', 'completeness_exhausted', 0)"
    )
    con.commit()
    con.close()

    resp = client.post("/api/admin/data-quality/reset-exhausted/SBER")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["previous_status"] == "completeness_exhausted"
    assert body["status"] == "ready"
    assert body["figi"] == "FIGI-EXH-1"


def test_admin_reset_route_404_on_clean_figi(client, fresh_db):
    """No exhausted marker -> 404, not a silent no-op.

    A 200-with-empty-body would let the operator think they cleared
    a marker that was never set; 404 makes the missing-marker case
    explicit (and triggers a different alert in their tooling).
    """
    con = sqlite3.connect(fresh_db)
    con.execute(
        "INSERT OR IGNORE INTO instruments "
        "(ticker, figi, class, name, currency, lot_size) "
        "VALUES ('CLEAN', 'FIGI-OK', 'share', 'Clean', 'RUB', 1)"
    )
    con.execute(
        "INSERT INTO instrument_metadata (figi, last_run_status, total_bars) "
        "VALUES ('FIGI-OK', 'ready', 100)"
    )
    con.commit()
    con.close()

    resp = client.post("/api/admin/data-quality/reset-exhausted/CLEAN")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "no_exhausted_marker"


def test_admin_reset_route_404_on_unknown_symbol(client):
    """Unknown ticker / figi -> 404 with ``unknown_symbol`` error."""
    resp = client.post("/api/admin/data-quality/reset-exhausted/BBG-NOPE")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_symbol"
