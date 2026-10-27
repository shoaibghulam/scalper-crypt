"""Validate commands in the engine as well as at the HTTP boundary."""

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from scalper.strategies import DEFAULT_STRATEGY_ID


class AutoTradeRequest(BaseModel):
    amount: Decimal = Field(gt=0, le=100_000, allow_inf_nan=False)
    leverage: int = Field(ge=1, le=100, strict=True)


class SingleCoinRequest(AutoTradeRequest):
    timeframe: Literal["1m", "5m", "10m"] = "1m"


class CreateBotRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=5)
    amount: Decimal = Field(gt=0, le=100_000, allow_inf_nan=False)
    leverage: int = Field(ge=1, le=100, strict=True)
    strategy: str = DEFAULT_STRATEGY_ID
    continuous: bool = Field(default=False, strict=True)
    name: str = Field(default="", max_length=64)
    margin_mode: Literal["ISOLATED", "CROSS", "CROSSED"] = "ISOLATED"

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, symbols: list[str]) -> list[str]:
        symbols = [s.strip().upper() for s in symbols]
        if any(not re.fullmatch(r"[A-Z0-9_]{1,30}", s) for s in symbols):
            raise ValueError("symbols must be nonempty exchange symbol names")
        if len(set(symbols)) != len(symbols):
            raise ValueError("duplicate symbols are not allowed")
        return symbols


class DismissBotsRequest(BaseModel):
    bot_ids: list[str] = Field(min_length=1, max_length=1000)
