"""Five deterministic adaptations of the supplied 5-minute research guide.

Source rules and every discretionary threshold are documented in
docs/research/pdf-five-minute/README.md. No fitted parameters or profitability claims.
"""

from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from math import sqrt
from statistics import pstdev

from scalper.core.fees import MIN_STOP_FRACTION
from scalper.core.filters import Rounding, round_to_tick
from scalper.exchange.market_data import Candle
from scalper.indicators.core import adx, atr, ema, rsi
from scalper.strategies.base import Levels, Setup, Strategy, StrategyContext
from scalper.strategies.combinations import fifteen

SOURCE = "new strategies/5_minute_crypto_trading_strategies.pdf"
RECIPES = (
    ("ema-pullback", "EMA Trend Pullback", "EMA9/21/50 trend and rejection with volume.", "3"),
    (
        "vwap-continuation",
        "VWAP Trend Continuation",
        "UTC session VWAP trend-zone rejection.",
        "3-4",
    ),
    (
        "vwap-reversion",
        "VWAP Mean Reversion",
        "Flat session VWAP, 2-sigma re-entry and RSI.",
        "4-5",
    ),
    (
        "bb-rsi-reversion",
        "Bollinger + RSI Mean Reversion",
        "BB20/2 re-entry toward the middle band.",
        "5",
    ),
    (
        "volume-breakout",
        "Volume + ATR Breakout Retest",
        "Compressed 20-bar range, expansion and retest.",
        "6",
    ),
)


def volatility(bars: list[Candle]) -> float:
    return atr([b.high for b in bars], [b.low for b in bars], [b.close for b in bars])


def session_vwap(bars: list[Candle]) -> tuple[float, float]:
    """Volume-weighted typical-price mean/deviation since 00:00 UTC, never rolling."""
    start = bars[-1].open_time // 86_400_000 * 86_400_000
    session = [bar for bar in bars if bar.open_time >= start]
    if session[0].open_time != start or len(session) < 12:
        raise ValueError("need complete UTC session history and twelve closed session candles")
    total = sum(bar.volume for bar in session)
    if total <= 0:
        raise ValueError("session has no volume")
    prices = [(bar.high + bar.low + bar.close) / 3 for bar in session]
    mean = sum(price * bar.volume for price, bar in zip(prices, session, strict=True)) / total
    variance = (
        sum(bar.volume * (price - mean) ** 2 for price, bar in zip(prices, session, strict=True))
        / total
    )
    return mean, sqrt(variance)


def bollinger(bars: list[Candle]) -> tuple[float, float]:
    values = [bar.close for bar in bars[-20:]]
    return sum(values) / 20, pstdev(values)


def rejection(previous: Candle, trigger: Candle, sign: int) -> bool:
    """Directional body plus rejection wick >= body, or opposite-body engulfing."""
    body = (trigger.close - trigger.open) * sign
    wick = (
        min(trigger.open, trigger.close) - trigger.low
        if sign == 1
        else trigger.high - max(trigger.open, trigger.close)
    )
    engulf = (
        (previous.close - previous.open) * sign < 0
        and (trigger.open - previous.close) * sign <= 0
        and (trigger.close - previous.open) * sign >= 0
    )
    return body > 0 and (wick >= body or engulf)


@dataclass(frozen=True)
class PdfSignal:
    direction: str
    extreme: float
    atr: float
    target: float | None = None
    reward: Decimal = Decimal("1.5")
    stop_atr: float = 1.0


