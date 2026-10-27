"""Research v2 rests on one claim: a cached signal changes speed, not results.

If that claim is ever false the fast path quietly reports different numbers
from the slow one, and a backtest nobody can trust is worse than a slow one.
So the equivalence is asserted on the whole result dict rather than on a
summary figure, and the cache is checked for the properties that make it
reusable across periods and cost settings.
"""

import pytest

from scalper.backtest import aggregate_five, replay, resolve_strategy, signal_series
from scalper.exchange.market_data import Candle
from scalper.strategies import STRATEGIES

MINUTE = 60_000
STRATEGY = "ctk-1"
ECONOMICS = {"initial": 100.0, "quantity_step": 0.001, "strategy_id": STRATEGY}


def candle(index: int, high: float, low: float, close: float, volume: float = 900.0) -> Candle:
    open_time = index * MINUTE
    return Candle(
        open_time,
        (high + low) / 2,
        high,
        low,
        close,
        volume,
        open_time + MINUTE - 1,
        close * volume,
        volume * 0.55,
    )


def series(count: int = 900) -> list[Candle]:
    """Quiet accumulation broken by periodic pushes, so signals can fire."""
    return [
        candle(i, 101.2, 98.6, 100.4, volume=3200.0)
        if i % 150 == 149
        else candle(i, 100.6, 100.4, 100.5)
        for i in range(count)
    ]


class TestCachedAndLiveAgree:
    def test_the_whole_result_matches_not_just_the_total(self):
        bars = series()
        live = replay(bars, start_index=500, **ECONOMICS)
        cached = replay(
            bars,
            start_index=500,
            signals=signal_series(bars, strategy_id=STRATEGY, start_index=500),
            **ECONOMICS,
        )
        assert cached == live

    def test_they_agree_under_stressed_costs(self):
        # The stress pass doubles spread and slippage. Costs move entries, so
        # they move brackets and fills - but not which bars signal. This is the
        # property that lets one cache serve all six passes of a run.
        bars = series()
        costs = {"slippage_bps": 4, "spread_bps": 4}
        signals = signal_series(bars, strategy_id=STRATEGY, start_index=500)
        live = replay(bars, start_index=500, **ECONOMICS, **costs)
        cached = replay(bars, start_index=500, signals=signals, **ECONOMICS, **costs)
        assert cached == live

    def test_one_cache_serves_a_later_period_slice(self):
        # evaluate_candles scores three periods from slices of one series, and
        # each slice re-indexes the bars. That is why the cache is keyed by
        # open time rather than by position.
        bars = series()
        signals = signal_series(bars, strategy_id=STRATEGY, start_index=500)
        window = bars[200:800]
        live = replay(window, start_index=300, **ECONOMICS)
        cached = replay(window, start_index=300, signals=signals, **ECONOMICS)
        assert cached == live


class TestTheCacheIsAReusableSuperset:
    def test_it_answers_for_bars_a_replay_would_skip(self):
        # A replay stops assessing while a position is open, so the bars it
        # asks about depend on capital and costs. Caching only those would
        # under-report trades on the next run with different economics.
        bars = series()
        signals = signal_series(bars, strategy_id=STRATEGY, start_index=500)
        traded = {t["opened_at"] for t in replay(bars, start_index=500, **ECONOMICS)["trades"]}
        assert traded <= set(signals)

    def test_every_signal_lands_on_the_strategy_interval(self):
        signals = signal_series(series(), strategy_id=STRATEGY, start_index=500)
        interval = resolve_strategy(STRATEGY).signal_interval_s * 1000
        assert signals and all(at % interval == 0 for at in signals)

    def test_directions_are_only_long_or_short(self):
        assert set(signal_series(series(), strategy_id=STRATEGY, start_index=500).values()) <= {
            "LONG",
            "SHORT",
        }

    def test_it_is_deterministic(self):
        bars = series()
        first = signal_series(bars, strategy_id=STRATEGY, start_index=500)
        assert first == signal_series(bars, strategy_id=STRATEGY, start_index=500)

    def test_an_empty_cache_means_no_trades_rather_than_reassessing(self):
        # A miss must never fall back to the slow path: that would hide a stale
        # or miskeyed cache behind a correct-looking result.
        assert replay(series(), start_index=500, signals={}, **ECONOMICS)["trade_count"] == 0

    def test_the_cache_is_what_drives_entries(self):
        # Guards the equivalence tests above against passing for the wrong
        # reason - that signals are accepted but ignored.
        bars = series()
        live = replay(bars, start_index=500, **ECONOMICS)
        assert live["trade_count"] > 0
        assert replay(bars, start_index=500, signals={}, **ECONOMICS)["trade_count"] == 0


class TestSharedHelpers:
    def test_the_five_minute_series_drops_partial_groups(self):
        # Contexts slice by close time; a half-formed 5m bar would let a
        # strategy see inside the group it is still forming.
        five, closes = aggregate_five(series(12))
        assert len(five) == 2
        assert closes == [c.close_time for c in five]

    def test_resolution_refuses_an_order_flow_strategy(self):
        # signal_series and replay must agree on what is replayable at all,
        # which is why they share one resolver.
        order_flow = next(
            (i for i in STRATEGIES.ids() if STRATEGIES.get(i).requires_order_flow), None
        )
        if order_flow is None:
            pytest.skip("no order-flow strategy is registered")
        with pytest.raises(ValueError, match="order-flow"):
            signal_series(series(), strategy_id=order_flow, start_index=500)
