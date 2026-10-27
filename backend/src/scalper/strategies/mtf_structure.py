"""Original closed-candle rules inspired by LuxAlgo's public structure concepts."""

from decimal import Decimal
from itertools import pairwise

from scalper.exchange.market_data import Candle
from scalper.indicators.core import adx, ema
from scalper.strategies.base import Strategy, StrategyContext
from scalper.strategies.pdf_five_minute import PdfFiveMinute, PdfSignal, volatility
from scalper.strategies.research import pivots


def aggregate(bars: list[Candle], minutes: int) -> list[Candle]:
    """Build UTC-aligned higher bars from complete, consecutive closed 5m groups."""
    interval = minutes * 60_000
    if minutes < 5 or minutes % 5:
        raise ValueError("timeframe must be a multiple of five minutes")
    groups: dict[int, list[Candle]] = {}
    for bar in bars:
        groups.setdefault(bar.open_time // interval * interval, []).append(bar)
    result = []
    for start, group in groups.items():
        if len(group) != minutes // 5 or any(
            b.open_time != start + n * 300_000 or b.close_time != b.open_time + 299_999
            for n, b in enumerate(group)
        ):
            continue
        result.append(Candle(
            start, group[0].open, max(b.high for b in group), min(b.low for b in group),
            group[-1].close, sum(b.volume for b in group), start + interval - 1,
            sum(b.quote_volume for b in group), sum(b.taker_buy_base for b in group),
        ))
    return result


def trend(bars: list[Candle], label: str) -> int:
    closes = [b.close for b in bars[-100:]]
    if len(closes) < 100:
        raise ValueError(f"need 100 completed {label} candles")
    fast, slow = ema(closes, 20), ema(closes, 50)
    slope = fast - ema(closes[:-3], 20)
    if closes[-1] > fast > slow and slope > 0:
        return 1
    if closes[-1] < fast < slow and slope < 0:
        return -1
    raise ValueError(f"{label} direction is neutral")


def structure_level(bars: list[Candle], sign: int) -> tuple[float, int]:
    """Recent fresh swing break; pivot's two right bars must predate the break."""
    for index in range(len(bars) - 1, max(4, len(bars) - 7), -1):
        known = bars[:index]
        points = pivots(known, low=sign == -1)
        if not points:
            continue
        pivot = points[-1]
        level = known[pivot].high if sign == 1 else known[pivot].low
        # Once a swing was broken it cannot be recycled as a fresh breakout.
        if any((b.close - level) * sign > 0 for b in known[pivot + 1:]):
            continue
        if (bars[index].close - level) * sign <= 0:
            continue
        if any((b.close - level) * sign <= 0 for b in bars[index + 1:]):
            continue
        return level, bars[index].close_time
    raise ValueError("5m: no fresh confirmed swing break in the last six candles")


