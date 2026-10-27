"""The contract every strategy implements.

Adding the third strategy should not require touching the scanner, the engine
or the dashboard. Everything that differs between strategies lives behind this
interface: what history it needs, whether a setup is live right now, and where
the stop and target go.

Two things are deliberately not the strategy decision. The economic gates
(spread, liquidity, minimum stop distance, cost-to-stop ratio) are properties
of the instrument and the fee schedule rather than of the idea being traded, so
they are enforced once in the scanner for everyone. And position sizing belongs
to risk management: a strategy that could set its own stop floor or its own
size could opt out of the constraints that keep the account alive.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from scalper.engine.plan import MIN_REWARD_TO_RISK
from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.exchange.order_flow import FlowEvidence


@dataclass(frozen=True)
class StrategyContext:
    """Everything a strategy is allowed to look at."""

    symbol: str
    candles_5m: list[Candle]
    candles_1m: list[Candle]
    ticker: Ticker
    book: BookTop
    order_flow: FlowEvidence | None = None
    #: Set only by a caller that has already validated this exact candle
    #: series. A replay assesses every bar of one series, so the per-bar scan
    #: re-checks the same candles thousands of times - a third of the run.
    #: The live path leaves this False and is validated as before.
    candles_validated: bool = False

    @property
    def price(self) -> float:
        return self.candles_1m[-1].close


@dataclass(frozen=True)
class Setup:
    """Whether this strategy would enter right now, and why or why not.

    The note is not decoration. A scanner that shows a short list with no
    reasoning cannot be tuned, and an untunable scanner gets loosened by
    guesswork until the edge is gone.
    """

    ready: bool
    note: str
    direction: str | None = None

    @classmethod
    def waiting(cls, note: str) -> Setup:
        return cls(ready=False, note=note)

    @classmethod
    def entry(cls, direction: str, note: str) -> Setup:
        return cls(ready=True, note=note, direction=direction)


@dataclass(frozen=True)
class Levels:
    """Where the stop and target go, before the risk layer adjusts them."""

    stop: Decimal
    target: Decimal


class Strategy(ABC):
    """One tradeable idea."""

    id: str = "base"
    name: str = "Base"
    summary: str = ""

    #: Every departure from the source material, stated so that nobody later
    #: mistakes an assumption of ours for something that was specified.
    assumptions: tuple[str, ...] = ()

    #: Closed bars required before the strategy will produce any answer.
    #: The reward-to-risk this strategy trades at.
    #:
    #: Defaults to the platform minimum. A strategy whose source specifies a
    #: smaller ratio may lower it, and must say in its assumptions what win
    #: rate that then requires - the alternative is a strategy that appears in
    #: the scanner and can never open a trade.
    min_reward_to_risk: Decimal = MIN_REWARD_TO_RISK

    history_5m: int = 60
    history_1m: int = 40

    # Fetch enough candles to reconstruct a session without making that many
    # bars a mathematical warm-up requirement.
    requires_order_flow: bool = False
    signal_interval_s: int = 60
    manual_only: bool = False
    testnet_only: bool = False
    backtest_only: bool = False
    max_trades_per_session: int = 0
    candle_limit_1m: int = 200

    def session_key(self, timestamp_ms: int) -> str:
        """Session identifier for replay entry counts; overridden by session models."""
        return str(timestamp_ms // 86_400_000)

    def scheduled_exit(self, opened_at_ms: int) -> int | None:
        """Absolute exit deadline, evaluated before a replay bar's price path."""
        return None

    @property
    def fetch_limit_1m(self) -> int:
        return max(
            self.candle_limit_1m + int(self.testnet_only and not self.requires_order_flow),
            self.history_1m + 1,
        )

    @staticmethod
    def validate_candles(bars: Sequence[Candle]) -> str | None:
        """The candle half of `data_error`, usable on a whole series at once."""
        for bar in bars:
            values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
            if any(not math.isfinite(v) for v in values):
                return "non-finite candle data"
            if (
                bar.low <= 0
                or bar.volume < 0
                or bar.low > min(bar.open, bar.close)
                or bar.high < max(bar.open, bar.close)
            ):
                return "invalid candle range or volume"
        return None

    @staticmethod
    def data_error(ctx: StrategyContext) -> str | None:
        if not ctx.candles_validated:
            error = Strategy.validate_candles([*ctx.candles_1m, *ctx.candles_5m])
            if error:
                return error
        # The book is per-context and cheap, so it is always checked.
        if (
            not math.isfinite(ctx.book.bid)
            or not math.isfinite(ctx.book.ask)
            or ctx.book.bid <= 0
            or ctx.book.ask < ctx.book.bid
        ):
            return "invalid executable bid/ask"
        return None

    def has_enough_history(self, ctx: StrategyContext) -> bool:
        return len(ctx.candles_5m) >= self.history_5m and len(ctx.candles_1m) >= self.history_1m

    def research_context(self, ctx: StrategyContext) -> StrategyContext:
        """Keep manual testnet and replay indicator windows identical."""
        return replace(
            ctx,
            candles_5m=ctx.candles_5m[-max(100, self.history_5m) :],
            candles_1m=ctx.candles_1m[-max(self.candle_limit_1m, self.history_1m) :],
        )

    @abstractmethod
    def assess(self, ctx: StrategyContext) -> Setup:
        """Whether the entry conditions hold on the last CLOSED bar.

        Never the forming bar. Evaluating a condition on a bar that has not
        closed is the most common look-ahead bug in this domain: the condition
        that fires can be gone before the bar completes.
        """

    @abstractmethod
    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        """Stop and target for an entry at this price.

        Returned as intentions. The risk layer still applies the minimum stop
        distance and the exchange filters, and may refuse the trade outright.
        """

    def execution_levels(
        self, ctx: StrategyContext, entry: Decimal, direction: str, tick_size: Decimal
    ) -> Levels:
        """Allow strategies to preserve their target rules after exchange rounding."""
        return self.levels(ctx, entry, direction)

    @property
    def timeframe(self) -> str:
        """The candle interval this strategy decides on, as "1m", "5m".

        Derived from the same field the research catalog uses, so the trading
        tabs and the backtest tabs can never disagree about what a strategy
        runs on.
        """
        minutes = self.signal_interval_s // 60
        if minutes and minutes % 1440 == 0:
            return f"{minutes // 1440}d"
        if minutes and minutes % 60 == 0:
            return f"{minutes // 60}h"
        return f"{minutes}m"

    def describe(self) -> dict[str, object]:
        return {
            "testnet_only": self.testnet_only,
            "manual_only": self.manual_only,
            "id": self.id,
            "name": self.name,
            "summary": self.summary,
            "timeframe": self.timeframe,
            "assumptions": list(self.assumptions),
        }


class StrategyRegistry:
    """Every strategy this build knows about."""

    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {}

    def register(self, strategy: Strategy) -> Strategy:
        if strategy.id in self._strategies:
            raise ValueError(f"duplicate strategy id: {strategy.id}")
        self._strategies[strategy.id] = strategy
        return strategy

    def get(self, strategy_id: str) -> Strategy:
        if strategy_id not in self._strategies:
            known = ", ".join(sorted(self._strategies)) or "none"
            raise KeyError(f"unknown strategy {strategy_id!r}; known: {known}")
        return self._strategies[strategy_id]

    def ids(self) -> list[str]:
        return sorted(self._strategies)

    def describe(self) -> list[dict[str, object]]:
        return [self._strategies[i].describe() for i in self.ids()]
