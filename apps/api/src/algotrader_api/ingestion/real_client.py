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
"""
from __future__ import annotations

import importlib
from datetime import date
from typing import Any

from ..observability.logging import get_logger
from .real_client_convert import (
    _acct_to_dict,
    _bond_to_dict,
    _candle_to_dict,
    _etf_to_dict,
    _future_to_dict,
    _option_to_dict,
    _share_to_dict,
    _to_datetime,
)

logger = get_logger("algotrader_api.ingestion.real_client")


class RealTinkoffClient:
    """Wraps t_tech.invest.AsyncClient + AsyncServices with our Protocol interface."""

    def __init__(self, *, token: str, target: str = "sandbox") -> None:
        try:
            self._sdk = importlib.import_module("t_tech.invest")
        except ImportError as e:
            raise RuntimeError(
                "t-tech-investments SDK not installed. "
                "Install from the tbank PyPI mirror or set ALGOTRADER_INGEST_FAKE=1 for dev."
            ) from e

        constants = importlib.import_module("t_tech.invest.constants")
        target_constant = getattr(constants, f"INVEST_GRPC_API_{target.upper()}", None)
        if target_constant is None:
            raise ValueError(f"unknown target: {target} (use 'sandbox' or 'production')")
        self._target = target_constant
        self._token = token
        # AsyncClient is the entrypoint; constructor just stores config.
        # `__aenter__()` returns AsyncServices which has `.users`,
        # `.instruments`, `.market_data`. We cache it for the lifetime
        # of the wrapper; aclose() closes the channel.
        self._client: Any = None
        self._services: Any = None

    async def _ensure(self) -> Any:
        """Open the AsyncClient and cache the resulting AsyncServices."""
        if self._services is None:
            AsyncClient = getattr(self._sdk, "AsyncClient")
            self._client = AsyncClient(self._token, target=self._target)
            self._services = await self._client.__aenter__()
        return self._services

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None
            self._services = None

    async def get_accounts(self) -> list[dict]:
        services = await self._ensure()
        response = await services.users.get_accounts()
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
        response = await getattr(services.instruments, method_name)(
            instrument_status=instrument_status,
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
        response = await services.market_data.get_candles(
            instrument_id=figi,
            from_=_to_datetime(date_from),
            to=_to_datetime(date_to),
            interval=interval_enum,
        )
        return [_candle_to_dict(c) for c in response.candles]
