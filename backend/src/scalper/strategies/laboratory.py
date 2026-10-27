"""Fifty fixed, source-inspired hypotheses. Deliberately NOT a trading registry.

Rules are our short-timeframe adaptations, not reproductions of published returns.
Both directions use the same rules; contradictory signals are rejected.
"""

from decimal import Decimal
from functools import lru_cache
from itertools import pairwise
from statistics import pstdev

from scalper.exchange.market_data import Candle
from scalper.indicators.core import atr, ema, ema_series, rsi, sma
from scalper.strategies.base import Levels, Setup, Strategy, StrategyContext

TV = "https://www.tradingview.com/support/solutions/"
CS = "https://chartschool.stockcharts.com/table-of-contents/"
SOURCES = {
    "ma": TV + "43000502589-moving-averages/",
    "macd": TV + "43000502344-moving-average-convergence-divergence-macd-indicator/",
    "rsi": TV + "43000502338-relative-strength-index-rsi/",
    "rsi2": CS + "trading-strategies-and-models/trading-strategies/rsi-2",
    "stoch": TV + "43000502332-stochastic-stoch/",
    "cci": TV + "43000502001-commodity-channel-index-cci/",
    "roc": TV + "43000502343-rate-of-change-roc/",
    "channel": CS + "technical-indicators-and-overlays/technical-overlays/price-channels",
    "bb": TV + "43000501840-bollinger-bands-bb/",
    "kc": CS + "technical-indicators-and-overlays/technical-overlays/keltner-channels",
    "squeeze": CS + "technical-indicators-and-overlays/technical-indicators/ttm-squeeze",
    "nr": CS + "trading-strategies-and-models/trading-strategies/narrow-range-day-nr7",
    "candle": CS + "chart-analysis/candlestick-charts/candlestick-pattern-dictionary",
    "vwap": CS
    + "technical-indicators-and-overlays/technical-overlays/volume-weighted-average-price-vwap",
    "obv": TV + "43000502593-on-balance-volume-obv/",
    "mfi": CS + "technical-indicators-and-overlays/technical-indicators/money-flow-index-mfi",
    "cmf": CS + "technical-indicators-and-overlays/technical-indicators/chaikin-money-flow-cmf",
    "volume": TV + "43000591617-volume/",
}

