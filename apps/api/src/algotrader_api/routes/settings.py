"""Settings routes: GET/PUT/DELETE /api/settings + PUT /api/settings/token."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..db.secrets import set_secret
from ..db.sqlite import execute
from ..observability.correlation import correlation_id
from ..observability.logging import get_logger
from ..observability.tracing import get_tracer
from ..schemas.api import (
    DEFAULT_SETTINGS,
    Settings,
    SettingsPutRequest,
    SettingsResponse,
)

router = APIRouter(prefix="/api", tags=["settings"])
logger = get_logger("algotrader_api.settings")
tracer = get_tracer("algotrader_api")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_version() -> str:
    return f"v1-{uuid.uuid4().hex[:8]}"


@router.get("/settings", response_model=SettingsResponse)
def get_settings() -> SettingsResponse:
    rows = execute(_get_sqlite_path(), "SELECT value, version FROM settings WHERE key = 'main'", ())
    if not rows:
        return SettingsResponse(
            values=Settings.model_validate(DEFAULT_SETTINGS),
            version="",
            updatedAt=_now_iso(),
        )
    value = json.loads(rows[0]["value"])
    return SettingsResponse(values=Settings.model_validate(value), version=rows[0]["version"], updatedAt=_now_iso())


@router.put("/settings", response_model=SettingsResponse)
async def put_settings(request: Request, body: SettingsPutRequest) -> SettingsResponse:
    with tracer.start_as_current_span("settings.put") as span:
        existing = execute(_get_sqlite_path(), "SELECT value, version FROM settings WHERE key = 'main'", ())
        existing_value = json.loads(existing[0]["value"]) if existing else None
        old_version = existing[0]["version"] if existing else ""
        client_version = body.version

        if existing and old_version != client_version:
            span.set_attribute("settings.version_old", old_version)
            span.set_attribute("settings.version_client", client_version)
            span.set_attribute("settings.section", "conflict")
            logger.warning(
                "settings.conflict",
                version_old=old_version,
                version_client=client_version,
                correlation_id=correlation_id(),
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "version_conflict",
                    "message": "settings were updated by another client",
                    "current": {"values": existing_value, "version": old_version},
                },
            )

        new_version = _gen_version()
        span.set_attribute("settings.version_old", old_version)
        span.set_attribute("settings.version_new", new_version)
        span.set_attribute("settings.section", "all")

        if existing:
            execute(
                _get_sqlite_path(),
                "UPDATE settings SET value = ?, version = ?, updated_at = CURRENT_TIMESTAMP WHERE key = 'main'",
                (json.dumps(body.values.model_dump()), new_version),
            )
        else:
            execute(
                _get_sqlite_path(),
                "INSERT INTO settings (key, value, version) VALUES (?, ?, ?)",
                ("main", json.dumps(body.values.model_dump()), new_version),
            )

        logger.info(
            "settings.put",
            version_old=old_version,
            version_new=new_version,
            correlation_id=correlation_id(),
        )
        return SettingsResponse(values=body.values, version=new_version, updatedAt=_now_iso())


@router.delete("/settings", status_code=204)
def delete_settings() -> None:
    with tracer.start_as_current_span("settings.delete") as span:
        execute(_get_sqlite_path(), "DELETE FROM settings WHERE key = 'main'", ())
        span.set_attribute("settings.section", "all")
        logger.info("settings.reset", correlation_id=correlation_id())


class TokenPutRequest(BaseModel):
    token: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    tokenLast4: str
    tokenRedacted: bool = True


@router.put("/settings/token", response_model=TokenResponse)
def put_settings_token(body: TokenPutRequest) -> TokenResponse:
    """Write the broker token. Persists in two places:

    1. `secrets.broker_token` — opaque store consumed by the worker.
    2. `settings.value.broker.tokenLast4` — UI-visible last-4 + a
       bumped row version so the Settings page knows to refresh.

    The full token never leaves the server; only the last-4 ever shows
    up in the UI. Splitting the writes here previously left the UI
    saying «Токен не задан» while the worker happily authenticated —
    fixed by updating both stores in this handler.
    """
    with tracer.start_as_current_span("settings.token.put") as span:
        token = body.token.strip()
        if not token:
            raise HTTPException(status_code=400, detail={"error": "empty_token"})
        last4 = token[-4:]
        try:
            set_secret(_get_sqlite_path(), "broker_token", token)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "settings.token.write_failed",
                error=str(exc),
                correlation_id=correlation_id(),
            )
            raise HTTPException(
                status_code=500,
                detail={"error": "token_write_failed", "message": str(exc)},
            ) from exc
        # Mirror last-4 + accountId into the structured settings row so
        # the UI stops claiming the token is unset.
        rows = execute(
            _get_sqlite_path(),
            "SELECT value, version FROM settings WHERE key = 'main'",
            (),
        )
        if rows:
            value = json.loads(rows[0]["value"])
            value.setdefault("broker", {})
            value["broker"]["environment"] = value["broker"].get("environment", "sandbox")
            value["broker"]["tokenLast4"] = last4
            value["broker"]["tokenRedacted"] = True
            new_version = _gen_version()
            execute(
                _get_sqlite_path(),
                "UPDATE settings SET value = ?, version = ?, updated_at = CURRENT_TIMESTAMP WHERE key = 'main'",
                (json.dumps(value), new_version),
            )
        else:
            value = Settings.model_validate(DEFAULT_SETTINGS).model_dump()
            value["broker"]["tokenLast4"] = last4
            value["broker"]["tokenRedacted"] = True
            new_version = _gen_version()
            execute(
                _get_sqlite_path(),
                "INSERT INTO settings (key, value, version) VALUES (?, ?, ?)",
                ("main", json.dumps(value), new_version),
            )
        span.set_attribute("settings.token_last4", last4)
        logger.info(
            "settings.token.put",
            token_last4=last4,
            correlation_id=correlation_id(),
        )
        return TokenResponse(tokenLast4=last4, tokenRedacted=True)


# SQLite path injected via app state — set in main.py lifespan
_sqlite_path_holder: dict[str, str] = {}


def set_sqlite_path(path: str) -> None:
    _sqlite_path_holder["path"] = path


def _get_sqlite_path() -> str:
    p = _sqlite_path_holder.get("path")
    if not p:
        raise RuntimeError("sqlite path not configured — call set_sqlite_path() in lifespan")
    return p
