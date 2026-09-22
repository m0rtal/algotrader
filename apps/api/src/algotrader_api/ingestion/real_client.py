"""Real Tinkoff client wrapping t-tech-investments AsyncClient.

The SDK is imported lazily so unit tests don't need the package installed.
Production deployment installs it from T-Bank's GitLab PyPI mirror — see
add-ttech-real-client/proposal.md for the exact mirror URL.

Wrapper responsibilities are kept narrow on purpose:
- Lazy import of `t_tech.invest` (PyPI name uses dashes; Python module uses
  underscores — pip's standard normalisation).
- Map our Protocol's `target="sandbox"|"production"` string to the SDK's
  gRPC host constants `INVEST_GRPC_API_SANDBOX` / `INVEST_GRPC_API`.
- Lazy-open the gRPC channel via `await AsyncClient.__aenter__()`; the
  return value is an `AsyncServices` object which exposes `.users`,
  `.instruments`, `.market_data` etc. — that's the object we route
  every RPC through.
- Hand every RPC call through with explicit request objects (the new SDK
  uses `GetAccountsRequest()` etc., not kwargs).
- Convert each gRPC response to plain dicts via `real_client_convert.py`
  so downstream `universe.upsert_instruments` and `bars.run_bars_phase`
  don't depend on the SDK type hierarchy.

Why the __aenter__ dance: `AsyncClient(token, target=...)` only stores
config; it doesn't open the gRPC channel until you call `__aenter__()`
on it, and the return value is `AsyncServices`, not the client itself.
Trying to call `client.instruments.shares(...)` directly raises
`AttributeError` because the bare AsyncClient has no service attributes
— they're added during `__aenter__` by `services.Services(...)`.

Why every RPC is wrapped in `asyncio.wait_for`: gRPC channels over
HTTP/2 can reach the "TCP ESTABLISHED but no data flows" state on
cellular / hostile networks (observed with 85.118.181.24:443 and
178.130.128.33:443). Without a per-call timeout the worker blocks
forever on the first RPC and the daemon heartbeat (a separate thread)
keeps the watchdog at bay, so supervisor kills+restarts the worker in
a tight loop (~30/min). The timeout here is enforced at the wrapper
layer (not in the vendored SDK) and, on fire, the channel is closed
and the next call rebuilds it — a poisoned HTTP/2 connection is not
salvageable by retry.

Why we also retry on gRPC UNAVAILABLE: live logs show every Tinkoff
chunk fails with ``(<StatusCode.UNAVAILABLE: (14, 'unavailable')>,
'failed to connect to all addresses; last error: UNAVAILABLE:
ipv4:178.130.128.33:443: Handshake read failed (recvmsg:Connection
reset by peer (104))')``. UNAVAILABLE is a transient peer/network
condition — the next attempt on a fresh HTTP/2 connection typically
succeeds. We rebuild the channel between attempts (see
``_reset_channel``) because retrying over the same poisoned socket
just re-fails.

Why each public method uses an async factory that re-calls ``_ensure``
on every attempt: after ``_reset_channel`` has torn down the cached
``_services`` handle, the only path to a fresh HTTP/2 connection is
``_ensure()`` (it sees ``_services is None`` and constructs a new
``AsyncClient``). A closure that captured the services object before
the reset would silently keep using the dead channel. Calling
``_ensure()`` from inside the factory on every attempt keeps the
retry path honest.
"""
from __future__ import annotations

import asyncio
import importlib
from datetime import date
from typing import Any

from ..observability.logging import get_logger
from .real_client_convert import (
    _acct_to_dict,
    _bond_to_dict,
    _candle_to_dict,
    _dividend_to_dict,
    _etf_to_dict,
    _future_to_dict,
    _option_to_dict,
    _share_to_dict,
    _to_datetime,
)

logger = get_logger("algotrader_api.ingestion.real_client")

# Default per-RPC timeout in seconds. Override per-instance via the
# ``request_timeout`` constructor kwarg.
DEFAULT_REQUEST_TIMEOUT: float = 30.0

# Retry policy for the Tinkoff SDK call layer on gRPC UNAVAILABLE
# ("Connection reset by peer", "failed to connect to all addresses").
# 3 total attempts (1 initial + up to 2 retries) with exponential
# backoff [1s, 2s, 4s] — the 3rd entry is unused at 3 attempts but
# keeps the schedule complete for future relaxation. Channel is
# rebuilt between attempts (see ``_reset_channel``).
RETRY_ON_UNAVAILABLE_ATTEMPTS: int = 3
RETRY_ON_UNAVAILABLE_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 2.0, 4.0)


