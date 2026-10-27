"""Ten closed-1m price-action hypotheses; thresholds are our research adaptations.

These predicates provide entries only. The shared research wrapper owns stops,
targets, fees, position sizing and execution. No source performance is implied.
"""

from functools import lru_cache
from itertools import pairwise
from math import isfinite
from typing import NamedTuple

from scalper.exchange.market_data import Candle
from scalper.indicators.core import atr

BROOKS = "https://www.brookstradingcourse.com/price-action/10-best-price-action-trading-patterns/"
GLOSSARY = "https://www.brookstradingcourse.com/price-action-trading-terms-glossary/"
GRIMES = "https://www.adamhgrimes.com/"

RECIPES = (
    (
        "Microchannel first pullback",
        "https://onlinelibrary.wiley.com/doi/10.1002/9781119202592.ch16",
        "Six rising lows, four bull bodies and 1.5A rise; first one-bar pullback holds half "
        "the run; next bull close exceeds its high.",
    ),
    (
        "High-2 second attempt",
        BROOKS,
        "Eight-bar uptrend; lower high, H1, lower high, then bullish H2 close above prior "
        "high; all four correction lows hold trend origin.",
    ),
    (
        "Snap Anti pullback",
        GRIMES + "selective-intraday-trades-a-deep-dive-into-the-snap-pullback/",
        "Eight-bar downtrend; two-bar shock rises 2A above trend high; two small pullback "
        "bars retrace 20-50%; bullish close above both highs.",
    ),
    (
        "Nested impulse pause",
        GRIMES + "patterns-within-patterns/",
        "Eight-bar uptrend then three-bar impulse gains 1.5A; three-bar pause spans <=0.8A "
        "and holds impulse midpoint; bullish close clears pause high.",
    ),
    (
        "Three-push second entry",
        BROOKS,
        "Three descending two-sided lows separated by >=0.5A rebounds, diminishing "
        "extensions; close clears last rebound, higher-low retest, bullish resumption.",
    ),
    (
        "Broken-trend extreme retest",
        BROOKS,
        "Eight-bar downtrend; bullish close breaks last three highs; three-bar retest "
        "returns within 0.35A of old low without closing below it; close clears retest.",
    ),
    (
        "Final flag failure",
        BROOKS,
        "Fifteen-bar decline >=3A with ten lower closes; three-bar horizontal flag <=1A; "
        "close breaks below flag, then bullish close regains midpoint.",
    ),
    (
        "Body-gap retest",
        GLOSSARY,
        "Bull breakout above old range; five later bars keep bodies above breakout "
        "point body; wick retests its high, then bullish close clears retest high.",
    ),
    (
        "Failed-failure continuation",
        GLOSSARY,
        "Close clears old 15-bar range by 0.15A; bear reversal, close below reversal low, "
        "then bullish close above reversal high and range; failure depth <=1A.",
    ),
    (
        "Delayed failure-test hold",
        GRIMES + "failure-test-2/",
        "Close 0.1-0.5A below old 20-bar low; next close reclaims; later bar retests "
        "within 0.15A and holds; final bull close exceeds retest high.",
    ),
)


class _Bar(NamedTuple):
    o: float
    h: float
    low: float
    c: float


def _trend(bars: tuple[_Bar, ...], scale: float, up: bool = True) -> bool:
    sign = 1 if up else -1
    return (
        sign * (bars[-1].c - bars[0].o) >= 1.5 * scale
        and sum(sign * (b.c - a.c) > 0 for a, b in pairwise(bars)) >= 5
    )


