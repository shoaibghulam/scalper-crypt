"""Eleven 24/7 ten-minute experiments; no cash sessions or time-of-day exits."""

from decimal import Decimal
from itertools import pairwise

from scalper.indicators.core import adx, ema, rsi
from scalper.strategies.base import Strategy, StrategyContext
from scalper.strategies.mtf_structure import MultiTimeframeStructure, aggregate
from scalper.strategies.pdf_advanced_five_minute import macd_histogram, stoch_rsi
from scalper.strategies.pdf_five_minute import PdfFiveMinute, PdfSignal, bollinger, volatility

RECIPES = (
    ("ema-pullback", "EMA Ribbon Pullback", "EMA9/20/50 trend, zone touch, prior-bar break."),
    ("macd-recovery", "MACD Momentum Recovery", "EMA50 bias, MACD histogram zero cross."),
    ("donchian-volume", "Donchian Volume Breakout", "Fresh 20-bar breakout, RVOL >=1.5."),
    ("bb-reentry", "Bollinger RSI Re-entry", "ADX <20, prior outer-band close, RSI reversal."),
    ("stoch-reset", "StochRSI Trend Reset", "EMA20/50 trend, StochRSI extreme crossover."),
    ("rolling-vwap", "Rolling VWAP Reclaim", "50-bar typical-price VWAP reclaim, EMA50 bias."),
    ("adx-expansion", "ADX Momentum Expansion", "ADX crosses 25, aligned DI, prior-bar break."),
    ("squeeze", "Bollinger Squeeze Release", "Prior narrow bands, fresh outer-band breakout."),
    ("liquidity-sweep", "Liquidity Sweep Reclaim", "Sweep prior 20-bar extreme and close inside."),
    ("rsi-pullback", "RSI Midline Recovery", "EMA20/50 trend, RSI crosses 50 after pullback."),
)


class TenMinuteMtf(MultiTimeframeStructure):
    id = "mtf-lux-inspired-structure-10m"
    name = "MTF Structure: LuxAlgo-inspired 1h to 10m"
    summary = "1h/30m bias, 15m strength, completed 5m structure, 10m retest entry; 24/7."
    entry_minutes = 10
    signal_interval_s = 600

    def describe(self) -> dict[str, object]:
        result = super().describe()
        result.update(timeframe="10m", group="ten-minute",
                      source="docs/research/ten-minute/README.md")
        result["assumptions"] = [str(a).replace("1m", "10m") for a in result["assumptions"]]
        result["assumptions"].append(
            "24/7. 5m is a structure sub-step, not a higher timeframe than the 10m entry."
        )
        return result


