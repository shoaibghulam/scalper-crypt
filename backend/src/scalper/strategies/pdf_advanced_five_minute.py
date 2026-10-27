"""Causal candle-based adaptations of pages 3-9 of the advanced research PDF.

Pages 10-12 require event/OI/two-leg infrastructure and are described separately,
never replaced by candle proxies. Automation choices live in the companion guide.
"""

from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from statistics import median

from scalper.exchange.market_data import Candle
from scalper.indicators.core import adx, ema, ema_series
from scalper.strategies.base import Strategy, StrategyContext
from scalper.strategies.combinations import fifteen
from scalper.strategies.pdf_five_minute import (
    PdfFiveMinute,
    PdfSignal,
    bollinger,
    session_vwap,
    volatility,
)

SOURCE = "new strategies/10_advanced_5_minute_crypto_strategies.pdf"
RECIPES = (
    ("ema-adx", "EMA Ribbon + ADX Pullback", "15m EMA50/200, 5m pullback, ADX and RVOL.", 615),
    (
        "donchian-rvol",
        "Donchian + ATR + RVOL",
        "20-bar close breakout with ATR/volume expansion.",
        100,
    ),
    (
        "squeeze",
        "Bollinger-Keltner Squeeze",
        "Three-bar squeeze, dual-envelope release and VWAP.",
        300,
    ),
    (
        "stochrsi",
        "StochRSI Trend Reset",
        "15m EMA200 context, StochRSI reset and micro breakout.",
        615,
    ),
    (
        "macd-vwap",
        "MACD Re-Acceleration + VWAP",
        "Second momentum leg after histogram contraction.",
        300,
    ),
    (
        "ichimoku",
        "Ichimoku Kijun Pullback",
        "Causally shifted cloud, 15m confirmation and volume.",
        252,
    ),
    (
        "sweep-reclaim",
        "Sweep + Structure + VWAP",
        "Range sweep, subsequent structure shift and VWAP reclaim.",
        300,
    ),
)

UNAVAILABLE_STRATEGIES = (
    {
        "id": "advanced5-order-book",
        "name": "Advanced 5m: Order-Book Imbalance + Micro-Breakout",
        "source_pages": "10",
        "reason": "Needs synchronized historical L2 snapshots and aggressive trade events, "
        "plus an event-execution simulator. Candles cannot establish depth persistence.",
    },
    {
        "id": "advanced5-open-interest",
        "name": "Advanced 5m: Price + Open Interest + Volume Breakout",
        "source_pages": "11",
        "reason": "Needs timestamp-aligned historical open-interest observations. "
        "Candle volume cannot substitute for open interest.",
    },
    {
        "id": "advanced5-btc-eth-pairs",
        "name": "Advanced 5m: BTC-ETH Pair Spread Mean Reversion",
        "source_pages": "12",
        "reason": "Needs a separate synchronized two-leg simulator with past-only hedge fitting, "
        "stationarity testing, both legs' costs and funding. Single-coin replay is unsuitable.",
    },
)


def relative_volume(bars: list[Candle]) -> float:
    baseline = sum(bar.volume for bar in bars[-21:-1]) / 20
    if baseline <= 0:
        raise ValueError("no relative-volume baseline")
    return bars[-1].volume / baseline


def stoch_rsi(closes: list[float]) -> list[tuple[float, float]]:
    """Wilder RSI14 -> stochastic14 -> SMA3 K -> SMA3 D, on a 0..1 scale."""
    if len(closes) < 32:
        raise ValueError("StochRSI needs at least 32 closes")
    changes = [b - a for a, b in pairwise(closes)]
    gain = sum(max(value, 0) for value in changes[:14]) / 14
    loss = sum(max(-value, 0) for value in changes[:14]) / 14
    rsis = []
    for index in range(13, len(changes)):
        if index > 13:
            gain = (gain * 13 + max(changes[index], 0)) / 14
            loss = (loss * 13 + max(-changes[index], 0)) / 14
        rsis.append(100 * gain / (gain + loss) if gain + loss else 50)
    raw = []
    for end in range(14, len(rsis) + 1):
        window = rsis[end - 14 : end]
        low, high = min(window), max(window)
        raw.append((window[-1] - low) / (high - low) if high > low else 0.5)
    ks = [sum(raw[i - 2 : i + 1]) / 3 for i in range(2, len(raw))]
    return [(ks[i], sum(ks[i - 2 : i + 1]) / 3) for i in range(2, len(ks))]


def macd_histogram(closes: list[float]) -> tuple[list[float], list[float]]:
    fast, slow = ema_series(closes, 12), ema_series(closes, 26)
    line = [a - b for a, b in zip(fast[14:], slow, strict=True)]
    signal = ema_series(line, 9)
    return line[8:], [a - b for a, b in zip(line[8:], signal, strict=True)]