# Long rules below; shorts mirror the comparisons and oscillator thresholds.
# ID numbers are identifiers, never performance ranks.
RECIPES = [
    ("EMA cross", "ma", 1, "EMA9 crosses above EMA21."),
    ("EMA trend reclaim", "ma", 1, "Close crosses EMA20 with close above EMA50."),
    ("EMA ribbon alignment", "ma", 1, "EMA9 > EMA21 > EMA50 becomes newly aligned."),
    ("EMA slope breakout", "ma", 5, "Rising EMA20 and close crosses prior bar high."),
    ("SMA cross", "ma", 5, "SMA10 crosses above SMA30."),
    ("VWMA reclaim", "ma", 1, "Close crosses volume-weighted close MA20 above EMA50."),
    ("MACD signal cross", "macd", 1, "MACD12/26 crosses its EMA9 signal."),
    ("MACD zero cross", "macd", 5, "MACD12/26 crosses zero."),
    ("MACD histogram turn", "macd", 1, "Negative histogram turns upward above EMA50."),
    ("RSI midpoint trend", "rsi", 1, "RSI14 crosses 50 above EMA50."),
    ("RSI extreme recovery", "rsi", 5, "RSI14 crosses back above 30."),
    ("RSI2 trend recovery", "rsi2", 1, "RSI2 crosses back above 10 above EMA50."),
    ("RSI trend pullback", "rsi", 1, "RSI14 crosses back above 40 above EMA50."),
    ("RSI momentum expansion", "rsi", 5, "RSI14 crosses above 60 with a prior-high breakout."),
    ("Stochastic extreme recovery", "stoch", 1, "Raw stochastic K14 crosses back above 20."),
    ("Stochastic signal cross", "stoch", 1, "K14 crosses its SMA3 while previous K <20."),
    ("Stochastic midpoint trend", "stoch", 5, "K14 crosses 50 above EMA50."),
    ("CCI momentum break", "cci", 5, "CCI20 crosses above +100."),
    ("CCI zero trend", "cci", 1, "CCI20 crosses zero above EMA50."),
    ("CCI extreme recovery", "cci", 5, "CCI20 crosses back above -100."),
    ("ROC zero cross", "roc", 1, "ROC10 crosses zero."),
    ("ROC acceleration breakout", "roc", 1, "Positive ROC5 accelerates with prior-high breakout."),
    ("Donchian20 breakout", "channel", 5, "Close exceeds prior 20 highs."),
    (
        "Donchian10 volume breakout",
        "channel",
        1,
        "Close exceeds prior 10 highs; volume >1.5x prior20 mean.",
    ),
    (
        "Donchian midpoint reclaim",
        "channel",
        5,
        "Close crosses prior20 range midpoint above EMA50.",
    ),
    (
        "Donchian failed breakdown",
        "channel",
        1,
        "Low sweeps prior20 low; bullish close reclaims it.",
    ),
    ("Bollinger continuation", "bb", 5, "Close crosses above upper SMA20 +2 sigma band."),
    ("Bollinger re-entry", "bb", 1, "Close crosses back inside lower SMA20 -2 sigma band."),
    ("Bollinger midpoint trend", "bb", 1, "Close crosses SMA20 above EMA50."),
    (
        "Bollinger contraction breakout",
        "bb",
        5,
        "Prior band width <1% and close exceeds prior20 highs.",
    ),
    ("Keltner continuation", "kc", 5, "Close crosses above EMA20 +1.5 ATR14."),
    ("Keltner re-entry", "kc", 1, "Close crosses back inside EMA20 -1.5 ATR14."),
    ("Keltner center bounce", "kc", 1, "Low touches EMA20; bullish close above EMA20 above EMA50."),
    (
        "BB KC squeeze release",
        "squeeze",
        5,
        "BB20 leaves KC20 after containment; close exceeds prior high.",
    ),
    ("NR4 breakout", "nr", 1, "Previous bar is narrowest of four; close breaks its high."),
    ("NR7 breakout", "nr", 5, "Previous bar is narrowest of seven; close breaks its high."),
    (
        "Inside bar breakout",
        "candle",
        1,
        "Previous bar inside mother bar; close breaks mother high.",
    ),
    (
        "Double inside breakout",
        "candle",
        5,
        "Two nested inside bars; close breaks original mother high.",
    ),
    (
        "Outside bar follow-through",
        "candle",
        1,
        "Previous bullish bar engulfs prior range; close breaks its high.",
    ),
    (
        "Body engulfing trend",
        "candle",
        5,
        "Bullish body engulfs previous bearish body above EMA50.",
    ),
    ("Rolling VWAP reclaim", "vwap", 1, "Close crosses rolling20 typical-price VWAP."),
    (
        "Rolling VWAP bounce",
        "vwap",
        1,
        "Low touches VWAP20; bullish close above VWAP20 above EMA50.",
    ),
    ("VWAP band re-entry", "vwap", 5, "Close re-enters rolling20 VWAP -2 weighted sigma band."),
    (
        "VWAP volume breakout",
        "volume",
        1,
        "Prior-high breakout above VWAP20; volume >1.5x prior20 mean.",
    ),
    ("OBV channel break", "obv", 5, "OBV exceeds its prior20 maximum; bullish candle."),
    ("OBV price confirmation", "obv", 1, "Price and OBV both exceed prior10 maxima."),
    ("MFI extreme recovery", "mfi", 5, "MFI14 crosses back above 20."),
    ("MFI midpoint trend", "mfi", 1, "MFI14 crosses 50 above EMA50."),
    ("Chaikin flow trend", "cmf", 5, "CMF20 crosses zero above EMA50."),
    (
        "ATR expansion breakout",
        "nr",
        1,
        "Range >1.5 prior ATR14; close breaks prior high above EMA50.",
    ),
]


def cross(previous: float, current: float, old_level: float, level: float) -> bool:
    return previous <= old_level and current > level