class TenMinuteRecipe(PdfFiveMinute):
    signal_interval_s = 600
    history_5m = 402
    history_1m = 40
    max_signal_age_s = 20

    def __init__(self, number: int) -> None:
        if not 1 <= number <= len(RECIPES):
            raise ValueError("unknown 10m recipe")
        self.number = number
        suffix, name, self.summary = RECIPES[number - 1]
        self.id, self.name = f"ten-{suffix}", f"10m: {name}"

    def _bars(self, ctx: StrategyContext):
        ctx = self.research_context(ctx)
        if not self.has_enough_history(ctx):
            raise ValueError("need 402 closed 5m candles for 200 completed 10m bars")
        if error := self.data_error(ctx):
            raise ValueError(error)
        for bars, interval in ((ctx.candles_5m, 300_000), (ctx.candles_1m, 60_000)):
            if any(b.open_time % interval or b.close_time != b.open_time + interval - 1
                   for b in bars):
                raise ValueError("invalid candle alignment")
            if any(b.open_time - a.open_time != interval for a, b in pairwise(bars)):
                raise ValueError("candle history has gaps or duplicates")
        bars = aggregate(ctx.candles_5m, 10)[-200:]
        if (bars[-1].close_time != ctx.candles_1m[-1].close_time
                or ctx.candles_5m[-1].close_time != bars[-1].close_time):
            raise ValueError("waiting for a newly closed 10m candle")
        return bars

    def signal(self, ctx: StrategyContext) -> PdfSignal:
        bars = self._bars(ctx)
        c, p = bars[-1], bars[-2]
        closes = [b.close for b in bars]
        value = volatility(bars)
        if value <= 0:
            raise ValueError("no usable ATR14")
        e9, e20, e50 = (ema(closes, n) for n in (9, 20, 50))
        old20, old50 = ema(closes[:-1], 20), ema(closes[:-1], 50)
        strength = adx([b.high for b in bars], [b.low for b in bars], closes)
        avg_volume = sum(b.volume for b in bars[-21:-1]) / 20
        if avg_volume <= 0:
            raise ValueError("no usable volume baseline")
        relative = c.volume / avg_volume
        momentum, old_momentum = rsi(closes), rsi(closes[:-1])
        middle, deviation = bollinger(bars)
        old_middle, old_deviation = bollinger(bars[:-1])
        line, histogram = macd_histogram(closes)
        stochastic = stoch_rsi(closes)
        signals = []
        for sign in (1, -1):
            trend = (e20 - e50) * sign > 0 and (e20 - old20) * sign > 0
            body = (c.close - c.open) * sign > 0
            break_previous = (c.close - (p.high if sign == 1 else p.low)) * sign > 0
            previous_extreme = p.low if sign == 1 else p.high
            if self.number == 1:
                zone = sorted((ema(closes[:-1], 9), old20))
                ready = (trend and (e9 - e20) * sign > 0 and p.low <= zone[1]
                         and p.high >= zone[0] and break_previous and relative >= 1.2)
            elif self.number == 2:
                ready = ((c.close - e50) * sign > 0 and line[-1] * sign > 0
                         and histogram[-2] * sign <= 0 < histogram[-1] * sign
                         and relative >= 1.2)
            elif self.number == 3:
                level = max(b.high for b in bars[-21:-1]) if sign == 1 else min(
                    b.low for b in bars[-21:-1])
                ready = (trend and (c.close - level) * sign > 0 and relative >= 1.5)
            elif self.number == 4:
                ready = (strength.adx < 20 and (p.close - old_middle) * sign <= -2 * old_deviation
                         and (c.close - middle) * sign > -2 * deviation
                         and (momentum - old_momentum) * sign > 0
                         and (old_momentum < 35 if sign == 1 else old_momentum > 65)
                         and relative >= 1)
            elif self.number == 5:
                (old_k, old_d), (k, d) = stochastic[-2:]
                ready = (trend and (old_k - old_d) * sign <= 0 < (k - d) * sign
                         and (old_k < .3 if sign == 1 else old_k > .7)
                         and break_previous and relative >= 1)
            elif self.number == 6:
                def rolling_vwap(window):
                    total = sum(b.volume for b in window)
                    if total <= 0:
                        raise ValueError("rolling VWAP has no volume")
                    return sum((b.high + b.low + b.close) / 3 * b.volume for b in window) / total
                current = rolling_vwap(bars[-50:])
                previous = rolling_vwap(bars[-51:-1])
                ready = ((p.close - previous) * sign <= 0 < (c.close - current) * sign
                         and (c.close - e50) * sign > 0 and relative >= 1.2)
            elif self.number == 7:
                old_adx = adx([b.high for b in bars[:-1]], [b.low for b in bars[:-1]],
                              closes[:-1]).adx
                ready = (old_adx < 25 <= strength.adx and trend and break_previous
                         and (strength.plus_di - strength.minus_di) * sign > 0 and relative >= 1.2)
            elif self.number == 8:
                widths = [4 * bollinger(bars[:end])[1] / bollinger(bars[:end])[0]
                          for end in range(len(bars) - 60, len(bars))]
                narrow = widths[-1] <= sorted(widths)[11]
                ready = (narrow and (p.close - old_middle) * sign <= 2 * old_deviation
                         and (c.close - middle) * sign > 2 * deviation and relative >= 1.5)
            elif self.number == 9:
                level = min(b.low for b in bars[-21:-1]) if sign == 1 else max(
                    b.high for b in bars[-21:-1])
                extreme = c.low if sign == 1 else c.high
                ready = ((extreme - level) * sign < 0 < (c.close - level) * sign
                         and (momentum - old_momentum) * sign > 0 and relative >= 1.5)
            else:
                ready = (trend and (p.close - old50) * sign > 0
                         and (old_momentum - 50) * sign <= 0 < (momentum - 50) * sign
                         and break_previous and relative >= 1.2)
            if ready and body:
                extreme = (min(previous_extreme, c.low) if sign == 1
                           else max(previous_extreme, c.high))
                signals.append(PdfSignal("LONG" if sign == 1 else "SHORT", extreme, value,
                                         reward=Decimal("2"), stop_atr=1.5))
        if len(signals) != 1:
            raise ValueError(f"waiting for {self.name} confirmation")
        return signals[0]

    def describe(self) -> dict[str, object]:
        return {
            **Strategy.describe(self), "timeframe": "10m", "research_only": True,
            "group": "ten-minute", "source": "docs/research/ten-minute/README.md",
            "assumptions": [
                "24/7, no session opening range, clock gate or forced session exit.",
                "Complete UTC-aligned 10m bars from two closed 5m bars; 200-bar indicator window.",
                self.summary,
                "Directional candle body required; symmetric long/short rules.",
                "ATR14 stop beyond two-bar extreme plus 0.1 ATR; minimum 1.5 ATR and 0.45%.",
                "2R target; maximum 0.5 ATR entry chase; full frozen brackets and testnet gates.",
                "Rolling VWAP means 50 bars, not a session-reset VWAP; no time-of-day dependency.",
                "Fixed experimental parameters; compare unseen periods, costs and drawdown.",
            ],
        }


TEN_MINUTE_STRATEGIES = {s.id: s for s in [TenMinuteMtf(), *(
    TenMinuteRecipe(n) for n in range(1, 11)
)]}
