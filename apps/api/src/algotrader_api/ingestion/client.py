"""Tinkoff Invest API client abstraction.

Defines a Protocol so we can swap a real client for a FakeTinkoffClient
in tests. The factory picks based on the ALGOTRADER_INGEST_FAKE env var.

The broker token lives in the application SQLite database (secrets table)
— see db/secrets.py. Worker reads it via get_broker_token() on each run.
"""
from __future__ import annotations

import json
import os
from typing import Protocol, runtime_checkable

from ..db.secrets import get_broker_token
from ..observability.logging import get_logger

logger = get_logger("algotrader_api.ingestion.client")


def load_broker_token(sqlite_path: str | None = None) -> str | None:
    """Read the broker token from the application database.

    Returns None if the token row is empty or unreadable. Never logs the
    token value — only its presence and length.
    """
    if sqlite_path is None:
        sqlite_path = os.environ.get("ALGOTRADER_SQLITE_PATH", "")
    if not sqlite_path:
        logger.warning("tinkoff.token.sqlite_path_unset")
        return None
    try:
        token = get_broker_token(sqlite_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tinkoff.token.read_error", error=str(exc))
        return None
    if not token:
        logger.warning("tinkoff.token.empty")
        return None
    logger.info("tinkoff.token.loaded", length=len(token))
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
    sqlite_path: str | None = None,
    target: str | None = None,
) -> TinkoffClient:
    """Factory: pick real or fake Tinkoff client.

    Selection rules:
    - use_fake=True → InMemoryTinkoffClient (tests)
    - use_fake=False → RealTinkoffClient (production)
    - use_fake=None → ALGOTRADER_INGEST_FAKE=1 → fake, else real

    RealTinkoffClient reads the token from `db.secrets` via load_broker_token().

    Target resolution order (only relevant for real client):
    1. Explicit `target` argument wins.
    2. ALGOTRADER_TINKOFF_TARGET env var.
    3. BrokerSettings.environment (set via Settings → Broker).
    4. "sandbox" — safe default. Live orders require an explicit flip.
    """
    if use_fake is None:
        use_fake = os.environ.get("ALGOTRADER_INGEST_FAKE") == "1"
    if use_fake:
        from .fake_client import InMemoryTinkoffClient
        logger.info("tinkoff.client.fake")
        return InMemoryTinkoffClient()
    from .real_client import RealTinkoffClient
    token = load_broker_token(sqlite_path)
    if not token:
        raise RuntimeError(
            "Tinkoff broker token not set. Save it via Settings → Broker → "
            "Токен, or set ALGOTRADER_INGEST_FAKE=1 for dev mode."
        )
    if target is None:
        target = os.environ.get("ALGOTRADER_TINKOFF_TARGET")
    if target is None:
        try:
            from ..db.sqlite import execute as _exec
            from ..config import get_settings
            settings = get_settings()
            rows = _exec(
                settings.sqlite_path,
                "SELECT value FROM settings WHERE key = 'main'",
                (),
            )
            if rows:
                blob = json.loads(rows[0]["value"])
                broker = blob.get("broker", {}) if isinstance(blob, dict) else {}
                env_name = broker.get("environment")
                if env_name in ("sandbox", "production"):
                    target = env_name
        except Exception:
            target = None
    if target is None:
        target = "sandbox"
    logger.info("tinkoff.client.real", target=target)
    return RealTinkoffClient(token=token, target=target)