def midpoint(bars: list[Candle], period: int) -> float:
    window = bars[-period:]
    return (max(b.high for b in window) + min(b.low for b in window)) / 2


@dataclass(frozen=True)
class Cloud:
    tenkan: float
    kijun: float
    span_a: float
    span_b: float
    forward_a: float
    forward_b: float


def ichimoku(bars: list[Candle]) -> Cloud:
    """Visible cloud uses values computed 26 bars ago; no future data or Chikou lookahead."""
    if len(bars) < 78:
        raise ValueError("Ichimoku needs 78 completed candles")
    prior = bars[:-26]
    tenkan, kijun = midpoint(bars, 9), midpoint(bars, 26)
    return Cloud(
        tenkan,
        kijun,
        (midpoint(prior, 9) + midpoint(prior, 26)) / 2,
        midpoint(prior, 52),
        (tenkan + kijun) / 2,
        midpoint(bars, 52),
    )


class AdvancedFiveMinute(PdfFiveMinute):
    def __init__(self, number: int) -> None:
        if not 1 <= number <= len(RECIPES):
            raise ValueError("unknown advanced PDF strategy")
        self.number = number
        suffix, name, self.summary, self.history_5m = RECIPES[number - 1]
        self.id = f"advanced5-{suffix}"
        self.name = f"Advanced 5m: {name}"
        self.pages = str(number + 2)

    def signal(self, ctx: StrategyContext) -> PdfSignal:
        bars = self._bars(ctx)
        value = volatility(bars)
        if value <= 0:
            raise ValueError("no usable ATR")
        methods = (
            self._ribbon,
            self._donchian,
            self._squeeze,
            self._stochastic,
            self._macd,
            self._ichimoku,
            self._sweep,
        )
        return methods[self.number - 1](bars, value)

    def _ribbon(self, bars: list[Candle], value: float) -> PdfSignal:
        higher = fifteen(bars)
        if len(higher) < 203:
            raise ValueError("need 203 completed 15m candles for EMA200 context")
        hc = [b.close for b in higher]
        hc50, hc200 = ema(hc, 50), ema(hc, 200)
        sign = 1 if hc50 > hc200 else -1 if hc50 < hc200 else 0
        if (
            not sign
            or (hc50 - ema(hc[:-3], 50)) * sign <= 0.1 * volatility(higher)
            or (hc200 - ema(hc[:-3], 200)) * sign <= 0
        ):
            raise ValueError("flat or unaligned 15m EMA trend")
        closes = [b.close for b in bars]
        fast, slow = ema(closes, 20), ema(closes, 50)
        if (fast - slow) * sign <= 0 or adx(
            [b.high for b in bars], [b.low for b in bars], closes
        ).adx <= 20:
            raise ValueError("5m EMA or ADX does not confirm trend")
        alignments = [
            ema(closes[:i], 20) > ema(closes[:i], 50)
            for i in range(len(closes) - 10, len(closes) + 1)
        ]
        if sum(a != b for a, b in pairwise(alignments)) > 2:
            raise ValueError("repeated EMA20/50 crosses")
        previous, trigger = bars[-2:]
        old_fast, old_slow = ema(closes[:-1], 20), ema(closes[:-1], 50)
        extreme = previous.low if sign == 1 else previous.high
        if (
            (extreme - old_fast) * sign > 0.1 * value
            or (previous.close - old_slow) * sign <= 0
            or (previous.close - old_fast) * sign > 0.25 * value
        ):
            raise ValueError("no orderly pullback to EMA20")
        if (
            (trigger.close - fast) * sign <= 0
            or (trigger.close - trigger.open) * sign <= 0
            or relative_volume(bars) <= 1.2
        ):
            raise ValueError("no directional EMA20 reclaim with RVOL above 1.2")
        extreme = min(b.low for b in bars[-4:]) if sign == 1 else max(b.high for b in bars[-4:])
        return PdfSignal(
            "LONG" if sign == 1 else "SHORT", extreme, value, reward=Decimal("2"), stop_atr=1.2
        )

    def _donchian(self, bars: list[Candle], value: float) -> PdfSignal:
        trigger = bars[-1]
        prior = bars[-21:-1]
        high, low = max(b.high for b in prior), min(b.low for b in prior)
        trend = ema([b.close for b in bars], 50)
        sign = (
            1
            if trigger.close > high and trigger.close > trend
            else (-1 if trigger.close < low and trigger.close < trend else 0)
        )
        span = trigger.high - trigger.low
        location = (
            ((trigger.close - trigger.low) if sign == 1 else (trigger.high - trigger.close)) / span
            if span > 0
            else 0
        )
        if (
            not sign
            or location < 0.75
            or value <= volatility(bars[:-5])
            or relative_volume(bars) < 1.5
        ):
            raise ValueError("no strong-close Donchian breakout with ATR and RVOL")
        extreme = trigger.low if sign == 1 else trigger.high
        return PdfSignal("LONG" if sign == 1 else "SHORT", extreme, value)

    def _squeeze(self, bars: list[Candle], value: float) -> PdfSignal:
        for back in (1, 2, 3):
            prefix = bars[:-back]
            mean, sigma = bollinger(prefix)
            center = ema([b.close for b in prefix], 20)
            width = 1.5 * volatility(prefix)
            if not center - width < mean - 2 * sigma < mean + 2 * sigma < center + width:
                raise ValueError("need three completed Bollinger-inside-Keltner candles")
        trigger = bars[-1]
        mean, sigma = bollinger(bars)
        center = ema([b.close for b in bars], 20)
        vwap, _ = session_vwap(bars)
        sign = (
            1
            if trigger.close > max(mean + 2 * sigma, center + 1.5 * value, vwap)
            else (-1 if trigger.close < min(mean - 2 * sigma, center - 1.5 * value, vwap) else 0)
        )
        if (
            not sign
            or relative_volume(bars) < 1.3
            or value <= volatility(bars[:-1])
            or trigger.high - trigger.low > 2 * value
        ):
            raise ValueError("no timely dual-envelope squeeze release with volume")
        extreme = min(b.low for b in bars[-4:]) if sign == 1 else max(b.high for b in bars[-4:])
        return PdfSignal("LONG" if sign == 1 else "SHORT", extreme, value, reward=Decimal("2"))

    def _stochastic(self, bars: list[Candle], value: float) -> PdfSignal:
        higher = fifteen(bars)
        if len(higher) < 200:
            raise ValueError("need 200 completed 15m candles for StochRSI context")
        higher_closes = [b.close for b in higher]
        trend = ema(higher_closes, 200)
        closes = [b.close for b in bars]
        vwap, _ = session_vwap(bars)
        average = ema(closes, 50)
        sign = (
            1
            if higher_closes[-1] > trend and closes[-1] > max(vwap, average)
            else (-1 if higher_closes[-1] < trend and closes[-1] < min(vwap, average) else 0)
        )
        oscillator = stoch_rsi(closes)
        old_k, old_d = oscillator[-2]
        k, d = oscillator[-1]
        reset = (
            min(k for k, _ in oscillator[-4:-1]) < 0.2
            if sign == 1
            else max(k for k, _ in oscillator[-4:-1]) > 0.8
        )
        boundary = bars[-2].high if sign == 1 else bars[-2].low
        if (
            not sign
            or not reset
            or (old_k - old_d) * sign > 0
            or (k - d) * sign <= 0
            or (closes[-1] - boundary) * sign <= 0
        ):
            raise ValueError("no StochRSI reset/cross and prior-candle breakout")
        extreme = min(b.low for b in bars[-5:]) if sign == 1 else max(b.high for b in bars[-5:])
        return PdfSignal("LONG" if sign == 1 else "SHORT", extreme, value, reward=Decimal("2"))

    def _macd(self, bars: list[Candle], value: float) -> PdfSignal:
        closes = [b.close for b in bars]
        line, histogram = macd_histogram(closes)
        sign = 1 if all(v > 0 for v in line[-4:]) else (-1 if all(v < 0 for v in line[-4:]) else 0)
        h = [v * sign for v in histogram[-4:]]
        if not sign or not h[0] > h[1] > h[2] > 0 or h[3] <= h[2]:
            raise ValueError("no MACD histogram contraction then re-acceleration")
        if bars[-1].open_time // 86_400_000 != bars[-4].open_time // 86_400_000:
            raise ValueError("momentum leg cannot span VWAP session reset")
        for back in (0, 1, 2):
            prefix = bars[: len(bars) - back]
            center, _ = session_vwap(prefix)
            if (prefix[-1].close - center) * sign <= 0 or (
                prefix[-1].close - ema([b.close for b in prefix], 20)
            ) * sign <= 0:
                raise ValueError("pullback lost VWAP or EMA20")
        center, _ = session_vwap(bars)
        earlier, _ = session_vwap(bars[:-3])
        prior = bars[-4:-1]
        boundary = max(b.high for b in prior) if sign == 1 else min(b.low for b in prior)
        if (
            (closes[-1] - boundary) * sign <= 0
            or relative_volume(bars) < 1.2
            or (center - earlier) * sign <= 0.05 * value
            or abs(closes[-1] - center) > 3 * value
        ):
            raise ValueError("no non-extended VWAP micro-breakout with RVOL")
        extreme = min(b.low for b in prior) if sign == 1 else max(b.high for b in prior)
        return PdfSignal("LONG" if sign == 1 else "SHORT", extreme, value, reward=Decimal("2"))

    def _ichimoku(self, bars: list[Candle], value: float) -> PdfSignal:
        cloud = ichimoku(bars)
        higher = fifteen(bars)
        context_cloud = ichimoku(higher)
        trigger, previous = bars[-1], bars[-2]
        sign = (
            1
            if (
                trigger.close > max(cloud.span_a, cloud.span_b)
                and cloud.tenkan > cloud.kijun
                and cloud.forward_a > cloud.forward_b
            )
            else (
                -1
                if (
                    trigger.close < min(cloud.span_a, cloud.span_b)
                    and cloud.tenkan < cloud.kijun
                    and cloud.forward_a < cloud.forward_b
                )
                else 0
            )
        )
        if not sign:
            raise ValueError("price, TK or projected cloud are not aligned")
        boundary = (
            max(context_cloud.span_a, context_cloud.span_b)
            if sign == 1
            else (min(context_cloud.span_a, context_cloud.span_b))
        )
        if (higher[-1].close - boundary) * sign <= 0 or (
            context_cloud.tenkan - context_cloud.kijun
        ) * sign <= 0:
            raise ValueError("15m Ichimoku trend does not confirm")
        if (
            abs(cloud.span_a - cloud.span_b) < 0.1 * value
            or (cloud.forward_a - ichimoku(bars[:-3]).forward_a) * sign <= 0.05 * value
        ):
            raise ValueError("thin or flat cloud")
        alignments = [
            ichimoku(bars[:i]).tenkan > ichimoku(bars[:i]).kijun
            for i in range(len(bars) - 10, len(bars) + 1)
        ]
        if sum(a != b for a, b in pairwise(alignments)) > 2:
            raise ValueError("repeated Tenkan/Kijun crossovers")
        prior_kijun = ichimoku(bars[:-1]).kijun
        if (
            previous.low > prior_kijun + 0.1 * value
            or previous.high < prior_kijun - 0.1 * value
            or (trigger.close - cloud.kijun) * sign <= 0
            or (trigger.close - trigger.open) * sign <= 0
            or trigger.volume <= median(b.volume for b in bars[-21:-1])
        ):
            raise ValueError("no Kijun pullback/reclaim with above-median volume")
        extreme = min(previous.low, trigger.low) if sign == 1 else max(previous.high, trigger.high)
        return PdfSignal("LONG" if sign == 1 else "SHORT", extreme, value, reward=Decimal("2"))

    def _sweep(self, bars: list[Candle], value: float) -> PdfSignal:
        trigger = bars[-1]
        vwap, _ = session_vwap(bars)
        for age in (1, 2, 3):
            index = len(bars) - 1 - age
            prior = bars[index - 20 : index]
            high, low = max(b.high for b in prior), min(b.low for b in prior)
            sweep = bars[index]
            sign = (
                1
                if sweep.low < low < sweep.close < high
                else (-1 if sweep.high > high > sweep.close > low else 0)
            )
            if not sign:
                continue
            following = bars[index + 1 :]
            level = low if sign == 1 else high
            extreme = sweep.low if sign == 1 else sweep.high
            if any(
                (b.close - level) * sign <= 0
                or ((b.low - extreme) * sign < 0 if sign == 1 else (b.high - extreme) * sign < 0)
                for b in following
            ):
                continue
            micro = (
                max(b.high for b in bars[index - 3 : index])
                if sign == 1
                else (min(b.low for b in bars[index - 3 : index]))
            )
            if (
                any((b.close - micro) * sign > 0 for b in bars[index:-1])
                or (trigger.close - micro) * sign <= 0
                or (trigger.close - vwap) * sign <= 0
                or relative_volume(bars) < 1.2
            ):
                continue
            return PdfSignal(
                "LONG" if sign == 1 else "SHORT",
                extreme,
                value,
                target=(high + low) / 2,
                stop_atr=0,
            )
        raise ValueError("no failed range sweep followed by first structure/VWAP reclaim")

    def describe(self) -> dict[str, object]:
        return {
            **Strategy.describe(self),
            "timeframe": "5m",
            "research_only": True,
            "group": "advanced-pdf-five-minute",
            "source": SOURCE,
            "source_pages": self.pages,
            "assumptions": [
                "Candle-based adaptation, not established profitable; testnet execution only.",
                "Closed 5m signals; context uses only complete UTC-aligned 15m candles.",
                "Session VWAP resets at 00:00 UTC; Ichimoku visible cloud is shifted causally.",
                "Single full bracket exit; partials, dynamic exits and trailing are not simulated.",
                "Platform 0.45% stop floor and 1.5R minimum; entry chase capped at 0.5 ATR.",
                "Rules and limitations: docs/research/advanced-pdf-five-minute/README.md.",
            ],
        }


ADVANCED_FIVE_MINUTE_STRATEGIES = {
    strategy.id: strategy for strategy in (AdvancedFiveMinute(number) for number in range(1, 8))
}
