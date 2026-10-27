"""Versioned bounded rules; no generated code or eval is accepted."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from scalper.research.data.loader import digest
from scalper.research.data.models import Record, Timeframe

Indicator = Literal[
    "close",
    "volume",
    "ema",
    "sma",
    "wma",
    "rsi",
    "macd",
    "bb_upper",
    "bb_lower",
    "atr",
    "adx",
    "stochastic",
    "vwap",
    "roc",
    "momentum",
    "volume_sma",
    "volatility",
    "returns",
]


class Operand(Record):
    indicator: Indicator | None = None
    period: int = Field(default=14, ge=2, le=200)
    value: float | None = None

    @model_validator(mode="after")
    def valid(self):
        if (self.indicator is None) == (self.value is None):
            raise ValueError("An operand needs exactly one indicator or constant")
        return self


class Condition(Record):
    op: Literal["gt", "lt", "ge", "le", "cross_up", "cross_down", "and", "or", "not"]
    left: Operand | None = None
    right: Operand | None = None
    children: tuple[Condition, ...] = ()

    @model_validator(mode="after")
    def valid(self):
        if self.op in {"and", "or", "not"}:
            count = len(self.children)
            if self.left or self.right or (count != 1 if self.op == "not" else not 2 <= count <= 4):
                raise ValueError("Invalid logical node")
        elif self.children or self.left is None or self.right is None:
            raise ValueError("Comparison needs two operands and no children")
        if self.depth > 4 or self.size > 15:
            raise ValueError("Expression exceeds depth 4 or 15 nodes")
        return self

    @property
    def depth(self):
        return 1 + max((c.depth for c in self.children), default=0)

    @property
    def size(self):
        return 1 + sum(c.size for c in self.children)


class Risk(Record):
    stop_loss: float = Field(default=0.008, ge=0.001, le=0.10)
    take_profit: float = Field(default=0.016, ge=0.001, le=0.30)
    trailing_stop: float = Field(default=0, ge=0, le=0.10)
    position_size: float = Field(default=0.20, gt=0, le=1)
    leverage: float = Field(default=1, ge=1, le=5)
    cooldown: int = Field(default=3, ge=0, le=1000)
    max_hold: int = Field(default=72, ge=1, le=1000)
    max_positions: Literal[1] = 1


class StrategyDefinition(Record):
    version: Literal[1] = 1
    timeframe: Timeframe = "5m"
    direction: Literal["long", "short"] = "long"
    entry: Condition
    exit: Condition | None = None
    risk: Risk = Risk()
    order_type: Literal["market", "limit"] = "market"
    limit_offset: float = Field(default=0.001, ge=0, le=0.02)

    @model_validator(mode="after")
    def bounded(self):
        nodes = [self.entry] + ([self.exit] if self.exit else [])
        indicators = set()

        def visit(node):
            for operand in (node.left, node.right):
                if operand and operand.indicator:
                    indicators.add((operand.indicator, operand.period))
            for child in node.children:
                visit(child)

        for node in nodes:
            visit(node)
        if self.complexity > 20 or len(indicators) > 8:
            raise ValueError("Maximum 20 nodes and 8 indicator configurations per strategy")
        if len(self.model_dump_json()) > 12000:
            raise ValueError("Strategy exceeds serialized length limit")
        return self

    @property
    def complexity(self):
        return self.entry.size + (self.exit.size if self.exit else 0)

    @property
    def strategy_id(self):
        return digest(self.model_dump(mode="json"))[:20]


class Costs(Record):
    fee: float = Field(default=0.0005, ge=0, le=0.01)
    slippage: float = Field(default=0.0002, ge=0, le=0.01)
    spread: float = Field(default=0.0002, ge=0, le=0.01)
    require_funding: bool = True


class Safety(Record):
    max_daily_loss: float = Field(default=0.05, gt=0, le=0.5)
    max_drawdown: float = Field(default=0.20, gt=0, le=0.8)
    max_position_size: float = Field(default=0.25, gt=0, le=1)
    max_leverage: float = Field(default=3, ge=1, le=5)
    max_consecutive_losses: int = Field(default=8, ge=1, le=100)
    max_positions: Literal[1] = 1
    kill_switch: bool = False


class Weights(Record):
    sharpe: float = Field(default=1, ge=0, le=10)
    drawdown: float = Field(default=3, ge=0, le=10)
    profit_factor: float = Field(default=0.3, ge=0, le=10)
    net_return: float = Field(default=2, ge=0, le=10)
    complexity: float = Field(default=0.03, ge=0, le=10)


class SearchConfig(Record):
    population: int = Field(default=64, ge=8, le=10000)
    generations: int = Field(default=8, ge=1, le=100)
    mutation_probability: float = Field(default=0.6, ge=0, le=1)
    crossover_probability: float = Field(default=0.25, ge=0, le=1)
    elite_fraction: float = Field(default=0.15, ge=0.01, le=0.5)
    max_complexity: int = Field(default=7, ge=1, le=20)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    capital: float = Field(default=1000, gt=0, le=1e9)
    min_trades: int = Field(default=20, ge=2, le=10000)
    finalists: int = Field(default=3, ge=1, le=10)
    optimization_trials: int = Field(default=20, ge=0, le=500)
    walk_forward_folds: int = Field(default=3, ge=2, le=6)
    embargo_bars: int = Field(default=12, ge=1, le=1000)
    monte_carlo_samples: int = Field(default=200, ge=20, le=2000)
    ml_filter: bool = False
    costs: Costs = Costs()
    safety: Safety = Safety()
    weights: Weights = Weights()

    @model_validator(mode="after")
    def budget(self):
        if self.population * self.generations > 1_000_000:
            raise ValueError("Maximum one million scheduled evaluations per experiment")
        return self
