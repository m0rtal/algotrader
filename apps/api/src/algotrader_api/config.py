"""Pydantic settings loaded from env vars / .env."""
from __future__ import annotations

import json
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings. Env vars: ALGOTRADER_<FIELD_NAME> (uppercase)."""

    model_config = SettingsConfigDict(
        env_prefix="ALGOTRADER_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    data_dir: str = "./data"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_sample_health: float = 0.1
    # Data ingest defaults — overridable via ALGOTRADER_INGEST_* env vars (worker scope).
    history_years: int = 5
    fetch_disabled: bool = False
    synth_seed: bool = False

    cors_origins: str = '["http://localhost:5173","http://192.168.1.101:5173"]'

    # OpenTelemetry
    otel_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "algotrader-api"
    otel_resource_attributes: str = "service.version=0.1.0,deployment.environment=dev"

    @property
    def sqlite_path(self) -> str:
        return f"{self.data_dir}/state.db"

    @property
    def bars_dir(self) -> str:
        return f"{self.data_dir}/bars"

    @property
    def cors_origins_list(self) -> list[str]:
        try:
            return json.loads(self.cors_origins)
        except (json.JSONDecodeError, TypeError):
            return ["http://localhost:5173"]


def get_settings() -> Settings:
    return Settings()
