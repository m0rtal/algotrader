# apps/api/tests/test_backfill_metadata_poison.py
"""Verify the metadata poison is scoped: Tinkoff short-window still marks
skipped on empty; MOEX path does not."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from algotrader_api.ingestion.backfill import BackfillRunner


@pytest.fixture(autouse=True)
def _migrated_memory_db(tmp_path, monkeypatch):
    """Run migrations on a tmp DB and redirect ``sqlite3.connect(':memory:')``
    inside ``backfill`` to that DB.

    The verbatim test code constructs ``BackfillRunner(db_path=":memory:")``;
    production ``_log`` opens a fresh connection per call, so the in-memory
    database needs the schema (notably ``ingestion_logs`` and
    ``instrument_metadata``) before the runner can write to it.
    """
    from algotrader_api.db import sqlite as sqlitedb

    migrations_dir = str(
        Path(__file__).resolve().parent.parent
        / "src" / "algotrader_api" / "db" / "migrations"
    )
    db_file = str(tmp_path / "state.db")
    sqlitedb.run_migrations(db_file, migrations_dir)
    sqlitedb.close_all()

    import algotrader_api.ingestion.backfill as backfill_mod
    import sqlite3 as _real_sqlite3

    real_connect = _real_sqlite3.connect

    def shim_connect(database, *args, **kwargs):
        if database == ":memory:":
            return real_connect(db_file, *args, **kwargs)
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(backfill_mod.sqlite3, "connect", shim_connect)

    yield

    sqlitedb.close_all()


def _make_runner():
    return BackfillRunner(
        client=MagicMock(), db_path=":memory:",
        event_sink=lambda ev: None,
    )


def test_tinkoff_empty_short_window_marks_skipped():
    """Existing behaviour preserved: 30-day window with Tinkoff empty
    response → status='skipped', last_bar_ts=today."""
    runner = _make_runner()
    upserts: list[dict] = []

    def fake_upsert(figi, last_bar_ts, total_bars, status, error_msg):
        upserts.append({"status": status, "last_bar_ts": last_bar_ts})

    runner._upsert_metadata = fake_upsert
    runner.client.get_candles = AsyncMock(return_value=[])

    import asyncio
    asyncio.run(runner._backfill_one(
        figi="00000000-0000-0000-0000-000000000010",
        ticker="SHORT",
        from_=date(2026, 8, 1), to=date(2026, 8, 30),
        source="tinkoff",
    ))
    assert any(u["status"] == "skipped" for u in upserts), \
        f"Tinkoff short empty response must mark skipped: {upserts}"


def test_moex_empty_long_window_does_not_mark_skipped():
    """The bug from 2026-09-16: MOEX-empty response for a long window
    MUST NOT stamp 'skipped', because that would block the next pass."""
    runner = _make_runner()
    upserts: list[dict] = []
    runner._upsert_metadata = lambda **kw: upserts.append(kw)
    runner._fetch_year_moex = staticmethod(
        lambda market, board, ticker, year, last_trading_day=None: []
    )
    runner._get_meta_moex = staticmethod(
        lambda ticker, *a, **kw: {"market": "shares", "board": "TQBR",
                                  "listed_from": date(2021, 1, 1)}
    )

    import asyncio
    asyncio.run(runner._backfill_one(
        figi="00000000-0000-0000-0000-000000000011",
        ticker="MOEXEMPTY",
        from_=date(2021, 1, 1), to=date(2026, 9, 21),
        source="moex",
    ))
    skipped = [u for u in upserts if u.get("status") == "skipped"]
    assert skipped == [], f"MOEX-empty must NOT mark skipped: {skipped}"