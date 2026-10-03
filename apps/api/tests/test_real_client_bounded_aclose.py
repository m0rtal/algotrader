"""Tests for the bounded per-FIGI client close in RealTinkoffClient.

Production context (review C2):
    ``_async_backfill_impl`` (``backfill.py``) awaits
    ``client.aclose()`` in a ``finally`` block so a slow / hung
    gRPC handshake cannot stall the per-FIGI loop. The original
    ``RealTinkoffClient.aclose`` awaited ``AsyncClient.__aexit__``
    directly with no timeout. On a poisoned HTTP/2 connection
    that never drains (live observation: one channel held the
    loop for >5 min before the daemon watchdog killed the
    worker), the entire per-FIGI close became unbounded.

    Fix contract: ``aclose()`` wraps the underlying
    ``__aexit__`` in ``asyncio.wait_for`` with
    ``BOND_CLIENT_CLOSE_TIMEOUT_SECONDS`` (module-level constant,
    default 5.0). A close that exceeds the timeout surfaces
    ``asyncio.TimeoutError`` (it does NOT swallow it). The
    production ``finally`` in ``backfill.py`` already catches
    ``Exception`` for cleanup, so the bounded timeout is
    classified as a close failure and the existing
    ``bond_depth_client_close_failed`` log records it.

Why the constant, not a hard-coded ``5.0``:
    Tests need a real ``asyncio.wait_for`` semantic — a
    cooperative-hanging ``__aexit__`` that exceeds the budget
    must trigger ``TimeoutError``. A hard-coded 5s budget
    would make the test slow and flaky in CI. The constant
    lets the test ``monkeypatch.setattr`` to a small value
    (0.01s) and assert the actual timeout behaviour with
    real ``asyncio.wait_for``, not a fake.

Scope:
    * normal: ``__aexit__`` returns fast → no timeout
    * empty: ``aclose()`` is a no-op when ``_client is None``
    * exception: ``__aexit__`` raises → exception is re-raised
      (the caller's ``except Exception`` in ``backfill.py``
      logs it; here we just confirm ``aclose`` is faithful
      and does not silently swallow)
    * cancel: a pending ``aclose`` is awaitable and respects
      ``asyncio.CancelledError`` from the event loop
    * close-exception: the production log path is exercised
      separately by ``test_backfill_bonds_client_lifecycle.py``
    * timeout-preserves-fetch-error: a bounded close timeout
      must NOT mask the original fetch exception (asserted via
      the existing ``test_async_backfill_impl_close_does_not_mask_fetch_error``)
    * writer-lock boundary: out of scope for this file
      (covered in ``test_backfill_bonds_client_lifecycle.py``)
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from algotrader_api.ingestion.real_client import RealTinkoffClient


# ─── helpers ────────────────────────────────────────────────────────


def _make_client_with_fake_sdk(*, aexit_behavior: str = "fast"):
    """Build a RealTinkoffClient that bypasses SDK import.

    Uses ``__new__`` and sets the private fields directly so the
    test can run without ``t-tech-investments`` installed. Returns
    ``(client, fake_inner_client)`` where ``fake_inner_client``
    is the stand-in for ``self._client`` (its ``__aexit__`` is
    configurable via ``aexit_behavior``):

      * "fast" — returns immediately
      * "raise" — raises RuntimeError on ``__aexit__``
      * "hang"  — awaits forever (used to drive a real timeout)
    """
    client = RealTinkoffClient.__new__(RealTinkoffClient)
    client._token = "stub_token"  # noqa: SLF001
    client._target = "stub:0"  # noqa: SLF001
    client._request_timeout = 30.0  # noqa: SLF001
    client._client = None  # noqa: SLF001
    client._services = None  # noqa: SLF001

    if aexit_behavior == "fast":

        async def aexit_fast(*_a):
            return None

        _aexit = aexit_fast

    elif aexit_behavior == "raise":

        async def aexit_raise(*_a):
            raise RuntimeError("synthetic aexit failure")

        _aexit = aexit_raise

    elif aexit_behavior == "hang":

        async def aexit_hang(*_a):
            # Cooperative-hanging close: never returns unless
            # cancelled. The bounded wait_for must cut it off.
            await asyncio.sleep(3600)

        _aexit = aexit_hang

    else:
        raise ValueError(f"unknown aexit_behavior: {aexit_behavior!r}")

    fake_inner = MagicMock()
    fake_inner.__aexit__ = _aexit
    client._client = fake_inner  # noqa: SLF001
    client._sdk = MagicMock()  # noqa: SLF001
    return client, fake_inner


# ─── RED tests ──────────────────────────────────────────────────────


def test_aclose_module_constant_exists_and_is_positive():
    """The close timeout MUST live as a module constant.

    Hard-coding ``5.0`` in the function body would make the
    timeout unfakeable in tests (CI would have to wait 5s for
    every slow-close test). The constant lets production tune
    it without code edits and lets tests drive a small budget
    with ``monkeypatch.setattr``.
    """
    from algotrader_api.ingestion import real_client

    assert hasattr(real_client, "BOND_CLIENT_CLOSE_TIMEOUT_SECONDS"), (
        "real_client must expose BOND_CLIENT_CLOSE_TIMEOUT_SECONDS"
    )
    value = real_client.BOND_CLIENT_CLOSE_TIMEOUT_SECONDS
    assert isinstance(value, (int, float))
    assert value > 0, f"close timeout must be > 0; got {value}"


def test_aclose_returns_quickly_on_normal_path(monkeypatch):
    """Normal close: ``__aexit__`` returns fast → ``aclose``
    completes well under the budget; no exception raised.
    """
    from algotrader_api.ingestion import real_client

    # 0.01s budget: the fast path returns in microseconds.
    monkeypatch.setattr(
        real_client, "BOND_CLIENT_CLOSE_TIMEOUT_SECONDS", 0.01,
    )
    client, _ = _make_client_with_fake_sdk(aexit_behavior="fast")

    # Must complete without exception.
    asyncio.run(client.aclose())
    # And the inner handle is cleared so a re-``_ensure`` rebuilds.
    assert client._client is None  # noqa: SLF001
    assert client._services is None  # noqa: SLF001


def test_aclose_is_safe_without_open(monkeypatch):
    """``aclose()`` is a no-op when ``_client is None`` — the
    first call before any ``__aenter__`` must not raise.
    """
    from algotrader_api.ingestion import real_client

    monkeypatch.setattr(
        real_client, "BOND_CLIENT_CLOSE_TIMEOUT_SECONDS", 0.01,
    )
    client, _ = _make_client_with_fake_sdk(aexit_behavior="fast")
    client._client = None  # noqa: SLF001

    # No exception. The bounded wait_for must NOT be entered on
    # the None path — the wait_for contract applies to the
    # ``__aexit__`` awaitable only.
    asyncio.run(client.aclose())


def test_aclose_propagates_inner_exception(monkeypatch):
    """If ``__aexit__`` itself raises, ``aclose`` re-raises it
    faithfully (the caller's ``except Exception`` in
    ``backfill.py`` logs the failure). The bounded wait_for
    must NOT swallow the original exception.
    """
    from algotrader_api.ingestion import real_client

    monkeypatch.setattr(
        real_client, "BOND_CLIENT_CLOSE_TIMEOUT_SECONDS", 0.01,
    )
    client, _ = _make_client_with_fake_sdk(aexit_behavior="raise")

    with pytest.raises(RuntimeError, match="synthetic aexit failure"):
        asyncio.run(client.aclose())


def test_aclose_times_out_on_hanging_exit(monkeypatch):
    """Cooperative-hanging ``__aexit__`` MUST trigger
    ``asyncio.TimeoutError`` after the budget. This is the key
    C2 invariant: a poisoned HTTP/2 close handshake must not
    stall the per-FIGI loop indefinitely.
    """
    import time as _time

    from algotrader_api.ingestion import real_client

    # Tiny budget: 0.01s. A hang for 3600s exceeds it instantly.
    monkeypatch.setattr(
        real_client, "BOND_CLIENT_CLOSE_TIMEOUT_SECONDS", 0.01,
    )
    client, _ = _make_client_with_fake_sdk(aexit_behavior="hang")

    started = _time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(client.aclose())
    elapsed = _time.monotonic() - started
    # Sanity: the test should fail fast, not after the 3600s hang.
    # We allow a generous wall-clock budget for CI jitter; the
    # hard upper bound is "way under a second".
    assert elapsed < 2.0, (
        f"aclose must time out within the budget; "
        f"elapsed={elapsed:.3f}s suggests the timeout did not fire"
    )


def test_aclose_timeout_does_not_swallow_cancellation(monkeypatch):
    """If the OUTER event loop cancels the awaiting coroutine
    (not the inner ``__aexit__``), ``aclose`` must propagate
    ``asyncio.CancelledError`` cleanly. The bounded
    ``asyncio.wait_for`` wrapper cancels its inner task on
    outer cancellation; the cancellation must surface.
    """
    from algotrader_api.ingestion import real_client

    monkeypatch.setattr(
        real_client, "BOND_CLIENT_CLOSE_TIMEOUT_SECONDS", 0.5,
    )
    client, _ = _make_client_with_fake_sdk(aexit_behavior="hang")

    async def _drive():
        task = asyncio.create_task(client.aclose())
        await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return "cancelled"
        return "completed"

    result = asyncio.run(_drive())
    assert result == "cancelled", (
        f"outer cancel must propagate; got {result!r}"
    )


def test_aclose_uses_real_wait_for_not_a_local_const(monkeypatch):
    """Ponytail sanity: the timeout MUST be the module
    constant. A review that wraps the call in
    ``asyncio.wait_for(..., timeout=5.0)`` (hard-coded) would
    make this test fail because the monkeypatched 0.01s budget
    would not be honoured.
    """
    import inspect

    from algotrader_api.ingestion import real_client

    monkeypatch.setattr(
        real_client, "BOND_CLIENT_CLOSE_TIMEOUT_SECONDS", 0.01,
    )
    client, _ = _make_client_with_fake_sdk(aexit_behavior="hang")

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(client.aclose())

    # Belt-and-braces: the source must reference the module
    # constant (not a hard-coded 5.0) for the close timeout.
    src = inspect.getsource(real_client.RealTinkoffClient.aclose)
    assert "BOND_CLIENT_CLOSE_TIMEOUT_SECONDS" in src, (
        "aclose must reference the module constant; "
        f"got source:\n{src}"
    )
