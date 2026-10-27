"""Deterministic causal indicator checks, including long/short reachability."""

import random

import pytest

from scalper.exchange.market_data import Candle
from scalper.strategies.minute_indicators import RECIPES, _divergence, _ema, _wma, signals


def candles(seed: int, reverse: bool = False) -> tuple[Candle, ...]:
    rng = random.Random(seed)
    price = 1000.0
    result = []
    for i in range(200):
        old = price
        price += rng.gauss(0, 1) * (1 if i < 160 else 4)
        width = rng.uniform(0.05, 2) * (1 if i < 160 else 3)
        high, low = max(old, price) + width, min(old, price) - width
        volume = rng.uniform(10, 100)
        op, close = old, price
        if reverse:
            op, close, high, low = 2000 - old, 2000 - price, 2000 - low, 2000 - high
        result.append(
            Candle(
                i * 60000,
                op,
                high,
                low,
                close,
                volume,
                i * 60000 + 59999,
                volume * close,
                volume / 2,
            )
        )
    return tuple(result)


def test_reference_smoothing() -> None:
    assert _ema([2, 4, 8], 3) == [2, 3, 5.5]
    assert _wma([1, 2, 3, 4], 3)[-2:] == pytest.approx([14 / 6, 20 / 6])


def test_flat_and_short_history_are_neutral() -> None:
    flat = tuple(
        Candle(i * 60000, 100, 100, 100, 100, 0, i * 60000 + 59999, 0, 0) for i in range(200)
    )
    assert signals(flat) == ((False, False),) * 10
    assert signals(flat[:199]) == ((False, False),) * 10
    assert len(RECIPES) == 10


def test_known_divergence_needs_confirmation_and_is_not_repeated() -> None:
    price = [100.0] * 30
    price[5], price[15] = 90, 89
    oscillator = [40.0] * 30
    oscillator[5], oscillator[15] = 20, 25
    oscillator[18] = 45
    highs = [x + 1 for x in price]
    assert _divergence(price[:17], highs[:17], price[:17], oscillator[:17]) == (False, False)
    assert _divergence(price[:19], highs[:19], price[:19], oscillator[:19]) == (True, False)
    oscillator[19], oscillator[20] = 39, 46
    assert _divergence(price[:21], highs[:21], price[:21], oscillator[:21]) == (False, False)
    reflected = [200 - x for x in price]
    assert _divergence(
        reflected[:19],
        reflected[:19],
        [x - 1 for x in reflected[:19]],
        [100 - x for x in oscillator[:19]],
    ) == (False, True)


def test_inputs_are_immutable_and_cache_bounded() -> None:
    bars = candles(1)
    original = tuple(bars)
    assert signals(bars) == signals(bars)
    assert bars == original
    assert signals.cache_info().maxsize == 512


@pytest.mark.parametrize(
    "index,seed", [(0, 177), (1, 17), (2, 57), (3, 9), (5, 90), (6, 14), (7, 19), (8, 3), (9, 1)]
)
def test_real_indicator_triggers_reachable_in_both_directions(index: int, seed: int) -> None:
    direct = signals(candles(seed))[index]
    inverse = signals(candles(seed, reverse=True))[index]
    assert sum(direct) == 1
    assert inverse == direct[::-1]


@pytest.mark.parametrize("reverse", [False, True])
def test_ultimate_full_formula_divergence_entry(reverse: bool) -> None:
    # Long sustained selloff, weak bounce, sharp lower low, then confirmed recovery.
    closes = (
        [1000.0] * 180
        + [1000 - 2 * (i + 1) for i in range(30)]
        + [940 + 2 * (i + 1) for i in range(6)]
        + [945.5, 939.0]
        + [939 + 3 * (i + 1) for i in range(20)]
    )
    if reverse:
        closes = [2000 - c for c in closes]
    bars = tuple(
        Candle(i * 60000, c, c + 0.5, c - 0.5, c, 100, i * 60000 + 59999, c * 100, 50)
        for i, c in enumerate(closes)
    )
    assert signals(bars[23:223])[4] == (False, False)
    assert signals(bars[24:224])[4] == (not reverse, reverse)
    assert signals(bars[25:225])[4] == (False, False)
