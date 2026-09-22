"""Tests for the per-RPC timeout wrapper in RealTinkoffClient.

Validates that:
- Default timeout is 30s.
- Configurable ``request_timeout`` constructor kwarg is honoured.
- A slow SDK RPC raises ``RealClientTimeoutError`` (a subclass of
  ``asyncio.TimeoutError``) carrying the RPC label and the configured
  timeout value.
- On timeout the gRPC channel is closed and the next call rebuilds
  it — critical, because a hung HTTP/2 connection never recovers.
- Successful (fast) calls are unchanged by the wrapper.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from algotrader_api.ingestion.real_client import (
    DEFAULT_REQUEST_TIMEOUT,
    RealClientTimeoutError,
    RealTinkoffClient,
)


# ─── Stubs ────────────────────────────────────────────────────────────


class _StubServices:
    """Stand-in for ``AsyncServices`` returned by ``AsyncClient.__aenter__``.

    Each method returns a coroutine we can hang on demand. We track
    every call so the test can assert channel rebuild behaviour.
    """

    def __init__(self) -> None:
        self.call_count = 0
        self.users = SimpleNamespace(get_accounts=self._slow_get_accounts)
        self.instruments = SimpleNamespace(get_dividends=self._slow_get_dividends)

    async def _slow_get_accounts(self) -> Any:
        self.call_count += 1
        # Yield to the loop enough times to let wait_for fire.
        for _ in range(50):
            await asyncio.sleep(0.1)
        self.call_count -= 1  # never reached on a timeout
        return SimpleNamespace(accounts=[])

    async def _slow_get_dividends(self, **kwargs: Any) -> Any:
        self.call_count += 1
        for _ in range(50):
            await asyncio.sleep(0.1)
        self.call_count -= 1
        return SimpleNamespace(dividends=[])


class _StubAsyncClient:
    """Counts open/close cycles so we can assert channel rebuild."""

    def __init__(self, services: _StubServices) -> None:
        self._services = services
        self.open_count = 0
        self.close_count = 0

    async def __aenter__(self) -> _StubServices:
        self.open_count += 1
        return self._services

    async def __aexit__(self, *args: Any) -> None:
        self.close_count += 1


def _patch_sdk(monkeypatch: pytest.MonkeyPatch, services: _StubServices, client: _StubAsyncClient) -> None:
    """Wire importlib.import_module so RealTinkoffClient sees our stubs."""
    fake_sdk = SimpleNamespace(AsyncClient=lambda token, target: client)
    monkeypatch.setattr(
        "algotrader_api.ingestion.real_client.importlib.import_module",
        lambda name: fake_sdk if name == "t_tech.invest" else SimpleNamespace(
            INVEST_GRPC_API_SANDBOX="sandbox-test",
        ),
    )


# ─── Config tests ─────────────────────────────────────────────────────


def test_default_request_timeout_is_30_seconds() -> None:
    assert DEFAULT_REQUEST_TIMEOUT == 30.0


def test_constructor_rejects_non_positive_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_sdk = SimpleNamespace(AsyncClient=lambda token, target: None)
    monkeypatch.setattr(
        "algotrader_api.ingestion.real_client.importlib.import_module",
        lambda name: fake_sdk if name == "t_tech.invest" else SimpleNamespace(
            INVEST_GRPC_API_SANDBOX="sandbox-test",
        ),
    )
    with pytest.raises(ValueError, match="request_timeout must be > 0"):
        RealTinkoffClient(token="t", request_timeout=0)
    with pytest.raises(ValueError, match="request_timeout must be > 0"):
        RealTinkoffClient(token="t", request_timeout=-1.0)


def test_constructor_accepts_custom_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_sdk = SimpleNamespace(AsyncClient=lambda token, target: None)
    monkeypatch.setattr(
        "algotrader_api.ingestion.real_client.importlib.import_module",
        lambda name: fake_sdk if name == "t_tech.invest" else SimpleNamespace(
            INVEST_GRPC_API_SANDBOX="sandbox-test",
        ),
    )
    client = RealTinkoffClient(token="t", request_timeout=5.0)
    assert client.request_timeout == 5.0


# ─── Timeout behaviour ────────────────────────────────────────────────


async def test_timeout_fires_and_raises_real_client_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow SDK RPC must raise ``RealClientTimeoutError`` within the
    configured timeout, not hang forever."""
    services = _StubServices()
    client = _StubAsyncClient(services)
    _patch_sdk(monkeypatch, services, client)

    wrapper = RealTinkoffClient(token="t", request_timeout=0.1)

    with pytest.raises(RealClientTimeoutError) as exc_info:
        await wrapper.get_accounts()

    assert exc_info.value.label == "users.get_accounts"
    assert exc_info.value.timeout == pytest.approx(0.1)
    # Subclass relationship — callers catching asyncio.TimeoutError keep working.
    assert isinstance(exc_info.value, asyncio.TimeoutError)


async def test_timeout_resets_channel_so_next_call_rebuilds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a timeout the channel must be torn down; the next call
    must open a fresh one. This is the whole point of the fix: a hung
    HTTP/2 connection never recovers on its own."""
    services = _StubServices()
    client = _StubAsyncClient(services)
    _patch_sdk(monkeypatch, services, client)

    wrapper = RealTinkoffClient(token="t", request_timeout=0.1)

    # First call: times out.
    with pytest.raises(RealClientTimeoutError):
        await wrapper.get_accounts()
    assert client.open_count == 1
    assert client.close_count == 1
    assert wrapper._client is None  # type: ignore[attr-defined]
    assert wrapper._services is None  # type: ignore[attr-defined]

    # Second call: rebuilds the channel; times out again, exercising the path.
    with pytest.raises(RealClientTimeoutError):
        await wrapper.get_accounts()
    assert client.open_count == 2
    assert client.close_count == 2


# ─── Happy path: fast SDK calls must keep working ─────────────────────


async def test_fast_call_succeeds_and_returns_dicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The timeout wrapper must be a no-op for fast calls."""

    class _FastServices:
        def __init__(self) -> None:
            self.users = SimpleNamespace(get_accounts=self._get_accounts)

        async def _get_accounts(self) -> Any:
            acct = SimpleNamespace(
                id="ACC-1", name="Main", type="ACCOUNT_TYPE_TINKOFF", status="ACCOUNT_STATUS_OPEN"
            )
            return SimpleNamespace(accounts=[acct])

    fast_services = _FastServices()
    stub_client = _StubAsyncClient(fast_services)  # type: ignore[arg-type]
    fake_sdk = SimpleNamespace(AsyncClient=lambda token, target: stub_client)
    monkeypatch.setattr(
        "algotrader_api.ingestion.real_client.importlib.import_module",
        lambda name: fake_sdk if name == "t_tech.invest" else SimpleNamespace(
            INVEST_GRPC_API_SANDBOX="sandbox-test",
        ),
    )

    wrapper = RealTinkoffClient(token="t", request_timeout=5.0)
    accounts = await wrapper.get_accounts()

    assert accounts == [
        {"id": "ACC-1", "name": "Main", "type": "ACCOUNT_TYPE_TINKOFF", "status": "ACCOUNT_STATUS_OPEN"}
    ]
    assert stub_client.open_count == 1
    # No timeout ⇒ no reset ⇒ channel stays open.
    assert stub_client.close_count == 0