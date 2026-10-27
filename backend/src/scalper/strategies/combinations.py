"""User-requested 1m combinations: isolated research rules, never live registration."""

from statistics import pstdev

from scalper.exchange.market_data import Candle
from scalper.indicators.core import adx, ema, rsi
from scalper.strategies.base import Setup, Strategy, StrategyContext
from scalper.strategies.laboratory import SOURCES, LabStrategy, cross, features
from scalper.strategies.research import LiquiditySweepReclaim, pivots

RULES = {
    1: ("VWAP + EMA9/21", "ma", "EMA9/21 cross aligned with rolling VWAP20."),
    2: (
        "EMA9/21 pullback",
        "ma",
        "Previous candle touches EMA9/21 zone; trend continuation close.",
    ),
    3: (
        "VWAP reclaim with volume",
        "vwap",
        "Previous VWAP reclaim; next close holds with 1.5x volume.",
    ),
    4: ("EMA9 + RSI", "rsi", "Price reclaims EMA9 with RSI14 above50 (below50 for shorts)."),
    5: ("EMA20/50 1m pullback", "ma", "1m EMA20/50 trend; previous EMA20 touch and continuation."),
    6: ("VWAP + RSI", "vwap", "VWAP20 reclaim with RSI14 midpoint confirmation."),
    7: ("Bollinger reversal + RSI", "bb", "Band re-entry with improving RSI below50 for longs."),
    8: (
        "Bollinger squeeze + volume",
        "squeeze",
        "Prior width bottom20% of120 widths; band/20-bar break and volume.",
    ),
    9: (
        "EMA9 + Bollinger pullback",
        "bb",
        "Previous outer-band touch; EMA9 reclaim with aligned EMA9 slope.",
    ),
    10: (
        "MACD histogram acceleration",
        "macd",
        "Two histogram increases above zero and prior-high breakout.",
    ),
    11: ("VWAP + MACD", "macd", "Histogram zero cross with VWAP20 bias."),
    12: (
        "Confirmed RSI divergence",
        "rsi",
        "Two confirmed price pivots diverge from RSI; later structure break.",
    ),
    13: ("Stochastic reversal + EMA", "stoch", "K14/D3 extreme cross with EMA50 bias."),
    14: ("ADX + EMA momentum", "ma", "EMA9/21 cross, ADX14 >=25 and aligned DI."),
    15: (
        "Volume structure breakout",
        "volume",
        "Prior20 high/low break with volume >=1.5x prior20 mean.",
    ),
    16: (
        "5m level rejection",
        "candle",
        "Confirmed 5m pivot touch within0.1 ATR; 50% wick and volume.",
    ),
    17: (
        "Liquidity sweep + reversal",
        "channel",
        "Existing confirmed swing sweep/volume/5m context entry.",
    ),
    19: (
        "24/7 participation momentum",
        "volume",
        "VWAP bias, prior20 breakout, volume >=2x; no session clock gate.",
    ),
    20: ("15m/5m bias + 1m entry", "ma", "Closed15m and5m EMA20/50 align; 1m EMA9 reclaim."),
}


