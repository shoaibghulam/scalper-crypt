"""Hand-calculated execution checks, independent of the replay implementation."""

import numpy as np
import pytest

from scalper.research.backtest import Backtester
from scalper.research.features import Features
from scalper.research.schema import Costs, Risk

from .test_discovery_engine import config, dataset, strategy


@pytest.mark.parametrize("direction,sign", [("long", 1), ("short", -1)])
def test_flat_market_round_trip_matches_hand_calculation(direction, sign):
    fee, friction = 0.0005, 0.0003
    cfg = config().model_copy(update={"costs": Costs(fee=fee, slippage=0.0002, spread=0.0002)})
    result = Backtester(Features(dataset(10, prices=np.full(10, 100.0)))).run(
        strategy(direction=direction, risk=Risk(max_hold=100)), cfg
    )
    entry, exit_price = 100 * (1 + sign * friction), 100 * (1 - sign * friction)
    quantity = cfg.capital * 0.2 / entry
    fees = quantity * (entry + exit_price) * fee
    expected = sign * quantity * (exit_price - entry) - fees
    assert len(result["trades"]) == 1
    assert result["trades"][0, 2] == pytest.approx(entry)
    assert result["trades"][0, 3] == pytest.approx(exit_price)
    assert result["metrics"]["fees_paid"] == pytest.approx(fees)
    assert result["metrics"]["net_profit"] == pytest.approx(expected)


@pytest.mark.parametrize("direction,gap", [("long", 95.0), ("short", 105.0)])
def test_stop_gap_fills_at_adverse_open_not_stop_level(direction, gap):
    prices = np.array([100.0, 100.0, 100.0, gap, gap])
    result = Backtester(Features(dataset(5, prices=prices))).run(
        strategy(direction=direction, risk=Risk(stop_loss=0.01, take_profit=0.1)), config()
    )
    assert result["trades"][0, 3] == gap
    assert result["trades"][0, 8] == 1


def test_timeframe_mismatch_is_rejected_instead_of_mislabelled():
    with pytest.raises(ValueError, match="timeframe"):
        Backtester(Features(dataset())).run(strategy(timeframe="1m"), config())


@pytest.mark.parametrize("direction,target", [("long", 101.0), ("short", 99.0)])
def test_target_price_and_profit_are_exact_without_costs(direction, target):
    result = Backtester(
        Features(dataset(5, prices=np.array([100.0, 100.0, 100.0, target, target])))
    ).run(strategy(direction=direction, risk=Risk(stop_loss=0.05, take_profit=0.01)), config())
    assert result["trades"][0, 3] == target
    assert result["trades"][0, 5] == pytest.approx(2.0)
    assert result["trades"][0, 8] == 2


@pytest.mark.parametrize("direction", ["long", "short"])
def test_limit_touch_without_trade_through_does_not_fill(direction):
    # Fixture high/low are exactly +/-0.2%: touching is insufficient.
    result = Backtester(Features(dataset(10, prices=np.full(10, 100.0)))).run(
        strategy(direction=direction, order_type="limit", limit_offset=0.002), config()
    )
    assert result["metrics"]["trades"] == 0
    assert result["metrics"]["net_profit"] == 0


def test_finalist_slots_use_distinct_validation_paths_only():
    from scalper.research.validation import distinct_finalists

    def candidate(rule, score, net):
        return {
            "strategy": rule,
            "score": score,
            "validation": {
                "equity": np.array([1000.0, 1000.0 + net]),
                "trades": np.array([[1.0, 2.0, 100.0, 101.0, 1.0, net, 0.0, 0.0, 2.0]]),
            },
        }

    first = candidate(strategy(), 3, 1)
    duplicate = candidate(strategy(risk=Risk(stop_loss=0.009)), 2, 1)
    other = candidate(strategy(direction="short"), 1, -1)
    selected, duplicates = distinct_finalists([other, duplicate, first], 2)
    assert selected == [first, other]
    assert duplicates == [
        {
            "strategy_id": duplicate["strategy"].strategy_id,
            "same_validation_as": first["strategy"].strategy_id,
        }
    ]