def features(bars: list[Candle]) -> dict[str, float]:
    """Only uses the supplied closed prefix. Flat oscillators are neutral."""
    closes = [b.close for b in bars]
    highs, lows = [b.high for b in bars], [b.low for b in bars]
    out = {f"e{n}": ema(closes, n) for n in (9, 20, 21, 50)}
    out.update({f"s{n}": sma(closes, n) for n in (10, 20, 30)})
    value = atr(highs, lows, closes)
    mid = out["s20"]
    sigma = pstdev(closes[-20:])
    out.update(
        atr=value,
        bbup=mid + 2 * sigma,
        bbdn=mid - 2 * sigma,
        width=4 * sigma / mid,
        kcup=out["e20"] + 1.5 * value,
        kcdn=out["e20"] - 1.5 * value,
    )
    fast, slow = ema_series(closes, 12), ema_series(closes, 26)
    macd = [a - b for a, b in zip(fast[14:], slow, strict=True)]
    out.update(macd=macd[-1], hist=macd[-1] - ema(macd, 9))
    out.update(rsi=rsi(closes), rsi2=rsi(closes, 2))
    ks = []
    for end in range(len(bars) - 2, len(bars) + 1):
        window = bars[end - 14 : end]
        lo, hi = min(b.low for b in window), max(b.high for b in window)
        ks.append(100 * (window[-1].close - lo) / (hi - lo) if hi > lo else 50)
    out.update(k=ks[-1], d=sum(ks) / 3)
    typical = [(b.high + b.low + b.close) / 3 for b in bars]
    mean = sma(typical, 20)
    deviation = sum(abs(p - mean) for p in typical[-20:]) / 20
    out["cci"] = (typical[-1] - mean) / (0.015 * deviation) if deviation else 0
    out.update(roc10=closes[-1] / closes[-11] - 1, roc5=closes[-1] / closes[-6] - 1)
    volume = sum(b.volume for b in bars[-20:])
    if volume <= 0:
        raise ValueError("no volume in research window")
    vwap = sum(p * b.volume for p, b in zip(typical[-20:], bars[-20:], strict=True)) / volume
    vsigma = (
        sum(b.volume * (p - vwap) ** 2 for p, b in zip(typical[-20:], bars[-20:], strict=True))
        / volume
    ) ** 0.5
    out.update(
        vwap=vwap,
        vup=vwap + 2 * vsigma,
        vdn=vwap - 2 * vsigma,
        vwma=sum(b.close * b.volume for b in bars[-20:]) / volume,
    )
    obv = [0.0]
    for a, b in pairwise(bars):
        obv.append(
            obv[-1] + (b.volume if b.close > a.close else -b.volume if b.close < a.close else 0)
        )
    out.update(
        obv=obv[-1],
        obvhi20=max(obv[-21:-1]),
        obvlo20=min(obv[-21:-1]),
        obvhi10=max(obv[-11:-1]),
        obvlo10=min(obv[-11:-1]),
    )
    positive = negative = 0.0
    for i in range(len(bars) - 14, len(bars)):
        flow = typical[i] * bars[i].volume
        if typical[i] > typical[i - 1]:
            positive += flow
        elif typical[i] < typical[i - 1]:
            negative += flow
    out["mfi"] = 100 * positive / (positive + negative) if positive + negative else 50
    out["cmf"] = (
        sum(
            (2 * b.close - b.high - b.low) / (b.high - b.low) * b.volume if b.high > b.low else 0
            for b in bars[-20:]
        )
        / volume
    )
    return out