def _longs(b: tuple[_Bar, ...], a: float) -> tuple[bool, ...]:
    """Fixed bounded sequences; each final candle is the fresh trigger."""
    t = b[-1]
    bull = t.c > t.o
    run, pull = b[-8:-2], b[-2]
    micro = (
        all(y.low > x.low for x, y in pairwise(run))
        and sum(x.c > x.o for x in run) >= 4
        and run[-1].c - run[0].o >= 1.5 * a
        and pull.low < run[-1].low
        and pull.low >= (run[0].low + max(x.h for x in run)) / 2
        and t.c > pull.h
    )

    context, p, h1, p2 = b[-12:-4], b[-4], b[-3], b[-2]
    high2 = (
        _trend(context, a)
        and p.h < context[-1].h
        and h1.h > p.h
        and p2.h < h1.h
        and t.c > p2.h
        and min(x.low for x in b[-4:]) > context[0].low
    )

    context, shock, pause = b[-13:-5], b[-5:-3], b[-3:-1]
    top = max(x.h for x in shock)
    depth = top - min(x.low for x in pause)
    span = top - shock[0].low
    anti = (
        _trend(context, a, up=False)
        and shock[-1].c - shock[0].o >= 2 * a
        and shock[-1].c > max(x.h for x in context)
        and 0.2 * span <= depth <= 0.5 * span
        and sum(x.h - x.low for x in pause) <= 0.7 * sum(x.h - x.low for x in shock)
        and min(x.c for x in pause) > (top + shock[0].low) / 2
        and t.c > max(x.h for x in pause)
    )

    context, impulse, pause = b[-15:-7], b[-7:-4], b[-4:-1]
    nested = (
        _trend(context, a)
        and impulse[-1].c - impulse[0].o >= 1.5 * a
        and max(x.h for x in pause) - min(x.low for x in pause) <= 0.8 * a
        and min(x.low for x in pause) > (impulse[0].low + max(x.h for x in impulse)) / 2
        and t.c > max(x.h for x in pause)
    )

    left, p1, r1, p2, r2, p3, turn, retest = b[-9:-1]
    wedge = (
        left.low > p1.low
        and r1.low > p1.low
        and r1.low > p2.low
        and r2.low > p2.low
        and r2.low > p3.low
        and turn.low > p3.low
        and p1.low > p2.low > p3.low
        and p1.low - p2.low > p2.low - p3.low
        and r1.h - p1.low >= 0.5 * a
        and r2.h - p2.low >= 0.5 * a
        and turn.c > r2.h
        and retest.low > p3.low
        and retest.low < turn.low
        and t.c > retest.h
    )

    context, turn, retests = b[-13:-5], b[-5], b[-4:-1]
    old_low = min(x.low for x in context)
    major = (
        _trend(context, a, up=False)
        and turn.c > max(x.h for x in context[-3:])
        and turn.c > turn.o
        and abs(min(x.low for x in retests) - old_low) <= 0.35 * a
        and all(x.c >= old_low for x in retests)
        and t.c > max(x.h for x in retests)
    )

    context, flag, breakout = b[-20:-5], b[-5:-2], b[-2]
    fh, fl = max(x.h for x in flag), min(x.low for x in flag)
    final_flag = (
        context[0].o - context[-1].c >= 3 * a
        and sum(y.c < x.c for x, y in pairwise(context)) >= 10
        and fh - fl <= a
        and abs(flag[-1].c - flag[0].c) <= 0.25 * a
        and breakout.c < fl
        and t.c > (fh + fl) / 2
    )

    old, breakout, after, retest = b[-22:-7], b[-7], b[-6:-1], b[-2]
    point = max(old, key=lambda x: x.h)
    body = max(point.o, point.c)
    body_gap = (
        breakout.c > point.h
        and breakout.c > breakout.o
        and breakout.h - breakout.low >= 1.2 * a
        and all(min(x.o, x.c) > body for x in after)
        and body - 0.3 * a <= retest.low <= point.h
        and t.c > retest.h
    )

    old, breakout, reversal, failed = b[-19:-4], b[-4], b[-3], b[-2]
    level = max(x.h for x in old)
    failure = (
        breakout.c > level + 0.15 * a
        and breakout.c > breakout.o
        and reversal.c < reversal.o
        and failed.c < reversal.low
        and min(reversal.low, failed.low) >= level - a
        and t.c > max(reversal.h, level)
    )

    old, excursion, reclaim, retest = b[-24:-4], b[-4], b[-3], b[-2]
    level = min(x.low for x in old)
    delayed = (
        level - 0.5 * a <= excursion.c <= level - 0.1 * a
        and reclaim.c > level
        and abs(retest.low - level) <= 0.15 * a
        and retest.c > level
        and retest.low > excursion.low
        and t.c > retest.h
    )
    return tuple(
        bull and value
        for value in (
            micro,
            high2,
            anti,
            nested,
            wedge,
            major,
            final_flag,
            body_gap,
            failure,
            delayed,
        )
    )


@lru_cache(maxsize=512)
def signals(bars: tuple[Candle, ...]) -> tuple[tuple[bool, bool], ...]:
    """Caller supplies completed candles; no intrabar or future observations used.

    A is ATR14 computed before the final 40 bars, frozen outside every pattern.
    Negating OHLC with high/low swapped supplies exactly symmetric short rules.
    """
    neutral = ((False, False),) * len(RECIPES)
    if len(bars) < 200:
        return neutral
    bars = bars[-200:]
    if any(
        not all(isfinite(v) and v > 0 for v in (x.open, x.high, x.low, x.close))
        or x.low > min(x.open, x.close)
        or x.high < max(x.open, x.close)
        for x in bars
    ):
        return neutral
    prior = bars[:-40]
    scale = atr([x.high for x in prior], [x.low for x in prior], [x.close for x in prior])
    if scale <= 0:
        return neutral
    forward = tuple(_Bar(x.open, x.high, x.low, x.close) for x in bars)
    reverse = tuple(_Bar(-x.open, -x.low, -x.high, -x.close) for x in bars)
    return tuple(zip(_longs(forward, scale), _longs(reverse, scale), strict=True))
