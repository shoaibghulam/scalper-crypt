import json
import random
import threading
from dataclasses import replace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from scalper import alpha_lab as lab
from scalper.api import alpha_lab as jobs
from scalper.backtest import replay
from scalper.exchange.market_data import Candle


def candles(n=5000):
    rng = random.Random(3)
    bars = []
    price = 100.0
    for i in range(n):
        close = price + rng.uniform(-0.2, 0.2)
        bars.append(
            Candle(
                i * 60000,
                price,
                max(price, close) + 0.1,
                min(price, close) - 0.1,
                close,
                10 + i % 17,
                i * 60000 + 59999,
                close * 10,
                5,
            )
        )
        price = close
    return bars


def test_future_changes_cannot_change_past_features():
    bars = candles(400)
    prefix, atr = lab.feature_rows(bars[:300], threading.Event())
    altered = bars[:300] + [replace(b, close=b.close + 10, high=b.high + 20) for b in bars[300:]]
    full, full_atr = lab.feature_rows(altered, threading.Event())
    assert full[:300] == prefix
    assert full_atr[:300] == atr


def test_higher_timeframe_waits_for_closed_hour():
    bars = candles(120)
    original, _ = lab.feature_rows(bars, threading.Event())
    changed = bars.copy()
    changed[118] = replace(changed[118], close=200, high=201)
    new, _ = lab.feature_rows(changed, threading.Event())
    # ATR normalizes the trend feature, so compare the raw numerator.
    _, a = lab.feature_rows(bars, threading.Event())
    _, b = lab.feature_rows(changed, threading.Event())
    assert original[118][14] * a[118] == pytest.approx(new[118][14] * b[118])
    assert original[118][14] == 0  # only one complete hourly close initializes EMA
    assert original[119][14] != 0


def test_partitions_are_chronological_with_embargo():
    windows = lab.partitions(13680, 3600)
    assert windows["train"][0] == 3600
    assert windows["validation"][0] - windows["train"][1] == 60
    assert windows["holdout"][0] - windows["validation"][1] == 60
    assert windows["holdout"][1] == 13680


@pytest.mark.parametrize("tokens", [(1,), (0, 0, 999, 0, 0, 0, 0, 0), (0, 0, 0, 0, 0, 0, 0, True)])
def test_formula_is_a_bounded_grammar_not_code(tokens):
    with pytest.raises(ValueError):
        lab.formula(tokens)


def test_formula_replay_uses_next_bar_and_costs():
    bars = [
        Candle(i * 60000, 100, 100.1, 99.9, 100, 10, i * 60000 + 59999, 1000, 5) for i in range(90)
    ]
    values = [0.0] * len(bars)
    values[10] = 2
    strategy = lab.FormulaStrategy(bars, values, [0.1] * len(bars), 0.5)
    result = replay(bars, strategy_id=strategy.id, research_strategy=strategy)
    trade = result["trades"][0]
    assert trade["opened_at"] == bars[11].open_time
    assert trade["closed_at"] == bars[71].open_time
    assert trade["fees"] > 0
    assert trade["net"] < 0
    assert result["trade_count"] == 1
    strategy.backtest_only = False
    with pytest.raises(ValueError, match="backtest-only"):
        replay(bars, strategy_id=strategy.id, research_strategy=strategy)


def test_cancel_before_feature_work():
    event = threading.Event()
    event.set()
    with pytest.raises(InterruptedError):
        lab.feature_rows(candles(10), event)


def test_reproducible_gp_and_holdout_never_feeds_selection(monkeypatch):
    bars = candles()
    calls = []
    windows = lab.partitions(len(bars), lab.WARMUP)

    def fake_replay(segment, **kw):
        start = segment[kw["start_index"]].open_time // 60000
        calls.append(start)
        strategy = kw["research_strategy"]
        trades = sum(direction is not None for direction, _ in strategy.signals.values())
        return {
            "net": trades / 100,
            "return_pct": trades / 100,
            "trade_count": trades,
            "closed_equity_drawdown_pct": 0,
            "trades": [],
            "equity_curve": [],
        }

    monkeypatch.setattr(lab, "replay", fake_replay)
    req = lab.DiscoveryRequest(method="gp", population=8, generations=2, minimum_trades=10)
    metadata = {"warmup": 3600, "minimum_notional": 0, "minimum_quantity": 0, "quantity_step": 0}
    report = lab.discover(req, bars, metadata, threading.Event(), lambda _: None)
    first_holdout = calls.index(windows["holdout"][0])
    assert all(start == windows["holdout"][0] for start in calls[first_holdout:])
    repeat = lab.discover(req, bars, metadata, threading.Event(), lambda _: None)
    assert report == repeat
    assert len(report["finalists"]) == 1
    json.dumps(report, allow_nan=False)


