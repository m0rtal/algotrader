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
        asyncio.get_running_loop()


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

    async def __aenter__(self) -> Any:
        # Mirror AsyncClient.__aenter__: short async handshake.
        await asyncio.sleep(0)
        return SimpleNamespace(stub=True)

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _patch_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``RealTinkoffClient`` use our event-loop-needing stub."""

    def _fake_load_sdk() -> Any:
        return SimpleNamespace(AsyncClient=_AsyncClientNeedingLoop)

    # ``RealTinkoffClient`` resolves the SDK lazily via ``self._sdk``;
    # patch the loader so the test sees our stub.
    monkeypatch.setattr(real_client, "load_sdk", _fake_load_sdk, raising=False)
    monkeypatch.setattr(real_client, "_LOAD_SDK", _fake_load_sdk, raising=False)


def test_ensure_works_inside_running_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """The actual regression: ``_ensure`` must succeed when awaited.

    Without the fix, ``AsyncClient.__init__`` runs in a
    ``ThreadPoolExecutor`` worker that has no event loop. The
    constructor's call to ``asyncio.get_running_loop()`` (mirroring
    ``grpc.aio.Channel.__init__``) raises ``RuntimeError: There is
    no current event loop in thread 'asyncio_0'``.
    """
    _patch_sdk(monkeypatch)
    client = real_client.RealTinkoffClient(token="fake", target="sandbox")

    async def _drive() -> None:
        services = await client._ensure()
        # ``_ensure`` returns whatever ``__aenter__`` yields; we just
        # need it to not raise and not be None.
        assert services is not None, (
            "_ensure must return the AsyncServices from __aenter__"
        )

    asyncio.run(_drive())


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
