"""Frozen BTC research hypothesis from the saved one-minute archive."""
from decimal import Decimal
from functools import lru_cache
from itertools import pairwise
from statistics import pstdev

from scalper.exchange.market_data import Candle
from scalper.indicators.core import adx, atr, rsi
from scalper.strategies.base import Levels, Setup, Strategy, StrategyContext


@lru_cache(maxsize=512)
def range_regime(bars: tuple[Candle, ...]) -> bool:
    return adx([b.high for b in bars], [b.low for b in bars],
               [b.close for b in bars]).adx < 25


class BtcSelectiveReversion(Strategy):
    id = "btc-selective-reversion-1m"
    name = "BTC Selective Reversion 1 Minute (Research)"
    summary = "Bollinger re-entry + recovering RSI; avoid strong closed-5m trends."
    backtest_only = True
    testnet_only = True
    history_1m = 200
    history_5m = 100
    signal_interval_s = 60
    use_regime = True
    research_symbols = ("BTCUSDT",)
    assumptions = (
        "Retrospective hypothesis from the saved archive; not proven profitable.",
        "BTCUSDT only; 1m BB20/2 band re-entry and RSI14 recovery toward 50.",
        "Closed 5m ADX14 must be below 25; no forming candle or future data.",
        "Original comparison bracket: max(1.5 ATR14, 0.45% of entry), target 2R.",
        "Fixed parameters before the comparison; 24/7, both directions.",
        "Uses experiment fees, slippage, leverage and order minimums; backtest only.",
    )

    def assess(self, ctx: StrategyContext) -> Setup:
        if ctx.symbol not in {"BTCUSDT", "REPLAYUSDT"}:
            return Setup.waiting("BTC research candidate requires BTCUSDT")
        if not self.has_enough_history(ctx):
            return Setup.waiting("requires 200 closed 1m and 100 closed 5m bars")
        ctx = self.research_context(ctx)
        if error := self.data_error(ctx):
            return Setup.waiting(error)
        for bars, interval in ((ctx.candles_1m, 60_000), (ctx.candles_5m, 300_000)):
            if any(b.open_time % interval or b.close_time != b.open_time+interval-1
                   for b in bars) or any(b.open_time-a.open_time != interval
                                        for a, b in pairwise(bars)):
                return Setup.waiting("invalid or discontinuous closed candles")
        if not 0 <= ctx.candles_1m[-1].close_time-ctx.candles_5m[-1].close_time < 300_000:
            return Setup.waiting("future or stale 5m context")
        if self.use_regime and not range_regime(tuple(ctx.candles_5m)):
            return Setup.waiting("5m trend too strong for range reversion")
        closes = [b.close for b in ctx.candles_1m]
        previous, current = closes[-21:-1], closes[-20:]
        old_mean, mean = sum(previous)/20, sum(current)/20
        old_width, width = 2*pstdev(previous), 2*pstdev(current)
        old_rsi, value = rsi(closes[:-1]), rsi(closes)
        long = (closes[-2] <= old_mean-old_width and closes[-1] > mean-width
                and old_rsi < value < 50)
        short = (closes[-2] >= old_mean+old_width and closes[-1] < mean+width
                 and old_rsi > value > 50)
        if long == short:
            return Setup.waiting("waiting for Bollinger re-entry and RSI recovery")
        return Setup.entry("LONG" if long else "SHORT", self.summary)

    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        if direction not in {"LONG", "SHORT"} or not entry.is_finite() or entry <= 0:
            raise ValueError("invalid BTC research entry")
        bars = ctx.candles_1m[-200:]
        value = atr([b.high for b in bars], [b.low for b in bars], [b.close for b in bars])
        distance = max(Decimal(str(1.5*value)), entry*Decimal("0.0045"))
        sign = Decimal(1 if direction == "LONG" else -1)
        result = Levels(entry-sign*distance, entry+sign*2*distance)
        if min(result.stop, result.target) <= 0:
            raise ValueError("nonpositive BTC bracket")
        return result

    def describe(self) -> dict[str, object]:
        return {**super().describe(), "timeframe": "1m", "backtest_only": True,
                "research_only": True, "group": "btc-archive-research",
                "parameters": {"bb_period": 20, "bb_sigma": 2, "rsi_period": 14,
                    "adx_period": 14, "adx_ceiling": 25 if self.use_regime else None,
                    "stop_atr": 1.5, "stop_floor": 0.0045, "target_r": 2}}


class BtcSelectiveReversed(BtcSelectiveReversion):
    id = "btc-selective-reversed-1m"
    name = "BTC Selective Reversed 1 Minute (Research)"
    summary = "Opposite selective-reversion signals; swap original stop and target roles."
    assumptions = (
        "Backtest-only reversal experiment; original strategy remains unchanged.",
        "Same closed 1m signals and closed 5m ADX filter; LONG becomes SHORT and vice versa.",
        "D = max(1.5 ATR14, 0.45% of actual reverse entry). Stop 2D; target D.",
        "Reward/risk is 0.5, not 2. High target-hit rate alone does not establish profit.",
        "Brackets rebuilt from actual reverse fill, with adverse costs in both directions.",
        "Same risk sizing rules; wider stops can reduce size below exchange minimums.",
        "Independent replay, not relabelled trades; counts need not swap exactly.",
        "BTCUSDT only. Costs and candle execution limitations remain unchanged.",
    )

    def assess(self, ctx: StrategyContext) -> Setup:
        original = super().assess(ctx)
        if not original.ready:
            return original
        return Setup.entry("SHORT" if original.direction == "LONG" else "LONG", self.summary)

    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        if direction not in {"LONG", "SHORT"}:
            raise ValueError("invalid reversed direction")
        original_direction = "SHORT" if direction == "LONG" else "LONG"
        original = super().levels(ctx, entry, original_direction)
        return Levels(stop=original.target, target=original.stop)

    def describe(self) -> dict[str, object]:
        result = super().describe()
        result["parameters"] = {
            "bb_period": 20, "bb_sigma": 2, "rsi_period": 14, "adx_period": 14,
            "adx_ceiling": 25, "base_distance_atr": 1.5, "base_distance_floor": 0.0045,
            "stop_distance_multiple": 2, "target_distance_multiple": 1,
            "reward_risk": 0.5, "reverse_direction": True,
        }
        return result
