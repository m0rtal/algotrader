"""Tinkoff Invest API client abstraction.

Defines a Protocol so we can swap a real client for a FakeTinkoffClient
in tests. The factory picks based on the ALGOTRADER_INGEST_FAKE env var.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..observability.logging import get_logger

logger = get_logger("algotrader_api.ingestion.client")

DEFAULT_TOKEN_PATH = "~/.hermes/secrets/tinkoff_token"


def read_token_file(path: str = DEFAULT_TOKEN_PATH) -> str | None:
    """Read the Tinkoff token from a file. Return None if unreadable.

    The token file should be owner-readable only (mode 0600). This function
    does NOT log the token value — only that the file was read.
    """
    expanded = Path(path).expanduser()
    if not expanded.exists():
        logger.warning("tinkoff.token.missing", path=str(expanded))
        return None
    if not os.access(expanded, os.R_OK):
        logger.warning("tinkoff.token.unreadable", path=str(expanded))
        return None
    try:
        token = expanded.read_text(encoding="utf-8").strip()
    except OSError as e:
        logger.warning("tinkoff.token.read_error", path=str(expanded), error=str(e))
        return None
    if not token:
        logger.warning("tinkoff.token.empty", path=str(expanded))
        return None
    logger.info("tinkoff.token.loaded", path=str(expanded), length=len(token))
    return token


@runtime_checkable
class TinkoffClient(Protocol):
    """Minimal interface we use from tinkoff.invest.AsyncClient.

    Defined as a Protocol so we can swap in a FakeTinkoffClient for unit tests
    without mocking the entire SDK surface.
    """

    async def get_accounts(self) -> list[dict]: ...

    async def get_shares(self) -> list[dict]: ...

    async def get_bonds(self) -> list[dict]: ...

    async def get_etfs(self) -> list[dict]: ...

    async def get_futures(self) -> list[dict]: ...

    async def get_options(self) -> list[dict]: ...

    async def get_candles(
        self,
        *,
        figi: str,
        date_from: str,
        date_to: str,
        interval: str = "CANDLE_INTERVAL_DAY",
    ) -> list[dict]: ...

    async def aclose(self) -> None: ...


def make_client(
    *,
    use_fake: bool | None = None,
    token_path: str = DEFAULT_TOKEN_PATH,
) -> TinkoffClient:
    """Factory: pick real or fake Tinkoff client.

    Selection rules:
    - use_fake=True → InMemoryTinkoffClient (tests)
    - use_fake=False → RealTinkoffClient (production)
    - use_fake=None → ALGOTRADER_INGEST_FAKE=1 → fake, else real
    """
    if use_fake is None:
        use_fake = os.environ.get("ALGOTRADER_INGEST_FAKE") == "1"
    if use_fake:
        from .fake_client import InMemoryTinkoffClient
        logger.info("tinkoff.client.fake")
        return InMemoryTinkoffClient()
    from .real_client import RealTinkoffClient
    token = read_token_file(token_path)
    if not token:
        raise RuntimeError(
            f"Tinkoff token not found at {token_path}. "
            "Set ALGOTRADER_INGEST_FAKE=1 for dev mode, or write token to file."
        )
    logger.info("tinkoff.client.real", token_path=token_path)
    return RealTinkoffClient(token=token)
