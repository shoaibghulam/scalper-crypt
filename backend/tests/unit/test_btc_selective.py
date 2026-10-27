from dataclasses import replace
from decimal import Decimal
from math import sin
from random import Random

import pytest

from scalper.api.research import ResearchRequest, catalog, evaluate_candles
from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.strategies.base import StrategyContext
from scalper.strategies.btc_selective import (
    BtcSelectiveReversed,
    BtcSelectiveReversion,
    range_regime,
)
from scalper.strategies.combinations import Combination


def contexts():
    rng = Random(73)
    one = []
    for i in range(1000):
        close = 100+sin(i/8)*2+rng.uniform(-1, 1)
        opened = one[-1].close if one else 100
        one.append(Candle(i*60_000, opened, max(opened,close)+.1,
                          min(opened,close)-.1, close, 100, (i+1)*60_000-1,0,0))
    five = [Candle(one[i].open_time,one[i].open,max(b.high for b in one[i:i+5]),
                   min(b.low for b in one[i:i+5]),one[i+4].close,500,
                   one[i+4].close_time,0,0) for i in range(0,1000,5)]
    for end in range(500,1001,5):
        yield StrategyContext('BTCUSDT',five[end//5-100:end//5],one[end-200:end],
             Ticker('BTCUSDT',one[end-1].close,0,0),
             BookTop('BTCUSDT',one[end-1].close,one[end-1].close))


def test_control_matches_archived_bollinger_rsi_rules_and_brackets():
    control = BtcSelectiveReversion()
    control.use_regime = False
    original = Combination(7)
    triggered = 0
    for ctx in contexts():
        actual, expected = control.assess(ctx), original.assess(ctx)
        assert (actual.ready,actual.direction) == (expected.ready,expected.direction)
        if actual.ready:
            triggered += 1
            entry = Decimal(str(ctx.price))
            assert control.levels(ctx,entry,actual.direction) == (
                original.levels(ctx,entry,actual.direction))
    assert triggered > 0


def test_future_context_and_wrong_symbol_refused():
    strategy = BtcSelectiveReversion()
    ctx = next(contexts())
    assert not strategy.assess(replace(ctx,symbol='ETHUSDT')).ready
    future = [replace(b,open_time=b.open_time+300_000,close_time=b.close_time+300_000)
              for b in ctx.candles_5m]
    assert 'future' in strategy.assess(replace(ctx,candles_5m=future)).note


def test_strong_trend_excluded():
    trend = tuple(Candle(i*300_000,100+i,102+i,99+i,101+i,100,
                        (i+1)*300_000-1,0,0) for i in range(100))
    assert not range_regime(trend)


def test_backtest_symbol_constraint_and_catalog():
    row = next(r for r in catalog() if r['id']==BtcSelectiveReversion.id)
    assert row['timeframe']=='1m' and row['backtest_only']
    request = ResearchRequest(csv='fixture',strategy=BtcSelectiveReversion.id,
                              dataset_label='ETHUSDT',quantity_step=.001)
    with pytest.raises(ValueError,match='BTCUSDT'):
        evaluate_candles(request,[])


def test_reversed_signals_preserve_waiting_states():
    original, reverse = BtcSelectiveReversion(), BtcSelectiveReversed()
    triggered = 0
    for ctx in contexts():
        a, b = original.assess(ctx), reverse.assess(ctx)
        assert a.ready == b.ready
        if a.ready:
            triggered += 1
            assert b.direction == ('SHORT' if a.direction == 'LONG' else 'LONG')
        else:
            assert a == b
    assert triggered > 0


@pytest.mark.parametrize('direction', ['LONG','SHORT'])
def test_reversed_bracket_swaps_price_levels_and_has_half_r(direction):
    ctx = next(contexts())
    entry = Decimal('100')
    original = BtcSelectiveReversion().levels(ctx,entry,direction)
    reversed_direction = 'SHORT' if direction == 'LONG' else 'LONG'
    reverse = BtcSelectiveReversed().levels(ctx,entry,reversed_direction)
    assert (reverse.stop,reverse.target) == (original.target,original.stop)
    assert abs(reverse.target-entry)*2 == abs(reverse.stop-entry)
    assert (reverse.stop < entry < reverse.target) if reversed_direction == 'LONG' else (
        reverse.target < entry < reverse.stop)


def test_reversed_is_backtest_only_and_rejects_invalid_direction():
    row = next(r for r in catalog() if r['id']==BtcSelectiveReversed.id)
    assert row['backtest_only'] and row['parameters']['reward_risk'] == .5
    with pytest.raises(ValueError,match='direction'):
        BtcSelectiveReversed().levels(next(contexts()),Decimal('100'),'INVALID')
