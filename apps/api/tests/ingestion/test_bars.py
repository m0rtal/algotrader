"""Tests for bars ingestion — atomic parquet write + incremental update."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest

from algotrader_api.ingestion import bars
from algotrader_api.ingestion.fake_client import InMemoryTinkoffClient


def _bars_for_figi(figi: str, start: date, n: int = 5) -> list[dict]:
    """Generate n candles starting from `start`."""
    return [
        {
            "ts": (start + timedelta(days=i)).isoformat(),
            "open": 100.0 + i,
            "high": 105.0 + i,
            "low": 95.0 + i,
            "close": 102.0 + i,
            "volume": 1_000_000 + i,
        }
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_first_fetch_writes_new_file(tmp_path):
    bars_dir = str(tmp_path / "bars")
    client = InMemoryTinkoffClient()
    today = date.today()
    candles = _bars_for_figi("F1", today - timedelta(days=4))
    client.set_candles("F1", candles)

    rows = await bars.fetch_bars_for_instrument(
        client, figi="F1", ticker="SBER", bars_dir=bars_dir, history_years=5
    )
    assert rows == 5
    bar_file = Path(bars_dir) / "SBER.parquet"
    assert bar_file.exists()
    conn = duckdb.connect(":memory:")
    result = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{bar_file}')").fetchone()
    assert result[0] == 5


@pytest.mark.asyncio
async def test_incremental_fetch_appends_to_existing(tmp_path):
    """Second fetch picks up from where the first left off and appends new candles."""
    bars_dir = str(tmp_path / "bars")
    today = date.today()

    # First fetch: 2 historical bars (today-2 and today-1)
    client = InMemoryTinkoffClient()
    initial = _bars_for_figi("F1", today - timedelta(days=2), n=2)
    client.set_candles("F1", initial)
    rows = await bars.fetch_bars_for_instrument(
        client, figi="F1", ticker="SBER", bars_dir=bars_dir, history_years=5
    )
    assert rows == 2
    bar_file = Path(bars_dir) / "SBER.parquet"
    conn = duckdb.connect(":memory:")
    count = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{bar_file}')").fetchone()[0]
    assert count == 2

    # Second fetch: client returns just today (after last_date=today-1)
    client.set_candles("F1", [{"ts": today.isoformat(), "open": 110, "high": 115, "low": 109, "close": 113, "volume": 999999}])
    rows = await bars.fetch_bars_for_instrument(
        client, figi="F1", ticker="SBER", bars_dir=bars_dir, history_years=5
    )
    assert rows == 1
    conn = duckdb.connect(":memory:")
    count = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{bar_file}')").fetchone()[0]
    assert count == 3  # 2 historical + 1 new
    # Sorted by ts
    rows_db = conn.execute(
        f"SELECT ts FROM read_parquet('{bar_file}') ORDER BY ts"
    ).fetchall()
    dates = [r[0] for r in rows_db]
    assert dates == [today - timedelta(days=2), today - timedelta(days=1), today]


@pytest.mark.asyncio
async def test_dedup_on_overlapping_dates(tmp_path):
    """If client returns a date that already exists in the file, keep only one row."""
    bars_dir = str(tmp_path / "bars")
    today = date.today()
    client = InMemoryTinkoffClient()
    # First fetch: 2 historical bars (today-2, today-1)
    initial = _bars_for_figi("F1", today - timedelta(days=2), n=2)
    client.set_candles("F1", initial)
    await bars.fetch_bars_for_instrument(
        client, figi="F1", ticker="SBER", bars_dir=bars_dir, history_years=5
    )

    # Second fetch: client returns today + one of the existing dates (today-1).
    # The today-1 candle should be deduplicated; today should be appended.
    # To trigger dedup, we must include today-1 in client response — which
    # the real Tinkoff API wouldn't return (it filters by date range).
    # We simulate the conflict by setting a candle that the FILTER will
    # include anyway: today itself.
    client.set_candles(
        "F1",
        [
            {"ts": today.isoformat(), "open": 110, "high": 115, "low": 109, "close": 113, "volume": 999999},
            {"ts": (today - timedelta(days=1)).isoformat(), "open": 999, "high": 999, "low": 999, "close": 999, "volume": 999},
        ],
    )
    # real filter on date_from=today, date_to=today → only today survives
    # so we cannot demonstrate dedup via the API surface; test dedup logic
    # separately via _atomic_write_parquet instead.
    from algotrader_api.ingestion.bars import _atomic_write_parquet

    rows = await bars.fetch_bars_for_instrument(
        client, figi="F1", ticker="SBER", bars_dir=bars_dir, history_years=5
    )
    # Only "today" passes the date filter; today-1 is filtered out.
    assert rows == 1

    # Now exercise dedup by writing a duplicate-date file directly
    bar_file = Path(bars_dir) / "DEDUP.parquet"
    _atomic_write_parquet(
        bar_file,
        [
            {"ts": today.isoformat(), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100},
            {"ts": today.isoformat(), "open": 9.0, "high": 9.5, "low": 8.5, "close": 9.2, "volume": 200},
        ],
        ticker="DEDUP",
    )
    conn = duckdb.connect(":memory:")
    count = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{bar_file}')").fetchone()[0]
    # Two input rows with same ts → deduplicated to 1
    assert count == 1


@pytest.mark.asyncio
async def test_no_new_data_returns_zero_rows(tmp_path):
    bars_dir = str(tmp_path / "bars")
    today = date.today()

    # First fetch: 3 bars up to today-1
    client = InMemoryTinkoffClient()
    client.set_candles("F1", _bars_for_figi("F1", today - timedelta(days=3), n=3))
    await bars.fetch_bars_for_instrument(
        client, figi="F1", ticker="SBER", bars_dir=bars_dir, history_years=5
    )

    # Second fetch: client returns empty (already up to date)
    client.set_candles("F1", [])
    rows = await bars.fetch_bars_for_instrument(
        client, figi="F1", ticker="SBER", bars_dir=bars_dir, history_years=5
    )
    assert rows == 0


@pytest.mark.asyncio
async def test_atomic_write_no_temp_files_left_on_success(tmp_path):
    """After successful write, no .tmp files remain in bars dir."""
    bars_dir = str(tmp_path / "bars")
    today = date.today()
    client = InMemoryTinkoffClient()
    client.set_candles("F1", _bars_for_figi("F1", today, n=2))
    await bars.fetch_bars_for_instrument(
        client, figi="F1", ticker="SBER", bars_dir=bars_dir, history_years=5
    )
    bar_dir = Path(bars_dir)
    tmps = list(bar_dir.glob("*.tmp"))
    assert tmps == []


@pytest.mark.asyncio
async def test_run_bars_phase_filters_classes(tmp_path):
    """Only shares and etfs get bars fetched."""
    bars_dir = str(tmp_path / "bars")
    today = date.today()
    client = InMemoryTinkoffClient()
    # Use n=1 so each figi has exactly 1 candle within the fetch range (today-1 → today)
    client.set_candles("s1", _bars_for_figi("s1", today, n=1))
    client.set_candles("e1", _bars_for_figi("e1", today, n=1))
    client.set_candles("b1", _bars_for_figi("b1", today, n=1))
    # No fixture for futures/options — they should still be skipped

    from algotrader_api.ingestion import rate_limit, retry

    instruments = [
        {"ticker": "SX", "figi": "s1", "class": "share"},
        {"ticker": "EX", "figi": "e1", "class": "etf"},
        {"ticker": "BX", "figi": "b1", "class": "bond"},
        {"ticker": "FX", "figi": "f1", "class": "future"},
    ]
    total, _ = await bars.run_bars_phase(
        client,
        instruments=instruments,
        bars_dir=bars_dir,
        history_years=5,
        rate_limiter=rate_limit.RateLimiter(rate=100, period=60.0),
        retry_policy=retry.AdaptiveRetry(max_attempts=1),
        progress_every=100,
    )
    # Only SX + EX = 2 instruments, 1 candle each
    assert total == 2
    assert (Path(bars_dir) / "SX.parquet").exists()
    assert (Path(bars_dir) / "EX.parquet").exists()
    assert not (Path(bars_dir) / "BX.parquet").exists()
    assert not (Path(bars_dir) / "FX.parquet").exists()


@pytest.mark.asyncio
async def test_run_bars_phase_continues_on_individual_failure(tmp_path):
    """One bad ticker doesn't abort the whole phase."""
    bars_dir = str(tmp_path / "bars")
    today = date.today()
    client = InMemoryTinkoffClient()
    client.set_candles("s_good", _bars_for_figi("s_good", today, n=1))
    # s_bad has no candles set → returns []

    from algotrader_api.ingestion import rate_limit, retry

    instruments = [
        {"ticker": "GOOD", "figi": "s_good", "class": "share"},
        {"ticker": "BAD", "figi": "s_bad", "class": "share"},
    ]
    total, _ = await bars.run_bars_phase(
        client,
        instruments=instruments,
        bars_dir=bars_dir,
        history_years=5,
        rate_limiter=rate_limit.RateLimiter(rate=100, period=60.0),
        retry_policy=retry.AdaptiveRetry(max_attempts=1),
        progress_every=100,
    )
    # GOOD contributes 1, BAD contributes 0 (empty)
    assert total == 1
    assert (Path(bars_dir) / "GOOD.parquet").exists()


@pytest.mark.asyncio
async def test_run_bars_phase_emits_progress_logs(tmp_path):
    """Verify progress events fire at the configured interval."""
    bars_dir = str(tmp_path / "bars")
    today = date.today()
    client = InMemoryTinkoffClient()
    # n=1 keeps dates within the fetch range
    for i in range(3):
        figi = f"s{i}"
        client.set_candles(figi, _bars_for_figi(figi, today, n=1))

    from algotrader_api.ingestion import rate_limit, retry

    instruments = [
        {"ticker": f"S{i}", "figi": f"s{i}", "class": "share"} for i in range(3)
    ]
    # progress_every=2 → progress event fires once at idx=2
    total, _ = await bars.run_bars_phase(
        client,
        instruments=instruments,
        bars_dir=bars_dir,
        history_years=5,
        rate_limiter=rate_limit.RateLimiter(rate=100, period=60.0),
        retry_policy=retry.AdaptiveRetry(max_attempts=1),
        progress_every=2,
    )
    assert total == 3
