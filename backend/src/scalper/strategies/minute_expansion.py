"""Twenty source-inspired, fixed-rule 1m hypotheses for testnet experiments."""

from itertools import pairwise

from scalper.strategies.base import Setup, Strategy, StrategyContext
from scalper.strategies.laboratory import LabStrategy
from scalper.strategies.minute_indicators import RECIPES as INDICATORS
from scalper.strategies.minute_indicators import signals as indicator_signals
from scalper.strategies.minute_patterns import RECIPES as PATTERNS
from scalper.strategies.minute_patterns import signals as pattern_signals

RECIPES = [*INDICATORS, *PATTERNS]


class MinuteCandidate(LabStrategy):
    history_1m = 200
    history_5m = 100
    candle_limit_1m = 200
    signal_interval_s = 60
    testnet_only = True

    def __init__(self, number: int) -> None:
        if not 1 <= number <= 20:
            raise ValueError("invalid one-minute research candidate")
        self.number = number
        self.id = f"minute-{number:02d}"
        self.name, self.source, self.summary = RECIPES[number - 1]

    def assess(self, ctx: StrategyContext) -> Setup:
        ctx = self.research_context(ctx)
        if not self.has_enough_history(ctx):
            return Setup.waiting("needs 200 closed 1m bars and 100 context bars")
        if error := self.data_error(ctx):
            return Setup.waiting(error)
        bars = tuple(ctx.candles_1m)
        if any(b.open_time - a.open_time != 60000 for a, b in pairwise(bars)):
            return Setup.waiting("one-minute candle history has a gap")
        if self.number <= 10:
            long, short = indicator_signals(bars)[self.number - 1]
        else:
            long, short = pattern_signals(bars)[self.number - 11]
        if long == short:
            return Setup.waiting("no unambiguous confirmed one-minute trigger")
        return Setup.entry("LONG" if long else "SHORT", self.summary)

    def describe(self) -> dict[str, object]:
        return {
            **Strategy.describe(self),
            "timeframe": "1m",
            "source": self.source,
            "research_only": True,
            "assumptions": [
                "Our fixed one-minute crypto adaptation, not the author's tested system.",
                "Closed candles only; patterns and parameters are unvalidated hypotheses.",
                "Shared stop: max(1.5 ATR14, 0.45% of entry); target: 2R before fees.",
                "Existing spread, sizing, slippage and account gates remain enforced.",
                "24/7; no session filter; 100 five-minute context bars retained by the pipeline.",
            ],
        }


MINUTE_STRATEGIES = {f"minute-{n:02d}": MinuteCandidate(n) for n in range(1, 21)}