def fifteen(bars: list[Candle]) -> list[Candle]:
    groups: dict[int, list[Candle]] = {}
    for bar in bars:
        groups.setdefault(bar.open_time // 900000 * 900000, []).append(bar)
    result = []
    for start, group in groups.items():
        if [b.open_time for b in group] != [start, start + 300000, start + 600000]:
            continue
        result.append(
            Candle(
                start,
                group[0].open,
                max(b.high for b in group),
                min(b.low for b in group),
                group[-1].close,
                sum(b.volume for b in group),
                start + 899999,
                0,
                0,
            )
        )
    return result


def divergence(bars: list[Candle], sign: int) -> bool:
    # Both right-side candles must have closed BEFORE the entry trigger.
    known = bars[:-1]
    points = pivots(known, low=sign == 1)
    if len(points) < 2:
        return False
    a, b = points[-2:]
    if a < 14 or not 3 <= b - a <= 40 or len(bars) - 1 - b > 10:
        return False
    closes = [c.close for c in bars]
    old = known[a].low if sign == 1 else known[a].high
    new = known[b].low if sign == 1 else known[b].high
    if (new - old) * sign >= 0 or (rsi(closes[: b + 1]) - rsi(closes[: a + 1])) * sign <= 0:
        return False
    # A fixed confirmation level avoids repeated entries from the same divergence.
    boundary = (
        max(c.high for c in known[b : b + 3]) if sign == 1 else min(c.low for c in known[b : b + 3])
    )
    return (
        all((c.close - boundary) * sign <= 0 for c in known[b + 3 :])
        and (bars[-1].close - boundary) * sign > 0
    )


class Combination(LabStrategy):
    history_1m = 180
    candle_limit_1m = 200
    history_5m = 60

    def __init__(self, number: int) -> None:
        self.number = number
        self.name, self.source_key, self.summary = RULES[number]
        self.id = f"user-{number:02d}"
        self.signal_interval_s = 60
        if number == 20:
            self.history_5m = 156

    def assess(self, ctx: StrategyContext) -> Setup:
        ctx = self.research_context(ctx)
        if not self.has_enough_history(ctx):
            return Setup.waiting("insufficient combination history")
        if error := self.data_error(ctx):
            return Setup.waiting(error)
        if self.number == 17:
            return LiquiditySweepReclaim().assess(ctx)
        try:
            long, short = self.triggers(ctx)
        except ValueError as exc:
            return Setup.waiting(str(exc))
        return (
            Setup.entry("LONG" if long else "SHORT", self.summary)
            if long != short
            else Setup.waiting("no confirmed combination trigger")
        )

    def triggers(self, ctx: StrategyContext) -> tuple[bool, bool]:
        bars = ctx.candles_1m
        c, p = bars[-1], bars[-2]
        f, g, h = features(bars), features(bars[:-1]), features(bars[:-2])
        hi, lo = max(b.high for b in bars[-21:-1]), min(b.low for b in bars[-21:-1])
        average = sum(b.volume for b in bars[-21:-1]) / 20
        volume = average > 0 and c.volume >= 1.5 * average
        results = []
        for sign in (1, -1):

            def price(key: str, direction: int = sign) -> bool:
                return cross(
                    direction * p.close, direction * c.close, direction * g[key], direction * f[key]
                )

            bias = (c.close - f["vwap"]) * sign > 0
            momentum = (f["rsi"] - 50) * sign > 0
            breakout = (c.close - (p.high if sign == 1 else p.low)) * sign > 0
            structure = (c.close - (hi if sign == 1 else lo)) * sign > 0
            body = (c.close - c.open) * sign > 0
            trend = (f["e9"] - f["e21"]) * sign > 0
            ma_cross = cross(sign * g["e9"], sign * f["e9"], sign * g["e21"], sign * f["e21"])
            histogram = cross(sign * g["hist"], sign * f["hist"], 0, 0)
            band = "bbdn" if sign == 1 else "bbup"
            previous_touch = p.low <= max(g["e9"], g["e21"]) and p.high >= min(g["e9"], g["e21"])
            n = self.number
            if n == 1:
                ready = ma_cross and bias
            elif n == 2:
                ready = (
                    trend
                    and previous_touch
                    and body
                    and breakout
                    and (c.close - f["e9"]) * sign > 0
                )
            elif n == 3:
                reclaim = cross(
                    sign * bars[-3].close, sign * p.close, sign * h["vwap"], sign * g["vwap"]
                )
                ready = reclaim and bias and body and volume
            elif n == 4:
                ready = price("e9") and momentum
            elif n == 5:
                ready = (
                    (f["e20"] - f["e50"]) * sign > 0
                    and p.low <= g["e20"] <= p.high
                    and body
                    and breakout
                    and (c.close - f["e20"]) * sign > 0
                )
            elif n == 6:
                ready = price("vwap") and momentum
            elif n == 7:
                ready = (
                    price(band) and (f["rsi"] - g["rsi"]) * sign > 0 and (f["rsi"] - 50) * sign < 0
                )
            elif n == 8:
                widths = []
                for end in range(len(bars) - 121, len(bars) - 1):
                    closes = [b.close for b in bars[end - 20 : end]]
                    widths.append(4 * pstdev(closes) / (sum(closes) / 20))
                compressed = g["width"] <= sorted(widths)[23]
                ready = (
                    compressed
                    and structure
                    and volume
                    and (c.close - f["bbup" if sign == 1 else "bbdn"]) * sign > 0
                )
            elif n == 9:
                touch = p.low <= g[band] if sign == 1 else p.high >= g[band]
                ready = touch and price("e9") and (f["e9"] - g["e9"]) * sign > 0 and body
            elif n == 10:
                ready = sign * f["hist"] > sign * g["hist"] > sign * h["hist"] > 0 and breakout
            elif n == 11:
                ready = histogram and bias
            elif n == 12:
                ready = divergence(bars, sign)
            elif n == 13:
                ready = (
                    cross(sign * g["k"], sign * f["k"], sign * g["d"], sign * f["d"])
                    and (g["k"] < 20 if sign == 1 else g["k"] > 80)
                    and (c.close - f["e50"]) * sign > 0
                )
            elif n == 14:
                dmi = adx([b.high for b in bars], [b.low for b in bars], [b.close for b in bars])
                ready = ma_cross and dmi.adx >= 25 and (dmi.plus_di - dmi.minus_di) * sign > 0
            elif n == 15:
                ready = structure and volume
            elif n == 16:
                points = pivots(ctx.candles_5m, low=sign == 1)
                ready = False
                if points:
                    pivot = ctx.candles_5m[points[-1]]
                    level = pivot.low if sign == 1 else pivot.high
                    extreme = c.low if sign == 1 else c.high
                    wick = (
                        min(c.open, c.close) - c.low if sign == 1 else c.high - max(c.open, c.close)
                    )
                    ready = (
                        abs(extreme - level) <= 0.1 * f["atr"]
                        and c.high > c.low
                        and wick >= 0.5 * (c.high - c.low)
                        and (c.close - level) * sign > 0
                        and volume
                    )
            elif n == 19:
                ready = bias and structure and average > 0 and c.volume >= 2 * average
            elif n == 20:
                higher = fifteen(ctx.candles_5m)
                if len(higher) < 50:
                    raise ValueError("need 50 complete closed15m candles")
                align = []
                for series in (ctx.candles_5m, higher):
                    closes = [b.close for b in series]
                    align.append((ema(closes, 20) - ema(closes, 50)) * sign > 0)
                ready = all(align) and price("e9") and body
            else:
                raise ValueError("unsupported combination")
            results.append(ready)
        return results[0], results[1]

    def describe(self) -> dict[str, object]:
        return {
            **Strategy.describe(self),
            "timeframe": "1m",
            "research_only": True,
            "source": SOURCES[self.source_key],
            "group": "user-combinations",
            "assumptions": [
                "Rolling20 VWAP, not session VWAP; closed candles only.",
                "Shared laboratory ATR stop and2R target, including sweep entry.",
                "User18 is the existing event-data strategy; no candle substitute.",
            ],
        }


COMBINATIONS = {f"user-{n:02d}": Combination(n) for n in RULES}
