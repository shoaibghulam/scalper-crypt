"""One-minute trend pullbacks using all nine user-requested indicators."""

from decimal import Decimal
from itertools import pairwise

from scalper.indicators.core import adx, ema, rsi
from scalper.strategies.base import Strategy, StrategyContext
from scalper.strategies.pdf_advanced_five_minute import macd_histogram, stoch_rsi
from scalper.strategies.pdf_five_minute import (
    PdfFiveMinute,
    PdfSignal,
    bollinger,
    session_vwap,
    volatility,
)


class NineIndicatorPullback(PdfFiveMinute):
    id = "nine-indicator-1m-pullback"
    name = "1m Nine-Indicator Trend Pullback"
    summary = "EMA/VWAP/ADX trend, Bollinger pullback, RSI/MACD/StochRSI recovery and volume."
    signal_interval_s = 60
    max_signal_age_s = 20
    history_1m = 200
    candle_limit_1m = 1440  # A full UTC session for true session VWAP, not rolling VWAP.
    history_5m = 300  # 1,500-minute replay warm-up includes the current UTC session.

    def __init__(self) -> None:
        pass  # Reuse only the base class's entry/bracket validation and rounding.

    def signal(self, ctx: StrategyContext) -> PdfSignal:
        ctx = self.research_context(ctx)
        if not self.has_enough_history(ctx):
            raise ValueError("need 200 closed 1m candles and session history")
        if error := self.data_error(ctx):
            raise ValueError(error)
        bars = ctx.candles_1m
        if any(b.open_time % 60_000 or b.close_time != b.open_time + 59_999 for b in bars):
            raise ValueError("invalid 1m candle alignment")
        if any(b.open_time - a.open_time != 60_000 for a, b in pairwise(bars)):
            raise ValueError("1m history has gaps or duplicates")
        vwap, _ = session_vwap(bars)
        bars = bars[-200:]
        closes = [b.close for b in bars]
        previous, trigger = bars[-2:]
        fast, medium, slow = (ema(closes, period) for period in (9, 20, 50))
        sign = 1 if fast > medium > slow else -1 if fast < medium < slow else 0
        if (not sign or (medium - ema(closes[:-3], 20)) * sign <= 0
                or (trigger.close - vwap) * sign <= 0):
            raise ValueError("EMA9/20/50 direction or session VWAP is unaligned")
        strength = adx([b.high for b in bars], [b.low for b in bars], closes)
        if strength.adx < 20 or (strength.plus_di - strength.minus_di) * sign <= 0:
            raise ValueError("ADX14/DI: weak or opposing trend")
        value = volatility(bars)
        if value <= 0:
            raise ValueError("ATR14: no usable volatility")
        prior_fast, prior_medium = ema(closes[:-1], 9), ema(closes[:-1], 20)
        zone_low, zone_high = sorted((prior_fast, prior_medium))
        if previous.low > zone_high or previous.high < zone_low:
            raise ValueError("no pullback into the EMA9/20 zone")
        middle, deviation = bollinger(bars)
        prior_middle, prior_deviation = bollinger(bars[:-1])
        # Bollinger is an extension filter inside this trend model, not an opposing fade.
        if (deviation <= 0 or not 0 < (trigger.close - middle) * sign < 2 * deviation
                or (previous.close - prior_middle) * sign < -2 * prior_deviation):
            raise ValueError("Bollinger20/2: reversal or overextended confirmation")
        current_rsi, prior_rsi = rsi(closes), rsi(closes[:-1])
        directional_rsi = current_rsi if sign == 1 else 100 - current_rsi
        if not 50 <= directional_rsi <= 70 or (current_rsi - prior_rsi) * sign <= 0:
            raise ValueError("RSI14: momentum not recovering or already stretched")
        line, histogram = macd_histogram(closes)
        if line[-1] * sign <= 0 or (histogram[-1] - histogram[-2]) * sign <= 0:
            raise ValueError("MACD12/26/9: trend or histogram recovery unconfirmed")
        stochastic = stoch_rsi(closes)
        directional = [(k, d) if sign == 1 else (1 - k, 1 - d) for k, d in stochastic]
        k, d = directional[-1]
        crossed = any(a <= b and c > e for (a, b), (c, e) in pairwise(directional[-4:]))
        if not crossed or not d < k < 0.9 or min(a for a, _ in directional[-4:]) > 0.5:
            raise ValueError("StochRSI14/14/3/3: no recent momentum-reset crossover")
        average_volume = sum(b.volume for b in bars[-21:-1]) / 20
        if average_volume <= 0 or trigger.volume < 1.2 * average_volume:
            raise ValueError("volume below 1.2x the previous 20 candles")
        edge = previous.high if sign == 1 else previous.low
        if ((trigger.close - edge) * sign <= 0 or (trigger.close - trigger.open) * sign <= 0
                or (trigger.close - fast) * sign <= 0):
            raise ValueError("waiting for a directional close beyond the pullback candle")
        extreme = min(previous.low, trigger.low) if sign == 1 else max(previous.high, trigger.high)
        return PdfSignal("LONG" if sign == 1 else "SHORT", extreme, value,
                         reward=Decimal("2"), stop_atr=1.5)

    def describe(self) -> dict[str, object]:
        return {
            **Strategy.describe(self), "timeframe": "1m", "research_only": True,
            "group": "nine-indicator", "source": "docs/research/nine-indicator/README.md",
            "assumptions": [
                "All nine indicators use 1m candles; no higher-timeframe direction filter.",
                "EMA9/20/50 alignment and EMA20 slope, UTC-session VWAP, ADX14 >=20 and DI.",
                "EMA9/20 pullback; Bollinger20/2 permits confirmation inside the trend-side band.",
                "RSI14 long 50-70 / short 30-50 with improvement; MACD12/26/9 histogram recovery.",
                "StochRSI14/14/3/3 reset and crossover within three bars; avoid extreme readings.",
                "Trigger volume >=1.2x prior 20 bars; directional close beyond the previous bar.",
                "ATR14; structural stop plus buffer, minimum 1.5 ATR and 0.45%; target 2R.",
                "Full session VWAP requires UTC midnight history and at least 12 session bars.",
                "Other indicators use 200 bars; 0.5 ATR chase limit; frozen brackets.",
                "Experimental/testnet only; all filters are required and may produce few trades.",
            ],
        }
