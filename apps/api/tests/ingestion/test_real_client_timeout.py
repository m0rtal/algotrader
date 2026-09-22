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
- gRPC ``UNAVAILABLE`` errors (``t_tech.invest.exceptions.AioRequestError``
  with ``code == grpc.StatusCode.UNAVAILABLE`` — observed in production
  as "Connection reset by peer" / "failed to connect to all addresses")
  trigger a retry sequence: channel rebuild + exponential backoff, up
  to ``RETRY_ON_UNAVAILABLE_ATTEMPTS`` (3) total attempts. Recovery on
  a later attempt returns the result; exhaustion raises
  ``RealClientUnavailableError`` (a separate class from
  ``RealClientTimeoutError``) carrying the label and last error.
- Non-UNAVAILABLE errors from the SDK are propagated without retry.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from algotrader_api.ingestion.real_client import (
    DEFAULT_REQUEST_TIMEOUT,
    RETRY_ON_UNAVAILABLE_ATTEMPTS,
    RETRY_ON_UNAVAILABLE_BACKOFF_SECONDS,
    RealClientTimeoutError,
    RealClientUnavailableError,
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


def test_default_request_timeout_is_10_seconds() -> None:
    assert DEFAULT_REQUEST_TIMEOUT == 10.0


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


# ─── Retry-on-UNAVAILABLE behaviour ──────────────────────────────────


def test_retry_policy_constants_are_stable() -> None:
    """Guard against silent changes to the retry contract.

    The production runbook (Tinkoff SDK flakiness fix) calls out
    "3 attempts with exponential backoff [1s, 2s, 4s]". Operators
    tune alerting on the attempt count, so changes must be
    intentional and visible in code review.
    """
    assert RETRY_ON_UNAVAILABLE_ATTEMPTS == 3
    assert RETRY_ON_UNAVAILABLE_BACKOFF_SECONDS == (1.0, 2.0, 4.0)


def _patch_sdk_with(monkeypatch: pytest.MonkeyPatch, services: Any, client: Any) -> None:
    """Same shape as ``_patch_sdk`` but takes any services object.

    ``_patch_sdk`` (top of file) hard-codes ``_StubServices`` for the
    timeout tests; here we need services whose ``get_accounts`` can
    be scripted per-attempt (raise UNAVAILABLE, succeed, etc.).
    """
    fake_sdk = SimpleNamespace(AsyncClient=lambda token, target: client)
    monkeypatch.setattr(
        "algotrader_api.ingestion.real_client.importlib.import_module",
        lambda name: fake_sdk if name == "t_tech.invest" else SimpleNamespace(
            INVEST_GRPC_API_SANDBOX="sandbox-test",
        ),
    )


async def test_unavailable_triggers_retry_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two UNAVAILABLE blips followed by a successful call must return
    the third call's value with the channel being rebuilt between attempts.

    This is the core regression: live logs show every Tinkoff chunk
    failing with UNAVAILABLE / "Connection reset by peer" — we must
    retry on a fresh HTTP/2 connection rather than propagate the first
    error verbatim.
    """
    # Import here so a missing SDK in CI without t_tech still lets
    # the file collect (other tests don't need it).
    from grpc import StatusCode
    from t_tech.invest.exceptions import AioRequestError

    # Shrink backoff to a tick so this test finishes in <100ms.
    monkeypatch.setattr(
        "algotrader_api.ingestion.real_client.RETRY_ON_UNAVAILABLE_BACKOFF_SECONDS",
        (0.0, 0.0, 0.0),
    )

    call_count = 0

    class _FlakyServices:
        def __init__(self) -> None:
            self.users = SimpleNamespace(get_accounts=self._get_accounts)

        async def _get_accounts(self) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise AioRequestError(
                    StatusCode.UNAVAILABLE,
                    "failed to connect to all addresses; last error: UNAVAILABLE: "
                    "ipv4:178.130.128.33:443: Handshake read failed "
                    "(recvmsg:Connection reset by peer (104))",
                    None,
                )
            return SimpleNamespace(
                accounts=[
                    SimpleNamespace(
                        id="ACC-R",
                        name="Recovered",
                        type="ACCOUNT_TYPE_TINKOFF",
                        status="ACCOUNT_STATUS_OPEN",
                    )
                ]
            )

    services = _FlakyServices()
    client = _StubAsyncClient(services)  # type: ignore[arg-type]
    _patch_sdk_with(monkeypatch, services, client)

    wrapper = RealTinkoffClient(token="t", request_timeout=5.0)
    accounts = await wrapper.get_accounts()

    assert accounts == [
        {"id": "ACC-R", "name": "Recovered", "type": "ACCOUNT_TYPE_TINKOFF", "status": "ACCOUNT_STATUS_OPEN"}
    ]
    # 3 attempts: 2 UNAVAILABLE blips + 1 success.
    assert call_count == 3
    # Channel was rebuilt between attempts. Each retry invokes
    # ``_reset_channel`` first; on the final success the channel is
    # left open. ``close_count`` must equal the number of intermediate
    # failures (not the total attempt count).
    assert client.close_count == 2
    # Final state: channel reopened on each retry and left open at the
    # end → 3 opens total (initial + 2 rebuilds).
    assert client.open_count == 3


async def test_unavailable_exhausts_retries_and_raises_separate_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After 3 failed attempts the wrapper raises
    ``RealClientUnavailableError`` (NOT ``RealClientTimeoutError``)
    with the last error's details.

    The separate class is a constraint: the worker / supervisor use
    exception type to drive alerting and channel-recovery strategy.
    """
    from grpc import StatusCode
    from t_tech.invest.exceptions import AioRequestError

    monkeypatch.setattr(
        "algotrader_api.ingestion.real_client.RETRY_ON_UNAVAILABLE_BACKOFF_SECONDS",
        (0.0, 0.0, 0.0),
    )

    call_count = 0

    class _AlwaysUnavailable:
        def __init__(self) -> None:
            self.users = SimpleNamespace(get_accounts=self._get_accounts)

        async def _get_accounts(self) -> Any:
            nonlocal call_count
            call_count += 1
            raise AioRequestError(
                StatusCode.UNAVAILABLE,
                "failed to connect to all addresses; last error: UNAVAILABLE: "
                "ipv4:178.130.128.33:443: Handshake read failed "
                "(recvmsg:Connection reset by peer (104))",
                None,
            )

    services = _AlwaysUnavailable()
    client = _StubAsyncClient(services)  # type: ignore[arg-type]
    _patch_sdk_with(monkeypatch, services, client)

    wrapper = RealTinkoffClient(token="t", request_timeout=5.0)

    with pytest.raises(RealClientUnavailableError) as exc_info:
        await wrapper.get_accounts()

    assert exc_info.value.label == "users.get_accounts"
    assert exc_info.value.attempts == 3
    assert "Connection reset by peer" in exc_info.value.details
    # Distinct from the timeout path's exception class.
    assert not isinstance(exc_info.value, RealClientTimeoutError)
    assert not isinstance(exc_info.value, asyncio.TimeoutError)
    # Exactly 3 attempts (initial + 2 retries); no 4th call.
    assert call_count == 3
    # Channel rebuilt after each failed attempt — 2 channel resets in
    # total (one before each retry). The third attempt happens against
    # a freshly opened channel and fails, after which we give up.
    assert client.close_count == 2


async def test_non_unavailable_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``AioRequestError`` with a different status code must propagate
    immediately without invoking the retry loop. Only UNAVAILABLE
    benefits from the channel-rebuild dance.
    """
    from grpc import StatusCode
    from t_tech.invest.exceptions import AioRequestError

    call_count = 0

    class _NonUnavailable:
        def __init__(self) -> None:
            self.users = SimpleNamespace(get_accounts=self._get_accounts)

        async def _get_accounts(self) -> Any:
            nonlocal call_count
            call_count += 1
            raise AioRequestError(
                StatusCode.UNAUTHENTICATED,
                "invalid token",
                None,
            )

    services = _NonUnavailable()
    client = _StubAsyncClient(services)  # type: ignore[arg-type]
    _patch_sdk_with(monkeypatch, services, client)

    wrapper = RealTinkoffClient(token="t", request_timeout=5.0)

    with pytest.raises(AioRequestError):
        await wrapper.get_accounts()

    # Single attempt; no retry; channel NOT torn down (it wasn't broken).
    assert call_count == 1
    assert client.close_count == 0


def test_is_unavailable_recognises_real_grpc_status() -> None:
    """The detector must accept the actual ``grpc.StatusCode.UNAVAILABLE``
    and reject everything else, including non-StatusCode objects."""

    class _StubStatus:
        def __init__(self, name: str) -> None:
            self.name = name

    class _StubExc(Exception):
        def __init__(self, code: Any) -> None:
            self.code = code

    # Real grpc.StatusCode if installed; otherwise this test is a no-op.
    try:
        from grpc import StatusCode
    except ImportError:  # pragma: no cover — grpc is in the test venv
        pytest.skip("grpc not installed")

    assert RealTinkoffClient._is_unavailable(
        _StubExc(StatusCode.UNAVAILABLE)
    ) is True
    assert RealTinkoffClient._is_unavailable(
        _StubExc(StatusCode.UNAUTHENTICATED)
    ) is False
    # Exceptions without ``.code`` are not UNAVAILABLE.
    assert RealTinkoffClient._is_unavailable(ValueError("nope")) is False
    # Duck-typed fallback: anything exposing ``.name == "UNAVAILABLE"``
    # is treated as UNAVAILABLE even without grpc.
    assert (
        RealTinkoffClient._is_unavailable(_StubExc(_StubStatus("UNAVAILABLE")))
        is True
    )
    assert (
        RealTinkoffClient._is_unavailable(_StubExc(_StubStatus("INTERNAL")))
        is False
    )


# ----------------------------------------------------------------------------
# _ensure() aenter timeout coverage — fixes PR #99 regression
# ----------------------------------------------------------------------------


def test_ensure_times_out_on_slow_aenter(monkeypatch):
    """A blocking __aenter__ must be timed out, not awaited forever."""
    import asyncio as _asyncio

    client = RealTinkoffClient(token="t", target="sandbox", request_timeout=0.3)

    class _BlockingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            await _asyncio.sleep(2.0)  # would hang forever

        async def __aexit__(self, *args):
            return None

    class _FakeSDK:
        AsyncClient = _BlockingClient

    monkeypatch.setattr(client, "_sdk", _FakeSDK())

    async def run() -> None:
        with pytest.raises(RealClientTimeoutError) as ei:
            await client._ensure()
        assert ei.value.label == "AsyncClient.__aenter__"
        assert ei.value.timeout == 0.3
        # Partial client must be dropped, not cached.
        assert client._client is None

    _asyncio.run(run())
