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
        """Open the AsyncClient and cache the resulting AsyncServices."""
        if self._services is None:
            AsyncClient = getattr(self._sdk, "AsyncClient")
            self._client = AsyncClient(self._token, target=self._target)
            self._services = await self._client.__aenter__()
        return self._services

    async def _reset_channel(self) -> None:
        """Tear down the current gRPC channel so the next RPC rebuilds it.

        Called after a timeout: a hung HTTP/2 connection will never
        recover, so we close the AsyncClient and drop our cached
        ``_services`` handle. The next call to ``_ensure()`` opens a
        fresh channel.
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

    async def _call(self, label: str, awaitable: Any) -> Any:
        """Wrap an SDK RPC coroutine in ``asyncio.wait_for``.

        ``label`` is used for the timeout error message and structured
        log so operators can tell which call (e.g. ``users.get_accounts``)
        is the one that's stuck. We deliberately do NOT swallow the
        ``TimeoutError`` — the caller (worker loop) needs to know the
        call didn't succeed. After the timeout we reset the channel so
        the next RPC gets a clean HTTP/2 connection.
        """
        try:
            return await asyncio.wait_for(
                awaitable, timeout=self._request_timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                "tinkoff_rpc_timeout",
                extra={
                    "label": label,
                    "timeout_s": self._request_timeout,
                },
            )
            await self._reset_channel()
            raise RealClientTimeoutError(label, self._request_timeout) from None

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
        services = await self._ensure()
        response = await self._call(
            "users.get_accounts", services.users.get_accounts()
        )
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
        services = await self._ensure()
        instrument_status = getattr(
            self._sdk.InstrumentStatus, "INSTRUMENT_STATUS_BASE"
        )
        response = await self._call(
            f"instruments.{method_name}",
            getattr(services.instruments, method_name)(
                instrument_status=instrument_status,
            ),
        )
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
        services = await self._ensure()
        CandleInterval = getattr(self._sdk, "CandleInterval")
        interval_enum = getattr(CandleInterval, interval, CandleInterval.CANDLE_INTERVAL_DAY)

        # SDK expects datetime objects for from_/to, not ISO strings.
        # Our Protocol accepts both for ergonomics.
        response = await self._call(
            "market_data.get_candles",
            services.market_data.get_candles(
                instrument_id=figi,
                from_=_to_datetime(date_from),
                to=_to_datetime(date_to),
                interval=interval_enum,
            ),
        )
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
        services = await self._ensure()
        response = await self._call(
            "instruments.get_dividends",
            services.instruments.get_dividends(
                figi=figi,
                from_=_to_datetime(from_),
                to=_to_datetime(to),
                instrument_id=figi,
            ),
        )
        return [_dividend_to_dict(d, figi=figi) for d in response.dividends]