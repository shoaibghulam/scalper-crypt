import numpy as np
import pytest
from pydantic import ValidationError

from scalper.research.backtest import Backtester, replay
from scalper.research.data.models import Bar, DataRequest, Dataset, FundingEvent
from scalper.research.features import Features
from scalper.research.schema import (
    Condition,
    Costs,
    Operand,
    Risk,
    SearchConfig,
    StrategyDefinition,
)
from scalper.research.search import compare


def dataset(n=600, funding=(), prices=None):
    prices = prices if prices is not None else 100 + np.arange(n) * 0.01 + np.sin(np.arange(n) / 9)
    bars = tuple(
        Bar(
            timestamp=i * 300000,
            available_at=(i + 1) * 300000,
            open=float(c),
            high=float(c * 1.002),
            low=float(c * 0.998),
            close=float(c),
            volume=10.0,
        )
        for i, c in enumerate(prices)
    )
    return Dataset(
        request=DataRequest(symbol="BTCUSDT", start=0, end=n * 300000, funding=True),
        source="test",
        source_sha256="0" * 64,
        bars=bars,
        funding_events=funding,
        funding_status="queried",
    )


def strategy(**kwargs):
    return StrategyDefinition(entry=compare("close", 14, "gt", value=0), **kwargs)


def config(**kwargs):
    return SearchConfig(
        population=8,
        generations=1,
        optimization_trials=0,
        costs=Costs(fee=0, slippage=0, spread=0),
        **kwargs,
    )


@pytest.mark.parametrize(
    "name",
    [
        "ema",
        "sma",
        "wma",
        "rsi",
        "macd",
        "bb_upper",
        "bb_lower",
        "atr",
        "adx",
        "stochastic",
        "vwap",
        "roc",
        "momentum",
        "volume_sma",
        "volatility",
        "returns",
    ],
)
def test_feature_prefix_is_causal(name):
    original = dataset()
    changed = dataset(
        prices=np.r_[100 + np.arange(300) * 0.01 + np.sin(np.arange(300) / 9), np.full(300, 9000.0)]
    )
    np.testing.assert_allclose(
        Features(original).indicator(name)[:300],
        Features(changed).indicator(name)[:300],
        equal_nan=True,
    )


def test_not_does_not_trade_during_warmup():
    rule = Condition(op="not", children=(compare("ema", 50, "gt", value=200),))
    truth, valid = Features(dataset()).expression(rule)
    assert not truth[:49].any() and not valid[:49].any()
    assert truth[50]


def test_schema_rejects_unbounded_and_invalid_operands():
    with pytest.raises(ValidationError):
        Operand(indicator="ema", value=1)
    with pytest.raises(ValidationError):
        Risk(leverage=100)
    node = compare("close", 14, "gt", value=1)
    with pytest.raises(ValidationError):
        for _ in range(5):
            node = Condition(op="not", children=(node,))


def test_next_open_and_accounting():
    data = dataset(10, prices=np.arange(100.0, 110.0))
    result = Backtester(Features(data)).run(
        strategy(risk=Risk(stop_loss=0.1, take_profit=0.3, max_hold=100)), config()
    )
    trade = result["trades"][0]
    assert trade[0] == 2  # no signal from before the period
    assert trade[2] == 102
    assert trade[3] == 109
    assert result["metrics"]["net_profit"] == pytest.approx(200 / 102 * 7)
    assert sum(result["trades"][:, 5]) == pytest.approx(result["metrics"]["net_profit"])


def test_funding_signed_and_not_paid_by_new_entry():
    events = (
        FundingEvent(timestamp=2 * 300000, rate=0.01, mark_price=100),
        FundingEvent(timestamp=4 * 300000, rate=0.01, mark_price=100),
    )
    data = dataset(10, funding=events, prices=np.full(10, 100.0))
    long = Backtester(Features(data)).run(strategy(), config())
    short = Backtester(Features(data)).run(strategy(direction="short"), config())
    assert long["metrics"]["funding_paid"] == 2
    assert short["metrics"]["funding_paid"] == -2
    assert long["metrics"]["net_profit"] == -2


def test_exchange_funding_millisecond_jitter_is_explicitly_supported():
    data = dataset(
        10,
        funding=(FundingEvent(timestamp=4 * 300000 + 5, rate=0.01, mark_price=100),),
        prices=np.full(10, 100.0),
    )
    result = Backtester(Features(data)).run(strategy(), config())
    assert result["metrics"]["funding_paid"] == 2
    bad = dataset(
        10, funding=(FundingEvent(timestamp=4 * 300000 + 2000, rate=0.01, mark_price=100),)
    )
    with pytest.raises(ValueError, match="over 1s"):
        Backtester(Features(bad))


def test_stop_wins_ambiguous_bar_and_fees_reconcile():
    data = dataset(10, prices=np.full(10, 100.0))
    cfg = config().model_copy(update={"costs": Costs(fee=0.001, slippage=0.001, spread=0.001)})
    s = strategy(risk=Risk(stop_loss=0.001, take_profit=0.001))
    result = Backtester(Features(data)).run(s, cfg)
    assert result["trades"][0, 8] == 1
    assert result["metrics"]["fees_paid"] > 0
    assert result["metrics"]["net_profit"] == pytest.approx(sum(result["trades"][:, 5]))


def test_kill_switch_and_risk_ceiling():
    cfg = config()
    killed = cfg.model_copy(update={"safety": cfg.safety.model_copy(update={"kill_switch": True})})
    result = Backtester(Features(dataset())).run(strategy(), killed)
    assert result["metrics"]["trades"] == 0
    assert result["metrics"]["halted"]
    with pytest.raises(ValueError, match="ceiling"):
        Backtester(Features(dataset())).run(strategy(risk=Risk(position_size=1)), cfg)


def test_compiled_matches_python_reference():
    f = Features(dataset())
    args = (
        f.ohlcv,
        f.timestamps,
        np.ones(600, dtype=bool),
        np.zeros(600, dtype=bool),
        np.zeros(600),
        1,
        600,
        1000.0,
        -1,
        0.008,
        0.016,
        0.006,
        0.2,
        2.0,
        3,
        40,
        False,
        0.001,
        0.0005,
        0.0003,
        0.05,
        0.2,
        8,
        False,
    )
    actual, expected = replay(*args), replay.py_func(*args)
    np.testing.assert_allclose(actual[0], expected[0])
    np.testing.assert_allclose(actual[1], expected[1])
    assert actual[2:] == expected[2:]
