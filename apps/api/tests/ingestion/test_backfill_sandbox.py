"""Real Tinkoff sandbox integration test for the backfill runner.

Verifies that `_backfill_one` actually fetches real MOEX data and writes
it to parquet + updates `instrument_metadata`. Gated by
`RUN_SANDBOX_INTEGRATION=1` so the default `pytest tests/` invocation
does not hit the network.

To run locally:
    RUN_SANDBOX_INTEGRATION=1 \\
        uv run pytest tests/ingestion/test_backfill_sandbox.py -v
"""
from __future__ import annotations

import asyncio
import os
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_SANDBOX_INTEGRATION"),
    reason="sandbox tests disabled",
)


def _real_token_or_skip():
    from algotrader_api.config import get_settings
    from algotrader_api.db.secrets import get_broker_token
    token = get_broker_token(get_settings().sqlite_path)
    if not token:
        pytest.skip("no broker token in secrets table — set one via Settings UI")
    return token


@pytest.mark.asyncio
async def test_sandbox_backfill_one_ticker_sber():
    """End-to-end: real SDK → backfill_one → parquet + metadata for SBER."""
    from algotrader_api.config import get_settings
    from algotrader_api.ingestion.backfill import BackfillRunner
    from algotrader_api.ingestion.client import make_client

    _real_token_or_skip()
    settings = get_settings()
    db_path = settings.sqlite_path
    bars_dir = settings.bars_dir

    client = make_client(sqlite_path=db_path, use_fake=False)

    events = []

    async def collect(ev):
        events.append(ev)

    runner = BackfillRunner(
        client=client,
        db_path=db_path,
        bars_dir=bars_dir,
        event_sink=collect,
    )

    # SBER has FIGI BBG004730N88. Pull a 90-day window to keep the test
    # fast — sandbox serves the same data as production (with a 15-min
    # delay), so any window works.
    written = await runner._backfill_one(
        figi="BBG004730N88",
        from_=date(2024, 9, 1),
        to=date(2024, 11, 30),
    )

    assert written > 0, "sandbox returned 0 candles for SBER Q4 2024"

    # Parquet file should exist now.
    import pathlib
    parquet = pathlib.Path(bars_dir) / "BBG004730N88.parquet"
    assert parquet.exists(), f"parquet not written at {parquet}"

    # Metadata row should have last_bar_ts set and status='ok'.
    import sqlite3
    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT last_bar_ts, total_bars, last_run_status FROM instrument_metadata WHERE figi = ?",
        ("BBG004730N88",),
    ).fetchone()
    con.close()
    assert row is not None, "instrument_metadata row missing"
    assert row[2] == "ok", f"unexpected status: {row[2]}"
    assert row[1] >= written, f"total_bars {row[1]} < written {written}"
    assert row[0] is not None, "last_bar_ts not set"
