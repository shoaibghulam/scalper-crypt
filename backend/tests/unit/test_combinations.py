from dataclasses import replace
from decimal import Decimal

import pytest

from scalper.api.research import ResearchRequest, evaluate
from scalper.strategies import STRATEGIES
from scalper.strategies.combinations import COMBINATIONS, Combination, divergence, fifteen
from tests.unit.test_laboratory import bars, context


@pytest.mark.parametrize("strategy", COMBINATIONS.values(), ids=COMBINATIONS.keys())
def test_isolated_combinations_validate_and_size(strategy):
    ctx = context()
    assert strategy.assess(ctx).direction in {None, "LONG", "SHORT"}
    assert not strategy.assess(replace(ctx, candles_1m=[])).ready
    assert STRATEGIES.get(strategy.id).testnet_only
    assert not STRATEGIES.get(strategy.id).manual_only
    for side in ("LONG", "SHORT"):
        price = Decimal(str(ctx.price))
        levels = strategy.levels(ctx, price, side)
        assert abs(levels.target - price) == 2 * abs(price - levels.stop)
    assert strategy.describe()["research_only"]


def test_partial_fifteen_minute_bar_is_never_visible():
    five = context().candles_5m
    closed = fifteen(five)
    assert closed
    assert all(c.close_time % 900000 == 899999 for c in closed)
    # Removing the final5m candle removes its entire15m group.
    shortened = fifteen(five[:-1])
    assert len(shortened) == len(closed) - 1
    assert shortened == closed[:-1]


def test_divergence_cannot_use_unconfirmed_right_edge():
    data = bars(200)
    data[-2] = replace(data[-2], low=1)
    # Extreme at the right edge cannot become a pivot until two later closes.
    from scalper.strategies.research import pivots

    assert len(data) - 2 not in pivots(data[:-1], low=True)
    assert isinstance(divergence(data, 1), bool)


def test_vwap_rsi_requires_both_filters(monkeypatch):
    ctx = context()
    p, c = ctx.candles_1m[-2:]
    f = {"e9": 100, "e21": 99, "vwap": c.close - 1, "rsi": 60, "hist": 1, "bbdn": 90, "bbup": 110}
    g = {**f, "vwap": p.close + 1}
    monkeypatch.setattr(
        "scalper.strategies.combinations.features",
        lambda series: f if len(series) == len(ctx.candles_1m) else g,
    )
    assert Combination(6).triggers(ctx) == (True, False)
    f["rsi"] = 40
    assert Combination(6).triggers(ctx) == (False, False)


def test_longer_warmup_is_shared_when_requested():
    data = "open_time,open,high,low,close,volume\n" + "\n".join(
        f"{i * 60000},100,101,99,100,100" for i in range(1480)
    )
    options = dict(csv=data, quantity_step=0.001, warmup=780)
    a = evaluate(ResearchRequest(strategy="user-20", **options))
    b = evaluate(ResearchRequest(strategy="lab-01", **options))
    assert a["warmup_candles"] == b["warmup_candles"] == 780
    assert [(r["start"], r["end"]) for r in a["results"]] == [
        (r["start"], r["end"]) for r in b["results"]
    ]
    assert all(r["trade_count"] == 0 for r in a["results"])


def test_event_strategy_is_not_disguised_as_candle_strategy():
    assert len(COMBINATIONS) == 19
    assert "user-18" not in COMBINATIONS
    with pytest.raises(ValueError):
        ResearchRequest(csv="x", quantity_step=0.001, strategy="user-18")