class RealClientTimeoutError(asyncio.TimeoutError):
    """Raised when an SDK RPC exceeds ``request_timeout`` seconds.

    Subclasses ``asyncio.TimeoutError`` so callers that catch the
    stdlib base class keep working; the dedicated subclass lets the
    worker / supervisor distinguish "real Tinkoff outage" from
    "HTTP/2 connection stuck" and trigger the channel-rebuild path.
    """

    def __init__(self, label: str, timeout: float) -> None:
        super().__init__(
            f"Tinkoff RPC {label!r} exceeded {timeout:.1f}s; rebuilding channel"
        )
        self.label = label
        self.timeout = timeout


class RealClientUnavailableError(Exception):
    """Raised after retries are exhausted on gRPC UNAVAILABLE.

    Distinct from :class:`RealClientTimeoutError`: UNAVAILABLE is a
    peer-side / network-level gRPC status (HTTP/2 RST_STREAM,
    "failed to connect to all addresses", "Connection reset by peer")
    that the SDK surfaces as ``t_tech.invest.exceptions.AioRequestError``
    with ``code == StatusCode.UNAVAILABLE``. We retry on it
    (channel rebuild + exponential backoff) before giving up, because
    in practice the Tinkoff sandbox/prod fleet has transient flakiness
    where the next attempt on a fresh HTTP/2 connection succeeds.

    Not a subclass of ``asyncio.TimeoutError`` — the failure mode is
    different and operator alerting keys off the type.
    """

    def __init__(self, label: str, attempts: int, details: str) -> None:
        super().__init__(
            f"Tinkoff RPC {label!r} failed with UNAVAILABLE after "
            f"{attempts} attempts: {details}"
        )
        self.label = label
        self.attempts = attempts
        self.details = details


