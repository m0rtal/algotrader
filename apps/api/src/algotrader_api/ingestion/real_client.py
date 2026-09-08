"""Real Tinkoff client wrapping t-tech-investments AsyncClient.

The SDK is imported lazily so unit tests don't need the package installed.
Production deployment installs it from T-Bank's GitLab PyPI mirror — see
add-ttech-real-client/proposal.md for the exact mirror URL.

Wrapper responsibilities are kept narrow on purpose:
- Lazy import of `t_tech.invest` (PyPI name uses dashes; Python module uses
  underscores — pip's standard normalisation).
- Map our Protocol's `target="sandbox"|"production"` string to the SDK's
  gRPC host constants `INVEST_GRPC_API_SANDBOX` / `INVEST_GRPC_API`.
- Lazy-construct `AsyncClient` on the first async call (token is sensitive,
  no need to open a channel at import time).
- Hand every RPC call through with explicit request objects (the new SDK
  uses `GetAccountsRequest()` etc., not kwargs).
- Convert each gRPC response to plain dicts via `real_client_convert.py`
  so downstream `universe.upsert_instruments` and `bars.run_bars_phase`
  don't depend on the SDK type hierarchy.
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
    _to_iso,
)

logger = get_logger("algotrader_api.ingestion.real_client")


class RealTinkoffClient:
    """Wraps t_tech.invest.AsyncClient with our Protocol interface."""

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
        self._client: Any = None  # AsyncClient, opened lazily on first async call

    async def _ensure(self) -> Any:
        if self._client is None:
            AsyncClient = getattr(self._sdk, "AsyncClient")
            self._client = AsyncClient(self._token, target=self._target)
        return self._client

    async def get_accounts(self) -> list[dict]:
        client = await self._ensure()
        request_cls = getattr(self._sdk, "GetAccountsRequest")
        response = await client.users.get_accounts(request_cls())
        return [_acct_to_dict(a) for a in response.accounts]

    async def _instruments(
        self,
        method_name: str,
        instrument_type_attr: str,
        converter: Any,
    ) -> list[dict]:
        """Shared body for get_shares/bonds/etfs/futures/options.

        The new SDK exposes a single `instruments.<method>` per asset class
        and discriminates with `InstrumentType.{SHARES,BONDS,...}` on the
        `InstrumentsRequest`. Each wrapper sets the right
        `instrument_type` enum and runs the gRPC call.
        """
        client = await self._ensure()
        request_cls = getattr(self._sdk, "InstrumentsRequest")
        instrument_status = getattr(
            self._sdk.InstrumentStatus, "INSTRUMENT_STATUS_BASE"
        )
        instrument_type = getattr(self._sdk.InstrumentType, instrument_type_attr)
        response = await getattr(client.instruments, method_name)(
            request_cls(
                instrument_status=instrument_status,
                instrument_type=instrument_type,
            )
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
            "futures", "INSTRUMENT_TYPE_FUTURE", _future_to_dict
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
        client = await self._ensure()
        request_cls = getattr(self._sdk, "GetCandlesRequest")
        CandleInterval = getattr(self._sdk, "CandleInterval")
        interval_enum = getattr(CandleInterval, interval, CandleInterval.CANDLE_INTERVAL_DAY)

        response = await client.market_data.get_candles(
            request_cls(
                instrument_id=figi,
                from_=_to_iso(date_from),
                to=_to_iso(date_to),
                interval=interval_enum,
            )
        )
        return [_candle_to_dict(c) for c in response.candles]

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
