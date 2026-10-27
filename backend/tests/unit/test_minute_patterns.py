"""Reachability and rejected confirmation for every source-inspired pattern."""

from dataclasses import replace

import pytest

from scalper.exchange.market_data import Candle
from scalper.strategies.minute_patterns import RECIPES, signals

OHLC = tuple[float, float, float, float]


def bar(o: float, c: float, h: float | None = None, low: float | None = None) -> OHLC:
    return o, max(o, c) + 0.2 if h is None else h, min(o, c) - 0.2 if low is None else low, c


def mirror(row: OHLC) -> OHLC:
    o, h, low, c = row
    return 200 - o, 200 - low, 200 - h, 200 - c


UP = [bar(100 + i * 0.5, 100 + (i + 1) * 0.5) for i in range(8)]
DOWN = [mirror(x) for x in UP]

# Ordered event histories, deliberately designed from the written causal rules.
FIXTURES = (
    UP[:6] + [bar(103, 102.6, 103.1, 102), bar(102.8, 103.5)],
    UP
    + [
        bar(103.8, 103.4, 104, 103.2),
        bar(103.4, 103.9, 104.1, 103.2),
        bar(103.8, 103.4, 103.9, 103.1),
        bar(103.5, 104.2),
    ],
    DOWN
    + [
        bar(96, 99),
        bar(99, 101),
        bar(100.5, 100, 100.7, 99.8),
        bar(100, 99.8, 100.2, 99.5),
        bar(100.2, 101),
    ],
    UP
    + [
        bar(104, 104.8),
        bar(104.8, 105.6),
        bar(105.6, 106.4),
        bar(106.1, 106, 106.3, 105.8),
        bar(106, 105.9, 106.2, 105.7),
        bar(105.9, 106, 106.2, 105.7),
        bar(106.1, 106.6),
    ],
    [
        bar(101, 100.5, 101.2, 100),
        bar(100.5, 99.5, 100.6, 99),
        bar(99.6, 100, 100.5, 99.5),
        bar(99.8, 98.3, 100, 98),
        bar(98.5, 99, 99.4, 98.4),
        bar(98.7, 97.8, 98.9, 97.5),
        bar(98.5, 99.7, 99.9, 98.2),
        bar(99, 98.8, 99.1, 98),
        bar(98.9, 99.5),
    ],
    DOWN
    + [
        bar(96, 98.5),
        bar(97.4, 97, 97.6, 96.5),
        bar(97, 96.3, 97.2, 95.9),
        bar(96.3, 96.8, 97, 96.1),
        bar(97, 98),
    ],
    [bar(105 - i * 0.4, 105 - (i + 1) * 0.4) for i in range(15)]
    + [
        bar(99, 99.1, 99.3, 98.8),
        bar(99.1, 99, 99.3, 98.8),
        bar(99, 99.05, 99.3, 98.8),
        bar(99, 98.5),
        bar(98.8, 99.4),
    ],
    [
        bar(100, 102),
        bar(101.8, 101.7),
        bar(101.7, 101.6),
        bar(101.6, 101.5),
        bar(101.5, 101.4),
        bar(101.4, 101.2, 101.5, 100.4),
        bar(101.3, 102),
    ],
    [
        bar(100, 101),
        bar(101, 100.7, 101.2, 100.5),
        bar(100.6, 100.2, 100.8, 100),
        bar(100.5, 101.4),
    ],
    [bar(99.6, 99.2, 99.7, 99), bar(99.2, 99.8), bar(99.8, 99.7, 100, 99.5), bar(99.8, 100.3)],
)


def candles(rows: list[OHLC]) -> tuple[Candle, ...]:
    padded = [(100.0, 100.5, 99.5, 100.0)] * (200 - len(rows)) + rows
    return tuple(
        Candle(i * 60000, o, h, low, c, 100, i * 60000 + 59999, 10000, 50)
        for i, (o, h, low, c) in enumerate(padded)
    )


@pytest.mark.parametrize("index", range(10), ids=[r[0] for r in RECIPES])
@pytest.mark.parametrize("short", [False, True], ids=["long", "short"])
def test_every_pattern_is_reachable(index: int, short: bool) -> None:
    rows = [mirror(x) for x in FIXTURES[index]] if short else FIXTURES[index]
    result = signals(candles(rows))[index]
    assert result == ((False, True) if short else (True, False))


@pytest.mark.parametrize("index", range(10), ids=[r[0] for r in RECIPES])
@pytest.mark.parametrize("short", [False, True], ids=["long", "short"])
def test_close_must_confirm_not_just_wick(index: int, short: bool) -> None:
    rows = list(FIXTURES[index])
    o, h, low, _ = rows[-1]
    # The candle still reaches the trigger intrabar, but its bullish body ends below it.
    rows[-1] = (o, h, low, o + 0.000001)
    if short:
        rows = [mirror(x) for x in rows]
    assert signals(candles(rows))[index] == (False, False)


@pytest.mark.parametrize("index", range(10), ids=[r[0] for r in RECIPES])
@pytest.mark.parametrize("short", [False, True], ids=["long", "short"])
def test_structural_near_miss_is_rejected(index: int, short: bool) -> None:
    rows = list(FIXTURES[index])
    # In order: broken microchannel, missing H1, insufficient reversal shock,
    # deep pause, expanding third push, deep retest, no flag break,
    # lost body gap, missing failure, and a retest revisiting the excursion low.
    mutations = (
        (3, 2, rows[2][2] if index == 0 else 0),
        (9, 1, 104),
        (9, 3, 99.2),
        (-2, 2, 104),
        (5, 2, 96.5),
        (-3, 2, 94),
        (-2, 3, 99),
        (-2, 0, 100),
        (-2, 3, 100.6),
        (-2, 2, 99),
    )
    row, field, value = mutations[index]
    changed = list(rows[row])
    changed[field] = value
    changed[2] = min(changed[2], changed[0], changed[3])
    rows[row] = (changed[0], changed[1], changed[2], changed[3])
    if short:
        rows = [mirror(x) for x in rows]
    # Ensure we test a valid candle with an invalid setup, not malformed data.
    assert all(low <= min(o, c) <= max(o, c) <= h for o, h, low, c in rows)
    assert signals(candles(rows))[index] == (False, False)


def test_flat_short_and_invalid_histories_are_neutral() -> None:
    flat = candles([])
    neutral = ((False, False),) * 10
    assert signals(flat) == neutral
    assert signals(flat[:-1]) == neutral
    zero_range = tuple(replace(x, high=100, low=100) for x in flat)
    assert signals(zero_range) == neutral
    assert signals((*flat[:-1], replace(flat[-1], close=float("nan")))) == neutral
    assert signals((*flat[:-1], replace(flat[-1], high=98))) == neutral


def test_history_older_than_200_candles_cannot_change_signals() -> None:
    bars = candles(FIXTURES[0])
    assert signals((replace(bars[0], open_time=-60000, high=10000), *bars)) == signals(bars)


def test_future_candles_do_not_change_an_existing_prefix() -> None:
    prefix = candles(FIXTURES[0])
    before = signals(prefix)
    extended = (*prefix, replace(prefix[-1], open_time=12000000, close=110, high=111))
    signals(extended)
    assert signals(extended[:-1]) == before