@lru_cache(maxsize=10000)
def decisions(closed_bars: tuple[Candle, ...]) -> tuple[tuple[bool, bool], ...]:
    """Ordered long/short predicates, exactly one per catalog recipe."""
    bars = list(closed_bars)
    c, p, pp, ppp = bars[-1], bars[-2], bars[-3], bars[-4]
    f, g, h = features(bars), features(bars[:-1]), features(bars[:-2])
    up, down = c.close > f["e50"], c.close < f["e50"]
    bull, bear = c.close > c.open, c.close < c.open
    bh, bl = c.close > p.high, c.close < p.low
    vol = c.volume > 1.5 * sum(b.volume for b in bars[-21:-1]) / 20
    hi, lo = max(b.high for b in bars[-21:-1]), min(b.low for b in bars[-21:-1])
    hi10, lo10 = max(b.high for b in bars[-11:-1]), min(b.low for b in bars[-11:-1])
    oldmid = (max(b.high for b in bars[-22:-2]) + min(b.low for b in bars[-22:-2])) / 2
    mid = (hi + lo) / 2

    def indicator(key: str, low: float = 0, high: float | None = None) -> tuple[bool, bool]:
        high = low if high is None else high
        return cross(g[key], f[key], low, low), cross(-g[key], -f[key], -high, -high)

    def price(key: str) -> tuple[bool, bool]:
        return (
            cross(p.close, c.close, g[key], f[key]),
            cross(-p.close, -c.close, -g[key], -f[key]),
        )

    def trend(pair: tuple[bool, bool]) -> tuple[bool, bool]:
        return pair[0] and up, pair[1] and down

    def bands(lower: str, upper: str, reentry: bool) -> tuple[bool, bool]:
        return (price(lower)[0], price(upper)[1]) if reentry else (price(upper)[0], price(lower)[1])

    def narrow(n: int) -> tuple[bool, bool]:
        nr = p.high - p.low < min(b.high - b.low for b in bars[-n - 1 : -2])
        return nr and bh, nr and bl

    inside = p.high < pp.high and p.low > pp.low
    double = inside and pp.high < ppp.high and pp.low > ppp.low
    squeeze = g["bbup"] < g["kcup"] and g["bbdn"] > g["kcdn"]
    release = squeeze and (f["bbup"] >= f["kcup"] or f["bbdn"] <= f["kcdn"])
    return (
        (
            cross(g["e9"], f["e9"], g["e21"], f["e21"]),
            cross(-g["e9"], -f["e9"], -g["e21"], -f["e21"]),
        ),
        trend(price("e20")),
        (
            f["e9"] > f["e21"] > f["e50"] and not g["e9"] > g["e21"] > g["e50"],
            f["e9"] < f["e21"] < f["e50"] and not g["e9"] < g["e21"] < g["e50"],
        ),
        (f["e20"] > g["e20"] and bh, f["e20"] < g["e20"] and bl),
        (
            cross(g["s10"], f["s10"], g["s30"], f["s30"]),
            cross(-g["s10"], -f["s10"], -g["s30"], -f["s30"]),
        ),
        trend(price("vwma")),
        indicator("hist"),
        indicator("macd"),
        (
            f["hist"] < 0 and f["hist"] > g["hist"] <= h["hist"] and up,
            f["hist"] > 0 and f["hist"] < g["hist"] >= h["hist"] and down,
        ),
        trend(indicator("rsi", 50)),
        indicator("rsi", 30, 70),
        trend(indicator("rsi2", 10, 90)),
        trend(indicator("rsi", 40, 60)),
        (indicator("rsi", 60, 40)[0] and bh, indicator("rsi", 60, 40)[1] and bl),
        indicator("k", 20, 80),
        (
            cross(g["k"], f["k"], g["d"], f["d"]) and g["k"] < 20,
            cross(-g["k"], -f["k"], -g["d"], -f["d"]) and g["k"] > 80,
        ),
        trend(indicator("k", 50)),
        indicator("cci", 100, -100),
        trend(indicator("cci")),
        indicator("cci", -100, 100),
        indicator("roc10"),
        (f["roc5"] > max(0, g["roc5"]) and bh, f["roc5"] < min(0, g["roc5"]) and bl),
        (c.close > hi, c.close < lo),
        (c.close > hi10 and vol, c.close < lo10 and vol),
        (
            cross(p.close, c.close, oldmid, mid) and up,
            cross(-p.close, -c.close, -oldmid, -mid) and down,
        ),
        (c.low < lo < c.close and bull, c.high > hi > c.close and bear),
        bands("bbdn", "bbup", False),
        bands("bbdn", "bbup", True),
        trend(price("s20")),
        (g["width"] < 0.01 and c.close > hi, g["width"] < 0.01 and c.close < lo),
        bands("kcdn", "kcup", False),
        bands("kcdn", "kcup", True),
        (
            c.low <= f["e20"] < c.close and bull and up,
            c.high >= f["e20"] > c.close and bear and down,
        ),
        (release and bh, release and bl),
        narrow(4),
        narrow(7),
        (inside and c.close > pp.high, inside and c.close < pp.low),
        (double and c.close > ppp.high, double and c.close < ppp.low),
        (
            p.high > pp.high and p.low < pp.low and p.close > p.open and bh,
            p.high > pp.high and p.low < pp.low and p.close < p.open and bl,
        ),
        (
            bull and p.close < p.open and c.open <= p.close and c.close > p.open and up,
            bear and p.close > p.open and c.open >= p.close and c.close < p.open and down,
        ),
        price("vwap"),
        (
            c.low <= f["vwap"] < c.close and bull and up,
            c.high >= f["vwap"] > c.close and bear and down,
        ),
        bands("vdn", "vup", True),
        (bh and c.close > f["vwap"] and vol, bl and c.close < f["vwap"] and vol),
        (f["obv"] > f["obvhi20"] and bull, f["obv"] < f["obvlo20"] and bear),
        (c.close > hi10 and f["obv"] > f["obvhi10"], c.close < lo10 and f["obv"] < f["obvlo10"]),
        indicator("mfi", 20, 80),
        trend(indicator("mfi", 50)),
        trend(indicator("cmf")),
        (
            c.high - c.low > 1.5 * g["atr"] and bh and up,
            c.high - c.low > 1.5 * g["atr"] and bl and down,
        ),
    )


