"""Process-level settings, deliberately separate from exchange credentials.

The API process never needs an API key. It reads the database, it fans state
out to browsers, and it writes commands for the engine to pick up. Keeping
credentials in a different settings object means there is no code path by which
the web process could load one, which is a stronger guarantee than remembering
not to.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from scalper.config import Environment
from scalper.envfiles import PROJECT_ROOT, env_files


class AppSettings(BaseSettings):
    """Settings shared by both processes. Contains no secrets."""

    model_config = SettingsConfigDict(env_file=env_files(), extra="ignore")

    env: Environment = Field(default_factory=Environment.default, alias="BINANCE_ENV")

    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")

    engine_host: str = Field(default="127.0.0.1", alias="ENGINE_HOST")
    engine_port: int = Field(default=8765, alias="ENGINE_PORT")

    data_dir: Path = Field(default=PROJECT_ROOT / "var" / "data", alias="DATA_DIR")

    max_positions: int = Field(default=5, ge=1, le=20, alias="MAX_POSITIONS")
    max_leverage: int = Field(default=10, ge=1, le=100, alias="MAX_LEVERAGE")
    max_trade_risk: Decimal = Field(
        default=Decimal("0.01"),
        gt=0,
        le=Decimal("0.05"),
        alias="MAX_TRADE_RISK",
        allow_inf_nan=False,
    )
    max_margin_fraction: Decimal = Field(
        default=Decimal("0.5"),
        gt=0,
        le=Decimal("0.8"),
        alias="MAX_MARGIN_FRACTION",
        allow_inf_nan=False,
    )
    daily_loss_fraction: Decimal = Field(
        default=Decimal("0.03"),
        gt=0,
        le=Decimal("0.1"),
        alias="DAILY_LOSS_FRACTION",
        allow_inf_nan=False,
    )

    @field_validator("api_host", "engine_host")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        if value not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("unauthenticated services must bind to loopback")
        return value

    @property
    def engine_url(self) -> str:
        return f"ws://{self.engine_host}:{self.engine_port}/engine"

    @property
    def db_path(self) -> Path:
        return self.data_dir / f"scalper.{self.env.value}.sqlite"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / f"engine.{self.env.value}.lock"