def test_zero_trade_candidates_rejected():
    assert lab.reward({"trade_count": 0}, 30) < -100


@pytest.mark.parametrize(
    "kwargs",
    [{"days": 366}, {"population": 1000}, {"symbol": "BTC/USDT"}, {"capital": float("nan")}],
)
def test_bounded_request(kwargs):
    with pytest.raises(ValidationError):
        lab.DiscoveryRequest(**kwargs)


def test_saved_job_restart_and_invalid_path(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "STORE", tmp_path)
    key = "a" * 32
    jobs.persist({"id": key, "status": "running", "phase": "training"})
    assert jobs.get(key)["status"] == "interrupted"
    with pytest.raises(HTTPException) as error:
        jobs.get("../secrets")
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_gpu_failure_is_reported_before_downloading(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "STORE", tmp_path)

    async def no_gpu():
        return {"torch": "test", "cuda": False}

    monkeypatch.setattr(jobs, "capabilities", no_gpu)
    job = await jobs.start(lab.DiscoveryRequest())
    await next(iter(jobs._tasks))
    result = jobs.get(job["id"])
    assert result["status"] == "failed"
    assert "CUDA" in result["phase"]
    assert job["id"] not in jobs._events


def test_neural_policy_learns_on_available_device():
    torch = pytest.importorskip("torch")
    sampler = lab.NeuralSampler("cuda" if torch.cuda.is_available() else "cpu", 42)
    before = [p.detach().clone() for p in sampler.parameters]
    tokens = sampler.sample(8)
    for row in tokens:
        lab.validate_genome(row)
    loss = sampler.learn([float(i) for i in range(8)])
    assert math_is_finite(loss)
    assert any(not torch.equal(a, b) for a, b in zip(before, sampler.parameters, strict=True))


def math_is_finite(value):
    import math

    return math.isfinite(value)


def test_multiple_coin_validation():
    assert len(lab.DiscoveryRequest(symbols=[f"C{i}USDT" for i in range(528)]).symbols) == 528
    assert lab.DiscoveryRequest(symbols=["ETHUSDT", "ETHUSDT"]).symbols == ["ETHUSDT"]
    for symbols in [["../bad"], [f"C{i}USDT" for i in range(1001)]]:
        with pytest.raises(ValidationError):
            lab.DiscoveryRequest(symbols=symbols)


@pytest.mark.asyncio
async def test_batch_dates_errors_and_persistence(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "STORE", tmp_path)
    requests = []

    async def download(request, state, event, **kwargs):
        requests.append((request.symbol, request.end_time))
        if request.symbol == "BADUSDT":
            raise ValueError("Not enough history")
        return [], {"end": 600000}

    def discover(request, bars, metadata, event, update):
        update({"phase": "Testing", "progress": 50})
        return {"finalists": [{"passed": request.symbol == "ETHUSDT"}]}

    monkeypatch.setattr(jobs, "download", download)
    monkeypatch.setattr(jobs, "discover", discover)
    job = await jobs.start(
        lab.DiscoveryRequest(method="gp", symbols=["BTCUSDT", "BADUSDT", "ETHUSDT"])
    )
    await next(iter(jobs._tasks))
    result = jobs.get(job["id"])
    assert requests == [("BTCUSDT", None), ("BADUSDT", 600000), ("ETHUSDT", 600000)]
    assert result["status"] == "completed_with_errors"
    assert result["passed_count"] == 1
    assert result["completed_coins"] == 3
    saved = json.loads((tmp_path / f"{job['id']}.json").read_text())
    assert [row["symbol"] for row in saved["coin_results"]] == ["BTCUSDT", "ETHUSDT"]
    assert saved["errors"][0]["symbol"] == "BADUSDT"


@pytest.mark.asyncio
async def test_cancel_keeps_finished_coins(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "STORE", tmp_path)
    calls = []

    async def download(request, state, event, **kwargs):
        calls.append(request.symbol)
        if len(calls) == 2:
            event.set()
            lab.check(event)
        return [], {"end": 600000}

    monkeypatch.setattr(jobs, "download", download)
    monkeypatch.setattr(jobs, "discover", lambda *args: {"finalists": [{"passed": False}]})
    job = await jobs.start(
        lab.DiscoveryRequest(method="gp", symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    )
    await next(iter(jobs._tasks))
    result = jobs.get(job["id"])
    assert result["status"] == "cancelled"
    assert len(result["coin_results"]) == 1
    assert calls == ["BTCUSDT", "ETHUSDT"]