class MultiTimeframeStructure(PdfFiveMinute):
    id = "mtf-lux-inspired-structure"
    name = "MTF Structure: LuxAlgo-inspired 1h to 1m"
    summary = "1h/30m EMA bias, 15m ADX, 5m confirmed swing break, 1m volume retest."
    history_5m = 1212  # At least 100 complete hourly bars at any UTC boundary.
    history_1m = 200
    candle_limit_1m = 200
    signal_interval_s = 60
    max_signal_age_s = 20
    entry_minutes = 1

    def __init__(self) -> None:
        # This model reuses only the PDF base's bracket/rounding implementation.
        pass

    def signal(self, ctx: StrategyContext) -> PdfSignal:
        ctx = self.research_context(ctx)
        if not self.has_enough_history(ctx):
            raise ValueError("need 101 hours of 5m history and 200 closed 1m candles")
        if error := self.data_error(ctx):
            raise ValueError(error)
        for bars, interval in ((ctx.candles_5m, 300_000), (ctx.candles_1m, 60_000)):
            if any(b.open_time % interval or b.close_time != b.open_time + interval - 1
                   for b in bars):
                raise ValueError("invalid candle alignment")
            if any(b.open_time - a.open_time != interval for a, b in pairwise(bars)):
                raise ValueError("candle history has gaps or duplicates")
        age = ctx.candles_1m[-1].close_time - ctx.candles_5m[-1].close_time
        if not 0 <= age < 300_000:
            raise ValueError("future or stale 5m context")
        five = ctx.candles_5m
        sign = trend(aggregate(five, 60), "1h")
        if trend(aggregate(five, 30), "30m") != sign:
            raise ValueError("30m direction disagrees with 1h")
        fifteen = aggregate(five, 15)[-100:]
        closes = [b.close for b in fifteen]
        strength = adx([b.high for b in fifteen], [b.low for b in fifteen], closes)
        if (strength.adx < 20 or (strength.plus_di - strength.minus_di) * sign <= 0
                or (closes[-1] - ema(closes, 20)) * sign <= 0):
            raise ValueError("15m: weak or opposing trend (ADX/DI/EMA20)")
        entries = (ctx.candles_1m if self.entry_minutes == 1
                   else aggregate(five, self.entry_minutes)[-200:])
        if entries[-1].close_time != ctx.candles_1m[-1].close_time:
            raise ValueError(f"waiting for a newly closed {self.entry_minutes}m candle")
        boundary, confirmed_at = structure_level(five[-100:], sign)
        if entries[-2].open_time <= confirmed_at:
            raise ValueError("entry retest must follow the completed 5m structure break")
        try:
            return self.entry_signal(entries, boundary, sign)
        except ValueError as exc:
            raise ValueError(str(exc).replace("1m:", f"{self.entry_minutes}m:")) from exc

    @staticmethod
    def entry_signal(bars: list[Candle], boundary: float, sign: int) -> PdfSignal:
        previous, trigger = bars[-2:]
        value = volatility(bars)
        if value <= 0:
            raise ValueError("1m: no usable ATR")
        touch = previous.low if sign == 1 else previous.high
        if not -0.25 * value <= (touch - boundary) * sign <= 0.25 * value:
            raise ValueError("1m: waiting for retest of the broken 5m swing")
        prior_edge = previous.high if sign == 1 else previous.low
        if ((trigger.close - prior_edge) * sign <= 0
                or (trigger.close - trigger.open) * sign <= 0
                or not 0 < (trigger.close - boundary) * sign <= 1.5 * value):
            raise ValueError("1m: no directional confirmation or breakout is overextended")
        baseline = sum(b.volume for b in bars[-21:-1]) / 20
        if baseline <= 0 or trigger.volume < 1.2 * baseline:
            raise ValueError("1m: confirmation volume below 1.2x prior 20 bars")
        extreme = min(previous.low, trigger.low) if sign == 1 else max(previous.high, trigger.high)
        return PdfSignal("LONG" if sign == 1 else "SHORT", extreme, value,
                         reward=Decimal("2"), stop_atr=1.5)

    def describe(self) -> dict[str, object]:
        return {
            **Strategy.describe(self), "timeframe": "1m", "research_only": True,
            "group": "multi-timeframe-structure", "source": "docs/research/mtf-structure/README.md",
            "assumptions": [
                "Original LuxAlgo-inspired public-concept rules, not a LuxAlgo signal integration.",
                "1h -> 30m -> 15m -> 5m -> 1m; only complete UTC-aligned candles.",
                "1h and 30m EMA20/50 direction and EMA20 slope; neutral/disagreement means wait.",
                "15m ADX14 >=20, aligned DI and price beyond EMA20.",
                "5m fresh swing break within six bars; pivots confirmed with two right bars.",
                "1m retest within 0.25 ATR, directional prior-bar break, RVOL20 >=1.2.",
                "Stop beyond retest with 0.1 ATR buffer, minimum 1.5 ATR and 0.45%; target 2R.",
                "No entry chase beyond 0.5 ATR; frozen full-exit brackets, no partials/trailing.",
                "Experimental/testnet only; fixed unoptimized parameters, no profitability claim.",
            ],
        }