class RealTinkoffClient:
    """Wraps t_tech.invest.AsyncClient + AsyncServices with our Protocol interface."""

    def __init__(
        self,
        *,
        token: str,
        target: str = "sandbox",
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        if request_timeout <= 0:
            raise ValueError(
                f"request_timeout must be > 0 seconds (got {request_timeout})"
            )
        try:
            self._sdk = importlib.import_module("t_tech.invest")
        except ImportError as e:
            raise RuntimeError(
                "t-tech-investments SDK not installed. "
                "Install from the tbank PyPI mirror or set ALGOTRADER_INGEST_FAKE=1 for dev."
            ) from e

        constants = importlib.import_module("t_tech.invest.constants")
        if target == "production":
            target_constant = constants.INVEST_GRPC_API
        elif target == "sandbox":
            target_constant = constants.INVEST_GRPC_API_SANDBOX
        else:
            raise ValueError(f"unknown target: {target} (use 'sandbox' or 'production')")
        self._target = target_constant
        self._token = token
        self._request_timeout = float(request_timeout)
        # AsyncClient is the entrypoint; constructor just stores config.
        # `__aenter__()` returns AsyncServices which has `.users`,
        # `.instruments`, `.market_data`. We cache it for the lifetime
        # of the wrapper; aclose() closes the channel.
        self._client: Any = None
        self._services: Any = None

    @property
    def request_timeout(self) -> float:
        """Current per-RPC timeout in seconds (read-only)."""
        return self._request_timeout

    async def _ensure(self) -> Any:
        """Open the AsyncClient and cache the resulting AsyncServices.

        The HTTP/2 handshake inside ``client.__aenter__()`` can hang
        indefinitely against a stuck/rate-limited Tinkoff endpoint.
        Per-RPC timeouts (PR #97) only fire after the channel is
        open, so we wrap ``__aenter__`` in ``asyncio.wait_for``
        here.

        Raises ``RealClientTimeoutError`` on timeout; caller is
        expected to retry via the existing UNAVAILABLE retry loop.
        The sync ``AsyncClient(token, target=...)`` constructor is
        left unwrapped — in practice it only stores config and does
        not perform network I/O, so it cannot hang.
        """
        if self._services is None:
            AsyncClient = getattr(self._sdk, "AsyncClient")
            self._client = AsyncClient(self._token, target=self._target)
            try:
                self._services = await asyncio.wait_for(
                    self._client.__aenter__(),
                    timeout=self._request_timeout,
                )
            except asyncio.TimeoutError as exc:
                # Channel handshake timed out — drop the partial client.
                await self._safe_aexit(self._client)
                self._client = None
                raise RealClientTimeoutError(
                    "AsyncClient.__aenter__", self._request_timeout,
                ) from exc
        return self._services

    async def _safe_aexit(self, client: Any) -> None:
        """Best-effort close of a partially-constructed AsyncClient."""
        if client is None:
            return
        try:
            await asyncio.wait_for(
                client.__aexit__(None, None, None), timeout=5.0,
            )
        except BaseException:  # noqa: BLE001 — best-effort cleanup
            pass

    async def _reset_channel(self) -> None:
        """Tear down the current gRPC channel so the next RPC rebuilds it.

        Called after a timeout or an UNAVAILABLE retry. A poisoned
        HTTP/2 connection will not recover on its own, so we close
        the AsyncClient and drop our cached ``_services`` handle.
        The next call to ``_ensure()`` opens a fresh channel.
        """
        client = self._client
        self._client = None
        self._services = None
        if client is not None:
            try:
                await client.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                logger.warning(
                    "tinkoff_channel_close_failed",
                    extra={"err": repr(exc)},
                )

    @staticmethod
    def _is_unavailable(exc: BaseException) -> bool:
        """Return True iff ``exc`` carries a gRPC UNAVAILABLE status.

        The SDK wraps raw gRPC ``AioRpcError`` into
        ``t_tech.invest.exceptions.AioRequestError`` carrying the
        ``grpc.StatusCode`` on ``.code``. We compare against the
        canonical ``grpc.StatusCode.UNAVAILABLE`` enum when ``.code``
        is an instance of that exact type, otherwise fall back to a
        duck-typed check on ``code.name == "UNAVAILABLE"`` so tests
        and any future SDK variant can stub the shape without
        depending on the real enum.
        """
        code = getattr(exc, "code", None)
        if code is None:
            return False
        try:  # pragma: no cover — exercised only when grpc is installed
            from grpc import StatusCode  # type: ignore[import-not-found]

            if isinstance(code, StatusCode):
                return code == StatusCode.UNAVAILABLE
        except ImportError:
            pass
        return getattr(code, "name", None) == "UNAVAILABLE"

    async def _call(self, label: str, factory: Any) -> Any:
        """Wrap an SDK RPC coroutine factory in timeout + UNAVAILABLE retries.

        ``factory`` is a zero-argument async callable that returns the
        RPC result. It is invoked fresh on every attempt; retries call
        it again after a channel reset, which rebuilds the cached
        ``_services`` handle because the factory calls ``self._ensure``
        itself.

        ``label`` is used for log/exception messages so operators can
        tell which call (e.g. ``users.get_accounts``) is the one that
        is stuck or flapping.

        Failure handling
        ----------------

        * ``asyncio.TimeoutError`` — the per-RPC timeout fired; reset
          the channel (a hung HTTP/2 connection is never salvageable)
          and raise :class:`RealClientTimeoutError`. No retry: by the
          time we hit the timeout it's typically a persistent outage.

        * gRPC UNAVAILABLE (``AioRequestError.code == StatusCode.UNAVAILABLE``)
          — reset the channel, sleep with exponential backoff, and
          retry up to ``RETRY_ON_UNAVAILABLE_ATTEMPTS`` total times
          (default 3: initial + 2 retries). After exhausting retries
          raise :class:`RealClientUnavailableError` with the last
          error's details. The retry+channel-rebuild path is critical
          on observed "Connection reset by peer" transient blips.

        * Any other exception — propagate untouched.
        """
        attempt = 0
        while True:
            attempt += 1
            try:
                return await asyncio.wait_for(
                    factory(), timeout=self._request_timeout
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "tinkoff_rpc_timeout",
                    extra={
                        "label": label,
                        "timeout_s": self._request_timeout,
                        "attempt": attempt,
                    },
                )
                await self._reset_channel()
                raise RealClientTimeoutError(label, self._request_timeout) from None
            except BaseException as exc:  # noqa: BLE001 — see ``_is_unavailable``
                if not self._is_unavailable(exc):
                    raise
                details = (
                    getattr(exc, "details", None)
                    or getattr(getattr(exc, "code", None), "name", "UNAVAILABLE")
                )
                if attempt >= RETRY_ON_UNAVAILABLE_ATTEMPTS:
                    logger.error(
                        "tinkoff_rpc_unavailable_exhausted",
                        extra={
                            "label": label,
                            "attempts": attempt,
                            "details": details,
                        },
                    )
                    raise RealClientUnavailableError(label, attempt, details) from exc
                backoff = RETRY_ON_UNAVAILABLE_BACKOFF_SECONDS[
                    min(attempt - 1, len(RETRY_ON_UNAVAILABLE_BACKOFF_SECONDS) - 1)
                ]
                logger.warning(
                    "tinkoff_rpc_unavailable_retry",
                    extra={
                        "label": label,
                        "attempt": attempt,
                        "next_attempt_in_s": backoff,
                        "details": details,
                    },
                )
                await self._reset_channel()
                await asyncio.sleep(backoff)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None
            self._services = None

    async def __aenter__(self) -> "RealTinkoffClient":
        # Trigger lazy open of the gRPC channel so the first RPC works.
        await self._ensure()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def get_accounts(self) -> list[dict]:
        async def factory() -> Any:
            services = await self._ensure()
            return await services.users.get_accounts()

        response = await self._call("users.get_accounts", factory)
        return [_acct_to_dict(a) for a in response.accounts]

    async def _instruments(
        self,
        method_name: str,
        instrument_type_attr: str,
        converter: Any,
    ) -> list[dict]:
        """Shared body for get_shares/bonds/etfs/futures/options.

        Each method on the new SDK's `InstrumentsService` (e.g. `shares()`)
        returns only that asset class — there's no `instrument_type`
        discriminator on `InstrumentsRequest`. So we just pass
        `instrument_status=INSTRUMENT_STATUS_BASE` and let the SDK
        return the right slice. The `instrument_type_attr` argument is
        kept for interface symmetry / future-proofing but ignored.
        """
        instrument_status = getattr(
            self._sdk.InstrumentStatus, "INSTRUMENT_STATUS_BASE"
        )
        method_attr = method_name

        async def factory() -> Any:
            services = await self._ensure()
            return await getattr(services.instruments, method_attr)(
                instrument_status=instrument_status,
            )

        response = await self._call(f"instruments.{method_name}", factory)
        return [converter(i) for i in response.instruments]

    async def get_shares(self) -> list[dict]:
        return await self._instruments(
            "shares", "INSTRUMENT_TYPE_SHARE", _share_to_dict
        )

    async def get_bonds(self) -> list[dict]:
        return await self._instruments(
            "bonds", "INSTRUMENT_TYPE_BOND", _bond_to_dict
        )

    async def get_etfs(self) -> list[dict]:
        return await self._instruments(
            "etfs", "INSTRUMENT_TYPE_ETF", _etf_to_dict
        )

    async def get_futures(self) -> list[dict]:
        return await self._instruments(
            "futures", "INSTRUMENT_TYPE_FUTURES", _future_to_dict
        )

    async def get_options(self) -> list[dict]:
        return await self._instruments(
            "options", "INSTRUMENT_TYPE_OPTION", _option_to_dict
        )

    async def get_candles(
        self,
        *,
        figi: str,
        date_from: str | date,
        date_to: str | date,
        interval: str = "CANDLE_INTERVAL_DAY",
    ) -> list[dict]:
        CandleInterval = getattr(self._sdk, "CandleInterval")
        interval_enum = getattr(CandleInterval, interval, CandleInterval.CANDLE_INTERVAL_DAY)

        # SDK expects datetime objects for from_/to, not ISO strings.
        # Our Protocol accepts both for ergonomics.
        async def factory() -> Any:
            services = await self._ensure()
            return await services.market_data.get_candles(
                instrument_id=figi,
                from_=_to_datetime(date_from),
                to=_to_datetime(date_to),
                interval=interval_enum,
            )

        response = await self._call("market_data.get_candles", factory)
        return [_candle_to_dict(c) for c in response.candles]

    async def get_dividends(
        self,
        figi: str,
        from_: Any,
        to: Any,
    ) -> list[dict]:
        """Fetch dividend events for ``figi`` between ``from_`` and ``to``.

        Thin wrapper around ``InstrumentsService.get_dividends``; the SDK
        takes the figi, an optional date range and an optional
        ``instrument_id`` and returns ``GetDividendsResponse(dividends=[...])``.
        We pass the figi as both ``figi`` and ``instrument_id`` because the
        proto accepts either and the SDK has been observed to return empty
        when only one is supplied on some sandbox builds.
        """
        async def factory() -> Any:
            services = await self._ensure()
            return await services.instruments.get_dividends(
                figi=figi,
                from_=_to_datetime(from_),
                to=_to_datetime(to),
                instrument_id=figi,
            )

        response = await self._call("instruments.get_dividends", factory)
        return [_dividend_to_dict(d, figi=figi) for d in response.dividends]
