from dataclasses import replace

import pytest

from scalper.backtest import replay
from tests.unit.test_strategy_black import candle, long_setup


def aligned(bars):
    return [
        replace(c, open_time=i * 60000, close_time=i * 60000 + 59999) for i, c in enumerate(bars)
    ]


def test_no_entry_before_the_signal_has_closed():
    assert replay(aligned(long_setup()))["trade_count"] == 0
    result = replay(aligned(long_setup() + [candle(110.30, 115, 105, 110.30)]))
    assert result["trade_count"] == 1
    trade = result["trades"][0]
    assert trade["opened_at"] > aligned(long_setup())[-1].close_time
    assert trade["reason"] == "stop"  # Both levels touched; no optimistic ordering.
    assert trade["fees"] > 0 and trade["net"] < 0


def test_future_bars_do_not_change_a_completed_trade():
    bars = long_setup() + [candle(110.30, 115, 105, 110.30)]
    short = replay(aligned(bars))
    long = replay(aligned(bars + [candle(110, 111, 109, 110)] * 8))
    assert short["trades"][0] == long["trades"][0]


def test_gapped_history_is_rejected():
    bars = aligned(long_setup())
    del bars[20]
    with pytest.raises(ValueError, match="consecutive"):
        replay(bars)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_economics_are_rejected(value):
    with pytest.raises(ValueError):
        replay([], initial=value)
