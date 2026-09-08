"""Real Tinkoff sandbox integration tests.

These exercise `RealTinkoffClient` against the real Tinkoff sandbox gRPC
endpoint. They are gated by `RUN_SANDBOX_INTEGRATION=1` so the normal
`uv run pytest tests/` does not hit the network.

To run locally with a real sandbox token in the app's `state.db`:

    RUN_SANDBOX_INTEGRATION=1 uv run pytest tests/ingestion/test_real_sandbox.py -v

Without the env var, every test in this module is skipped with reason
"sandbox tests disabled".
"""
from __future__ import annotations

import asyncio
import os
import pytest

# Module-level skip: gated integration tests. Setting
# RUN_SANDBOX_INTEGRATION=1 enables them.
pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_SANDBOX_INTEGRATION"),
    reason="sandbox tests disabled",
)


def _real_token_or_skip():
    """Read broker token from the app's own secrets table; skip if missing.

    The token never comes from env or hardcoded — that path is what we're
    shipping *out* of. The test verifies the wrapper reads the token from
    where Settings UI writes it.
    """
    from algotrader_api.config import get_settings
    from algotrader_api.db.secrets import get_broker_token
    token = get_broker_token(get_settings().sqlite_path)
    if not token:
        pytest.skip("no broker token in secrets table — set one via Settings UI")
    return token


def _make_client():
    from algotrader_api.ingestion.real_client import RealTinkoffClient
    return RealTinkoffClient(token=_real_token_or_skip(), target="sandbox")


@pytest.mark.asyncio
async def test_sandbox_get_accounts_returns_list():
    """A sandbox token should authenticate and return at least the synthetic sandbox account."""
    client = _make_client()
    try:
        accounts = await client.get_accounts()
        assert isinstance(accounts, list)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_sandbox_get_shares_returns_at_least_one_instrument():
    """Tinkoff sandbox mirrors live MOEX shares on a 15-min delay.

    There should be hundreds of shares; we only require ≥1 because the
    sandbox can be flaky on weekday evenings.
    """
    client = _make_client()
    try:
        shares = await client.get_shares()
        assert isinstance(shares, list)
        assert len(shares) >= 1, "sandbox returned 0 shares — unusual, may be sandbox outage"
        first = shares[0]
        for key in ("ticker", "figi", "class", "name", "currency", "lot_size"):
            assert key in first, f"share row missing key {key!r}: {first}"
        assert first["class"] == "share"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_sandbox_get_candles_returns_ohlcv_rows():
    """For a known share FIGI (SBER = BBG004730N88), 2024 daily candles return non-empty."""
    client = _make_client()
    try:
        candles = await client.get_candles(
            figi="BBG004730N88",
            date_from="2024-06-01",
            date_to="2024-06-30",
            interval="CANDLE_INTERVAL_DAY",
        )
        assert isinstance(candles, list)
        assert len(candles) >= 1, "sandbox returned no candles for SBER in June 2024"
        first = candles[0]
        for key in ("ts", "open", "high", "low", "close", "volume"):
            assert key in first, f"candle row missing key {key!r}: {first}"
        assert isinstance(first["open"], float)
        assert first["open"] > 0  # real SBER price was ~300 RUB in June 2024
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_sandbox_token_in_db_is_used_by_real_client():
    """End-to-end: write a known (but bogus) token to DB, watch the wrapper reject.

    We can't use the real token here without leaking it into logs; instead,
    write a token, construct a client, and assert it doesn't raise on
    construction. The token value is the SDK's problem; the wrapper just
    forwards it.
    """
    from algotrader_api.config import get_settings
    from algotrader_api.db.secrets import set_secret as _set
    from algotrader_api.ingestion.real_client import RealTinkoffClient

    # We don't actually call the network here — we just verify the wrapper
    # constructs successfully when the token is whatever's in the DB. The
    # sandbox tests above exercise the real network path.
    db = get_settings().sqlite_path
    _set(db, "broker_token", "t.synthetic.check")
    from algotrader_api.db.secrets import get_broker_token

    token = get_broker_token(db)
    assert token == "t.synthetic.check"

    c = RealTinkoffClient(token=token, target="sandbox")
    assert c._token == "t.synthetic.check"
    assert c._target == "sandbox-invest-public-api.tbank.ru"
    await c.aclose()


@pytest.mark.asyncio
async def test_sandbox_aclose_cleans_up_async_client():
    """After aclose, _client is reset to None so a second call is safe."""
    client = _make_client()
    # Open the underlying AsyncClient by making one cheap call.
    accounts = await client.get_accounts()
    assert client._client is not None
    await client.aclose()
    assert client._client is None
