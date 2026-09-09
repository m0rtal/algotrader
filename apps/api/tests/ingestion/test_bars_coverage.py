"""Coverage tests for bars.py defensive / edge-case paths."""

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from algotrader_api.ingestion.bars import (
    _atomic_write_parquet,
    _max_date_in_parquet,
    append_bars,
    fetch_bars_for_instrument,
)


class _NoDataClient:
    async def get_candles(self, **kwargs):
        return []


class _RateLimitedClient:
    async def get_candles(self, **kwargs):
        raise RuntimeError("RESOURCE_EXHAUSTED: rate limit exceeded")


class _AcquireFailedClient:
    async def get_candles(self, **kwargs):
        raise ConnectionError("upstream down")


@pytest.mark.asyncio
async def test_fetch_bars_returns_zero_when_date_from_after_date_to(tmp_path):
    """Up-to-date ticker: existing parquet has today's max → no work."""
    bar_file = tmp_path / "SBER.parquet"
    rows = [
        {"ts": date.today().isoformat(), "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.5, "volume": 1000},
    ]
    _atomic_write_parquet(bar_file, rows, ticker="SBER")
    client = _NoDataClient()
    n = await fetch_bars_for_instrument(
        client,
        figi="BBG004730N88",
        ticker="SBER",
        bars_dir=str(tmp_path),
        history_years=1,
    )
    assert n == 0


@pytest.mark.asyncio
async def test_fetch_bars_no_data_creates_empty_parquet_when_no_existing(tmp_path):
    bar_file = tmp_path / "SBER.parquet"
    assert not bar_file.exists()
    client = _NoDataClient()
    n = await fetch_bars_for_instrument(
        client,
        figi="BBG004730N88",
        ticker="SBER",
        bars_dir=str(tmp_path),
        history_years=1,
    )
    assert n == 0
    assert bar_file.exists()


@pytest.mark.asyncio
async def test_fetch_bars_propagates_exceptions():
    """fetch_bars_for_instrument does NOT swallow — caller wraps it."""
    client = _RateLimitedClient()
    with pytest.raises(RuntimeError):
        await fetch_bars_for_instrument(
            client,
            figi="BBG004730RP0",
            ticker="GAZP",
            bars_dir="/tmp",
            history_years=1,
        )


@pytest.mark.asyncio
async def test_run_bars_phase_swallows_acquire_errors(tmp_path):
    """acquire() raising → caught by outer try, logs warning."""
    from algotrader_api.ingestion.bars import run_bars_phase

    class _AcquireFails:
        async def acquire(self, *a, **k):
            raise ConnectionError("acquire broken")

    class _IdentityRetry:
        async def run(self, fn, *a, **k):
            return await fn()

    instruments = [{"ticker": "X", "figi": "F", "class": "share"}]
    rows, hits = await run_bars_phase(
        _RateLimitedClient(),
        instruments=instruments,
        bars_dir=str(tmp_path),
        history_years=1,
        rate_limiter=_AcquireFails(),
        retry_policy=_IdentityRetry(),
    )
    assert rows == 0
    assert hits == 0


@pytest.mark.asyncio
async def test_run_bars_phase_records_rate_limit_hits(tmp_path):
    """RESOURCE_EXHAUSTED in error msg increments rate_limit_hits."""
    from algotrader_api.ingestion.bars import run_bars_phase

    class _RL:
        async def acquire(self, *a, **k): pass
        def record_success(self, *a, **k): pass
        def record_error(self, *a, **k): pass

    class _IdentityRetry:
        async def run(self, fn, *a, **k):
            return await fn()

    instruments = [{"ticker": "X", "figi": "F", "class": "share"}]
    rows, hits = await run_bars_phase(
        _RateLimitedClient(),
        instruments=instruments,
        bars_dir=str(tmp_path),
        history_years=1,
        rate_limiter=_RL(),
        retry_policy=_IdentityRetry(),
    )
    assert rows == 0
    assert hits == 1


def test_max_date_in_parquet_returns_none_for_missing(tmp_path):
    assert _max_date_in_parquet(tmp_path / "nope.parquet") is None


def test_max_date_in_parquet_returns_none_for_malformed(tmp_path):
    bar_file = tmp_path / "broken.parquet"
    bar_file.write_bytes(b"not a parquet file at all")
    assert _max_date_in_parquet(bar_file) is None


def test_atomic_write_parquet_handles_malformed_existing(tmp_path):
    bar_file = tmp_path / "SBER.parquet"
    bar_file.write_bytes(b"not a parquet")
    rows = [
        {"ts": "2024-06-01", "open": 100.0, "high": 101.0, "low": 99.0,
         "close": 100.5, "volume": 1000},
    ]
    _atomic_write_parquet(bar_file, rows, ticker="SBER")
    assert bar_file.exists()


def test_atomic_write_parquet_handles_string_ts_in_existing(tmp_path):
    """Result column may come back as a string — we coerce to date."""
    bar_file = tmp_path / "SBER.parquet"
    rows = [
        {"ts": "2024-06-01", "open": 100.0, "high": 101.0, "low": 99.0,
         "close": 100.5, "volume": 1000},
    ]
    _atomic_write_parquet(bar_file, rows, ticker="SBER")
    # The file should now be a valid parquet; max date returns a date.
    d = _max_date_in_parquet(bar_file)
    assert isinstance(d, date)
    assert d.isoformat() == "2024-06-01"


def test_append_bars_with_empty_candles_returns_zero(tmp_path):
    path = tmp_path / "X.parquet"
    n = append_bars(path=str(path), candles=[])
    assert n == 0
    assert not path.exists()
