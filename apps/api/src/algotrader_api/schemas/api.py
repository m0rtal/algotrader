"""Pydantic schemas mirroring @algotrader/shared Zod schemas for FastAPI.

Mirrors packages/shared/src/index.ts — keep in sync manually. Pydantic gives
us Python-side validation; the frontend continues to validate against Zod
on the same shape, ensuring contract parity without code generation.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BrokerEnv = Literal["sandbox", "live"]
RegimeFilter = Literal["trend", "range", "vol", "all"]
DataSource = Literal["tinkoff", "moex_iss", "file"]


class BrokerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment: BrokerEnv = "sandbox"
    tokenLast4: str = ""
    tokenRedacted: bool = True
    accountId: str = ""


class RiskSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    maxDrawdownPct: float = Field(default=10, ge=1, le=50)
    maxPositionSizePct: float = Field(default=20, ge=1, le=100)
    killSwitchEnabled: bool = False
    killSwitchThresholdPct: float = Field(default=15, ge=1, le=50)

    @model_validator(mode="after")
    def _check_threshold(self) -> "RiskSettings":
        if self.killSwitchThresholdPct < self.maxDrawdownPct:
            raise ValueError("killSwitchThresholdPct should be >= maxDrawdownPct")
        return self


class MLSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modelVersion: str = "v1"
    retrainIntervalDays: int = Field(default=30, ge=1, le=90)
    confidenceThreshold: float = Field(default=0.6, ge=0, le=1)
    regimeFilter: RegimeFilter = "all"


class DataSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: DataSource = "tinkoff"
    cacheTtlMinutes: int = Field(default=60, ge=1, le=1440)
    historyYears: int = Field(default=5, ge=1, le=10)
    autoFetch: bool = True


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    broker: BrokerSettings = Field(default_factory=BrokerSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    ml: MLSettings = Field(default_factory=MLSettings)
    data: DataSettings = Field(default_factory=DataSettings)


class SettingsResponse(BaseModel):
    values: Settings
    version: str
    updatedAt: str


class SettingsPutRequest(BaseModel):
    values: Settings
    version: str


DEFAULT_SETTINGS = Settings().model_dump()
