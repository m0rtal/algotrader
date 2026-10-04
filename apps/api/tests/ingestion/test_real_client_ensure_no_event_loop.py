"""Regression test for PR #120: RealTinkoffClient._ensure must NOT
raise "There is no current event loop in thread 'asyncio_0'".

Background: previous _ensure wrapped `AsyncClient(...)` in
`asyncio.to_thread` to "time out" the constructor. The wrap
dispatched the constructor to a ThreadPoolExecutor worker that has
no event loop, but ``grpc.aio.Channel.__init__`` calls
``cygrpc.get_working_loop()`` → ``asyncio.get_event_loop()`` → fails
when called from a thread without a running loop.

Fix: call `AsyncClient(...)` directly from the async function. The
channel is lazy (no I/O), so it returns in microseconds; the actual
network handshake happens at `__aenter__`, which IS async and
already wrapped in `asyncio.wait_for`.

Test plan:
- Stub ``AsyncClient`` to a class whose ``__init__`` calls
  ``cygrpc.get_working_loop()`` (or a stand-in that mirrors the
  behaviour).
- Construct ``RealTinkoffClient`` and call ``await client._ensure()``
  inside a real `asyncio.run`.
- Without the fix: RuntimeError "There is no current event loop".
- With the fix:    _ensure resolves and returns the stub services.
"""
from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace
from typing import Any

import pytest

from algotrader_api.ingestion import real_client


class _ChannelNeedingLoop:
    """Stand-in for grpc.aio.Channel: __init__ MUST find a running loop."""

    def __init__(self) -> None:
        # Mirror what grpc.aio.Channel.__init__ does: ask the
        # current thread for a working loop. If called from a thread
        # that has no loop bound (e.g. an asyncio.to_thread worker),
        # this raises RuntimeError.
        self.loop = asyncio.get_running_loop()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _AsyncClientNeedingLoop:
    """Drop-in for the tinkoff ``AsyncClient``.

    ``RealTinkoffClient._ensure`` calls ``getattr(self._sdk,
    "AsyncClient")`` and constructs it. We make that constructor
    require a running event loop, so the regression fires if
    ``_ensure`` ever dispatches construction off-thread.
    """

    def __init__(self, token: str, *, target: str | None = None) -> None:
        self._token = token
        self._target = target
        self._channel = _ChannelNeedingLoop()
        self.services = SimpleNamespace(
            users=SimpleNamespace(),
            instruments=SimpleNamespace(),
            market_data=SimpleNamespace(),
        )

    async def __aenter__(self) -> Any:
        # Mirror AsyncClient.__aenter__: awaited services, not the client.
        await asyncio.sleep(0)
        return self.services

    async def __aexit__(self, *exc: Any) -> bool:
        await self._channel.close()
        return False


def _patch_sdk(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Intercept only SDK imports, without patching shared importlib."""
    import_module = real_client.importlib.import_module
    sdk = SimpleNamespace(AsyncClient=_AsyncClientNeedingLoop)
    constants = SimpleNamespace(
        INVEST_GRPC_API="offline-production",
        INVEST_GRPC_API_SANDBOX="offline-sandbox",
    )
    sdk_imports: list[str] = []

    def _import_module(name: str, package: str | None = None) -> Any:
        if name == "t_tech.invest":
            sdk_imports.append(name)
            return sdk
        if name == "t_tech.invest.constants":
            sdk_imports.append(name)
            return constants
        if name == "t_tech" or name.startswith("t_tech."):
            raise AssertionError(f"Unexpected real SDK import: {name}")
        return import_module(name, package)

    monkeypatch.setattr(
        real_client, "importlib", SimpleNamespace(import_module=_import_module),
    )
    return sdk_imports


def test_ensure_works_inside_running_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """The actual regression: ``_ensure`` must succeed when awaited.

    Without the fix, ``AsyncClient.__init__`` runs in a
    ``ThreadPoolExecutor`` worker that has no event loop. The
    constructor's call to ``asyncio.get_running_loop()`` (mirroring
    ``grpc.aio.Channel.__init__``) raises ``RuntimeError: There is
    no current event loop in thread 'asyncio_0'``.
    """
    import importlib

    original_import = importlib.import_module
    sdk_imports = _patch_sdk(monkeypatch)
    assert importlib.import_module is original_import
    assert real_client.importlib.import_module("asyncio") is asyncio
    network_attempts: list[Any] = []
    socket_connect = socket.socket.connect
    socket_connect_ex = socket.socket.connect_ex

    def _guard_connect(sock: socket.socket, address: Any) -> Any:
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            network_attempts.append(address)
            raise AssertionError("REAL_NETWORK_ATTEMPT_BLOCKED")
        return socket_connect(sock, address)

    def _guard_connect_ex(sock: socket.socket, address: Any) -> Any:
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            network_attempts.append(address)
            raise AssertionError("REAL_NETWORK_ATTEMPT_BLOCKED")
        return socket_connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", _guard_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guard_connect_ex)
    client = real_client.RealTinkoffClient(token="fake", target="sandbox")
    assert client._sdk.AsyncClient is _AsyncClientNeedingLoop
    assert client._client is None
    assert client._services is None

    async def _drive() -> None:
        try:
            services = await client._ensure()
            context = client._client
            assert isinstance(context, _AsyncClientNeedingLoop)
            assert services is context.services
            assert client._services is services
            assert context._token == "fake"
            assert context._target == client._target == "offline-sandbox"
            assert context._channel.loop is asyncio.get_running_loop()
            assert not context._channel.closed
            assert await client._ensure() is services
            assert client._client is context
        finally:
            await client.aclose()
        assert context._channel.closed
        assert client._client is None
        assert client._services is None

    asyncio.run(_drive())
    assert sdk_imports == ["t_tech.invest", "t_tech.invest.constants"]
    assert network_attempts == []


def test_ensure_does_not_use_to_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Static guard: ``_ensure`` source must not invoke asyncio.to_thread.

    This catches a future regression where someone re-introduces the
    ThreadPoolExecutor wrap on the AsyncClient constructor — a pattern
    that's incompatible with grpc.aio.Channel.__init__.
    """
    import inspect

    src = inspect.getsource(real_client.RealTinkoffClient._ensure)
    assert "asyncio.to_thread" not in src, (
        "RealTinkoffClient._ensure must not use asyncio.to_thread — "
        "grpc.aio.Channel.__init__ needs a running event loop, which "
        "ThreadPoolExecutor workers do not have. Re-introducing this "
        "pattern regresses the universe_sync 'There is no current event "
        "loop in thread asyncio_0' bug."
    )