class PdfFiveMinute(Strategy):
    signal_interval_s = 300
    history_1m = 40
    history_5m = 100
    max_signal_age_s = 20

    def __init__(self, number: int) -> None:
        self.number = number
        suffix, name, self.summary, self.pages = RECIPES[number - 1]
        self.id = f"pdf5-{suffix}"
        self.name = f"PDF 5m: {name}"
        # Enough to include the entire UTC day even just before midnight.
        if number in (2, 3):
            self.history_5m = 300

    def _bars(self, ctx: StrategyContext) -> list[Candle]:
        ctx = self.research_context(ctx)
        if not self.has_enough_history(ctx):
            raise ValueError("insufficient closed history for PDF strategy")
        if error := self.data_error(ctx):
            raise ValueError(error)
        for bars, interval in ((ctx.candles_1m, 60_000), (ctx.candles_5m, 300_000)):
            if any(
                b.open_time % interval or b.close_time != b.open_time + interval - 1 for b in bars
            ):
                raise ValueError("invalid candle alignment")
            if any(b.open_time - a.open_time != interval for a, b in pairwise(bars)):
                raise ValueError("candle history has a gap")
        if ctx.candles_1m[-1].close_time != ctx.candles_5m[-1].close_time:
            raise ValueError("waiting for a newly closed 5-minute candle")
        return ctx.candles_5m

    def signal(self, ctx: StrategyContext) -> PdfSignal:
        bars = self._bars(ctx)
        value = volatility(bars)
        if value <= 0:
            raise ValueError("no usable ATR")
        if self.number == 5:
            return self._breakout(bars)
        closes = [bar.close for bar in bars]
        if self.number == 1:
            return self._ema(bars, closes, value)
        if self.number == 2:
            return self._vwap_trend(bars, closes, value)
        return self._reversion(bars, closes, value)

    def _ema(self, bars: list[Candle], closes: list[float], value: float) -> PdfSignal:
        fast, slow, trend = (ema(closes, n) for n in (9, 21, 50))
        slope = trend - ema(closes[:-3], 50)
        sign = 1 if fast > slow > trend else -1 if fast < slow < trend else 0
        if not sign or slope * sign <= 0.1 * value:
            raise ValueError("EMA trend is flat or unaligned")
        alignments = [
            ema(closes[:i], 9) > ema(closes[:i], 21)
            for i in range(len(closes) - 10, len(closes) + 1)
        ]
        if sum(a != b for a, b in pairwise(alignments)) > 2:
            raise ValueError("repeated EMA crossovers")
        previous, trigger = bars[-2:]
        zone = sorted((ema(closes[:-1], 9), ema(closes[:-1], 21)))
        if previous.low > zone[1] or previous.high < zone[0]:
            raise ValueError("no pullback into EMA9/21 zone")
        for i in range(len(bars) - 4, len(bars) - 1):
            if (bars[i].close - ema(closes[: i + 1], 50)) * sign <= 0:
                raise ValueError("pullback closed beyond EMA50")
        if (trigger.close - fast) * sign <= 0 or not rejection(previous, trigger, sign):
            raise ValueError("no rejection close beyond fast EMA")
        if trigger.volume < sum(bar.volume for bar in bars[-21:-1]) / 20:
            raise ValueError("confirmation volume below prior average")
        extreme = (
            min(bar.low for bar in bars[-4:]) if sign == 1 else max(bar.high for bar in bars[-4:])
        )
        return PdfSignal("LONG" if sign == 1 else "SHORT", extreme, value)

    def _vwap_trend(self, bars: list[Candle], closes: list[float], value: float) -> PdfSignal:
        if bars[-1].open_time // 86_400_000 != bars[-4].open_time // 86_400_000:
            raise ValueError("VWAP slope cannot cross the UTC session reset")
        center, _ = session_vwap(bars)
        earlier, _ = session_vwap(bars[:-3])
        previous_center, _ = session_vwap(bars[:-1])
        fast, slow = ema(closes, 9), ema(closes, 21)
        sign = (
            1
            if closes[-1] > center and fast > slow
            else (-1 if closes[-1] < center and fast < slow else 0)
        )
        if not sign or (center - earlier) * sign <= 0.1 * value:
            raise ValueError("no directional session VWAP trend")
        if abs(closes[-1] - center) > 2 * value:
            raise ValueError("price is extended beyond two ATR from VWAP")
        previous, trigger = bars[-2:]
        zone = sorted((ema(closes[:-1], 9), ema(closes[:-1], 21)))
        touches = previous.low <= previous_center <= previous.high or (
            previous.low <= zone[1] and previous.high >= zone[0]
        )
        if not touches or not rejection(previous, trigger, sign):
            raise ValueError("no VWAP or EMA-zone rejection")
        if (trigger.close - fast) * sign <= 0:
            raise ValueError("close has not reclaimed fast EMA")
        if trigger.volume < sum(bar.volume for bar in bars[-21:-1]) / 20:
            raise ValueError("confirmation volume below prior average")
        extreme = min(previous.low, trigger.low) if sign == 1 else max(previous.high, trigger.high)
        target = (
            max(bar.high for bar in bars[-22:-2])
            if sign == 1
            else (min(bar.low for bar in bars[-22:-2]))
        )
        return PdfSignal("LONG" if sign == 1 else "SHORT", extreme, value, target)

    def _reversion(self, bars: list[Candle], closes: list[float], value: float) -> PdfSignal:
        strength = adx([b.high for b in bars], [b.low for b in bars], closes).adx
        slope = ema(closes, 50) - ema(closes[:-3], 50)
        if strength >= 20 or abs(slope) > 0.2 * value:
            raise ValueError("mean reversion requires a low-trend regime")
        if self.number == 3:
            if bars[-1].open_time // 86_400_000 != bars[-4].open_time // 86_400_000:
                raise ValueError("VWAP re-entry cannot cross UTC reset")
            center, sigma = session_vwap(bars)
            old_center, old_sigma = session_vwap(bars[:-1])
            earlier, _ = session_vwap(bars[:-3])
            if abs(center - earlier) > 0.2 * value:
                raise ValueError("session VWAP is not flat")
        else:
            center, sigma = bollinger(bars)
            old_center, old_sigma = bollinger(bars[:-1])
            if sigma > old_sigma * 1.2:
                raise ValueError("Bollinger bands are expanding sharply")
        if min(sigma, old_sigma) <= 0:
            raise ValueError("no usable band width")
        previous, trigger = bars[-2:]
        old_rsi = rsi(closes[:-1])
        sign = (
            1
            if (
                previous.close < old_center - 2 * old_sigma
                and center - 2 * sigma < trigger.close < center
                and old_rsi <= 35
                and trigger.close > trigger.open
            )
            else -1
            if (
                previous.close > old_center + 2 * old_sigma
                and center < trigger.close < center + 2 * sigma
                and old_rsi >= 65
                and trigger.close < trigger.open
            )
            else 0
        )
        if not sign:
            raise ValueError("no oversold/overbought band re-entry")
        # Only complete UTC-aligned 15m candles may veto a contrarian entry.
        if self.number == 3:
            higher = fifteen(bars)
            block = higher[-1]
            prior = higher[-5:-1]
            boundary = min(b.low for b in prior) if sign == 1 else max(b.high for b in prior)
            if (block.close - boundary) * sign < 0 and block.volume > 1.5 * sum(
                b.volume for b in prior
            ) / 4:
                raise ValueError("expanding-volume higher-timeframe break against reversion")
        extreme = min(previous.low, trigger.low) if sign == 1 else max(previous.high, trigger.high)
        return PdfSignal("LONG" if sign == 1 else "SHORT", extreme, value, center)

    def _breakout(self, bars: list[Candle]) -> PdfSignal:
        trigger = bars[-1]
        for age in (1, 2, 3):
            index = len(bars) - 1 - age
            reference = bars[index - 20 : index]
            high, low = max(b.high for b in reference), min(b.low for b in reference)
            before_atr = volatility(bars[:index])
            earlier_atr = volatility(bars[: index - 20])
            if high - low > 6 * before_atr or before_atr > earlier_atr:
                continue
            breakout = bars[index]
            value = volatility(bars[: index + 1])
            if breakout.volume < 1.5 * sum(b.volume for b in reference) / 20 or value <= before_atr:
                continue
            sign = 1 if breakout.close > high else -1 if breakout.close < low else 0
            if not sign:
                continue
            boundary = high if sign == 1 else low
            intervening = bars[index + 1 : -1]
            if any((b.low <= boundary if sign == 1 else b.high >= boundary) for b in intervening):
                continue  # First retest only.
            extreme = trigger.low if sign == 1 else trigger.high
            touch = (extreme - boundary) * sign
            if (
                not -0.5 * value <= touch <= 0.1 * value
                or (trigger.close - boundary) * sign <= 0
                or (trigger.close - trigger.open) * sign <= 0
            ):
                continue
            return PdfSignal(
                "LONG" if sign == 1 else "SHORT", extreme, value, reward=Decimal("2"), stop_atr=1.25
            )
        raise ValueError("no compressed-range volume/ATR breakout with first retest")

    def assess(self, ctx: StrategyContext) -> Setup:
        try:
            signal = self.signal(ctx)
            entry = ctx.book.ask if signal.direction == "LONG" else ctx.book.bid
            self.levels(ctx, Decimal(str(entry)), signal.direction)
        except ValueError as exc:
            return Setup.waiting(str(exc))
        return Setup.entry(signal.direction, self.summary)

    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        signal = self.signal(ctx)
        if direction != signal.direction or not entry.is_finite() or entry <= 0:
            raise ValueError("invalid entry or signal direction")
        if abs(entry - Decimal(str(ctx.price))) > Decimal(str(signal.atr * 0.5)):
            raise ValueError("entry moved beyond half an ATR")
        sign = Decimal(1 if direction == "LONG" else -1)
        reference = Decimal(str(signal.extreme)) - sign * Decimal(str(0.1 * signal.atr))
        if (entry - reference) * sign <= 0:
            raise ValueError("entry crossed structural stop")
        risk = max(
            (entry - reference) * sign,
            Decimal(str(signal.stop_atr * signal.atr)),
            entry * MIN_STOP_FRACTION,
        )
        stop = entry - sign * risk
        target = (
            Decimal(str(signal.target))
            if signal.target is not None
            else entry + sign * signal.reward * risk
        )
        if min(stop, target) <= 0 or (target - entry) * sign < risk * Decimal("1.5"):
            raise ValueError("source target leaves less than platform minimum 1.5R")
        return Levels(stop, target)

    def execution_levels(
        self, ctx: StrategyContext, entry: Decimal, direction: str, tick_size: Decimal
    ) -> Levels:
        levels = self.levels(ctx, entry, direction)
        signal = self.signal(ctx)
        sign = Decimal(1 if direction == "LONG" else -1)
        stop = round_to_tick(levels.stop, tick_size, Rounding.DOWN if sign == 1 else Rounding.UP)
        raw_target = (
            levels.target
            if signal.target is not None
            else entry + sign * signal.reward * abs(entry - stop)
        )
        # Fixed reference targets round toward entry; never invent extra room.
        upward = (sign == -1) if signal.target is not None else (sign == 1)
        target = round_to_tick(raw_target, tick_size, Rounding.UP if upward else Rounding.DOWN)
        if (target - entry) * sign < abs(entry - stop) * Decimal("1.5"):
            raise ValueError("rounded source target leaves less than 1.5R")
        return Levels(stop, target)

    def describe(self) -> dict[str, object]:
        return {
            **super().describe(),
            "timeframe": "5m",
            "research_only": True,
            "group": "pdf-five-minute",
            "source": SOURCE,
            "source_pages": self.pages,
            "assumptions": [
                "Deterministic adaptation of the supplied PDF; not demonstrated profitable.",
                "Closed 5m signals; next executable entry; symmetric long/short rules.",
                "UTC-day typical-price session VWAP; no entries during session warm-up.",
                "Thresholds and omitted optional filters: docs/research/pdf-five-minute/README.md.",
                "Platform 0.45% stop floor and 1.5R minimum; reject inadequate mean targets.",
                "Single full exit, no partials or trailing; no funding/news/order-book history.",
                "PDF suggests 0.25%-0.50% risk; use explicit backtest risk settings.",
            ],
        }


PDF_FIVE_MINUTE_STRATEGIES = {
    strategy.id: strategy for strategy in (PdfFiveMinute(number) for number in range(1, 6))
}
