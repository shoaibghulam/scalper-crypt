import math
import random
from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from scalper.api.app import create_app
from scalper.api.research import ResearchRequest, catalog
from scalper.backtest import replay
from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.strategies import STRATEGIES
from scalper.strategies.base import StrategyContext
from scalper.strategies.laboratory import LAB_STRATEGIES, RECIPES, decisions, features


def bars(count=600):
    rng = random.Random(932)
    price = 100.0
    result = []
    for i in range(count):
        close = price + rng.uniform(-1, 1)
        result.append(
            Candle(
                i * 60000,
                price,
                max(price, close) + 0.1,
                min(price, close) - 0.1,
                close,
                rng.uniform(100, 500),
                i * 60000 + 59999,
                0,
                0,
            )
        )
        price = close
    return result


def context():
    one = bars()
    five = [
        Candle(
            i * 60000,
            one[i].open,
            max(b.high for b in one[i : i + 5]),
            min(b.low for b in one[i : i + 5]),
            one[i + 4].close,
            sum(b.volume for b in one[i : i + 5]),
            (i + 5) * 60000 - 1,
            0,
            0,
        )
        for i in range(0, len(one), 5)
    ]
    return StrategyContext(
        "LABUSDT",
        five[-100:],
        one[-200:],
        Ticker("LABUSDT", one[-1].close, 0, 0),
        BookTop("LABUSDT", one[-1].close, one[-1].close),
    )


def test_fifty_unique_rules_are_research_only():
    assert len(RECIPES) == len(LAB_STRATEGIES) == 50
    assert len({s.name for s in LAB_STRATEGIES.values()}) == 50
    assert set(LAB_STRATEGIES).issubset(STRATEGIES.ids())
    for strategy in LAB_STRATEGIES.values():
        assert strategy.describe()["research_only"] is True
        assert str(strategy.describe()["source"]).startswith("https://")
        assert STRATEGIES.get(strategy.id).testnet_only
        assert not STRATEGIES.get(strategy.id).manual_only


@pytest.mark.parametrize("strategy", LAB_STRATEGIES.values(), ids=LAB_STRATEGIES.keys())
def test_all_candidates_validate_history_and_brackets(strategy):
    ctx = context()
    result = strategy.assess(ctx)
    assert result.direction in {None, "LONG", "SHORT"}
    empty = replace(ctx, candles_1m=[])
    assert not strategy.assess(empty).ready
    invalid = replace(ctx, book=BookTop("LABUSDT", float("nan"), 100))
    assert not strategy.assess(invalid).ready
    for side in ("LONG", "SHORT"):
        entry = Decimal(str(ctx.price))
        levels = strategy.levels(ctx, entry, side)
        if side == "LONG":
            assert levels.stop < entry < levels.target
        else:
            assert levels.target < entry < levels.stop
        assert abs(levels.target - entry) == 2 * abs(levels.stop - entry)


def test_features_are_neutral_on_flat_prices():
    flat = [Candle(i * 60000, 100, 100, 100, 100, 100, i * 60000 + 59999, 0, 0) for i in range(100)]
    values = features(flat)
    assert values["rsi"] == values["k"] == values["mfi"] == 50
    assert all(math.isfinite(value) for value in values.values())
    assert all(pair == (False, False) for pair in decisions(tuple(flat)))


def test_channels_exclude_trigger_and_inside_bar_uses_mother_range():
    data = [Candle(i * 60000, 100, 101, 99, 100, 100, i * 60000 + 59999, 0, 0) for i in range(100)]
    data[-2] = replace(data[-2], high=100.5, low=99.5)
    data[-1] = replace(data[-1], close=102, high=102.1)
    signal = decisions(tuple(data))
    assert signal[22] == (True, False)  # Donchian20
    assert signal[36] == (True, False)  # Inside bar
    # A future shock cannot mutate the cached earlier prefix.
    frozen = signal
    data.append(replace(data[-1], open_time=6000000, close_time=6059999, high=200, close=200))
    decisions(tuple(data))
    assert decisions(tuple(data[:-1])) == frozen


def test_replay_never_enters_before_closed_trigger_and_is_causal():
    data = bars(800)
    first = replay(
        data[:700], strategy_id="lab-23", minimum_notional=5, quantity_step=0.001, initial=1000
    )
    second = replay(
        data, strategy_id="lab-23", minimum_notional=5, quantity_step=0.001, initial=1000
    )
    closed = [t for t in first["trades"] if t["reason"] != "end_of_data"]
    assert closed
    assert closed == second["trades"][: len(closed)]
    assert all(t["opened_at"] % 300000 == 0 for t in second["trades"])


def test_catalog_http_and_unrecognized_research_ids():
    client = TestClient(create_app())
    response = client.get("/api/research/catalog")
    assert response.status_code == 200
    assert len([s for s in response.json() if s["id"].startswith("lab-")]) == 50
    # 148 registered, minus order-flow-pullback (refused below), plus the
    # seven research-only entries that never trade live.
    assert len(catalog()) == 155
    with pytest.raises(ValueError, match="Unsupported"):
        ResearchRequest(csv="x", strategy="lab-51", quantity_step=0.001)
    with pytest.raises(ValueError, match="Unsupported"):
        ResearchRequest(csv="x", strategy="order-flow-pullback", quantity_step=0.001)


@pytest.mark.parametrize("number", [26, 30, 38, 39])
def test_rare_price_structures_have_reachable_triggers(number):
    data = [Candle(i * 60000, 100, 101, 99, 100, 100, i * 60000 + 59999, 0, 0) for i in range(100)]
    if number == 26:
        data[-1] = replace(data[-1], low=98, close=100.5)
    elif number == 30:
        data[-1] = replace(data[-1], high=103, close=102)
    elif number == 38:
        data[-3] = replace(data[-3], high=100.8, low=99.2)
        data[-2] = replace(data[-2], high=100.5, low=99.5)
        data[-1] = replace(data[-1], high=103, close=102)
    else:
        data[-2] = replace(data[-2], high=102, low=98, close=101)
        data[-1] = replace(data[-1], high=103, close=102.5)
    assert decisions(tuple(data))[number - 1] == (True, False)
    mirrored = [
        replace(b, open=200 - b.open, high=200 - b.low, low=200 - b.high, close=200 - b.close)
        for b in data
    ]
    assert decisions(tuple(mirrored))[number - 1] == (False, True)