class LabStrategy(Strategy):
    history_1m = 100
    history_5m = 100

    def __init__(self, number: int) -> None:
        if not 1 <= number <= len(RECIPES):
            raise ValueError("invalid laboratory candidate")
        self.number = number
        self.id = f"lab-{number:02d}"
        self.name, _, minutes, self.summary = RECIPES[number - 1]
        self.signal_interval_s = minutes * 60

    def assess(self, ctx: StrategyContext) -> Setup:
        ctx = self.research_context(ctx)
        if not self.has_enough_history(ctx):
            return Setup.waiting("insufficient history")
        if error := self.data_error(ctx):
            return Setup.waiting(error)
        bars = ctx.candles_1m if self.signal_interval_s == 60 else ctx.candles_5m
        if ctx.candles_1m[-1].close_time != bars[-1].close_time:
            return Setup.waiting("waiting for closed entry timeframe")
        try:
            long, short = decisions(tuple(bars))[self.number - 1]
        except ValueError as exc:
            return Setup.waiting(str(exc))
        if long == short:
            return Setup.waiting("no unambiguous closed-bar trigger")
        return Setup.entry("LONG" if long else "SHORT", self.summary)

    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        ctx = self.research_context(ctx)
        bars = ctx.candles_1m if self.signal_interval_s == 60 else ctx.candles_5m
        if direction not in {"LONG", "SHORT"} or not entry.is_finite() or entry <= 0:
            raise ValueError("invalid lab entry")
        value = atr([b.high for b in bars], [b.low for b in bars], [b.close for b in bars])
        risk = max(Decimal(str(1.5 * value)), entry * Decimal("0.0045"))
        sign = Decimal(1 if direction == "LONG" else -1)
        stop, target = entry - sign * risk, entry + sign * 2 * risk
        if min(stop, target) <= 0:
            raise ValueError("nonpositive lab bracket")
        return Levels(stop, target)

    def describe(self) -> dict[str, object]:
        return {
            **super().describe(),
            "timeframe": f"{self.signal_interval_s // 60}m",
            "source": SOURCES[RECIPES[self.number - 1][1]],
            "research_only": True,
            "assumptions": [
                "Our crypto adaptation; not a validated published system.",
                "Shared 1.5 ATR / 0.45% stop floor, fixed 2R target.",
                "24/7 closed candles; no exchange session filter.",
            ],
        }


LAB_STRATEGIES = {f"lab-{n:02d}": LabStrategy(n) for n in range(1, 51)}
