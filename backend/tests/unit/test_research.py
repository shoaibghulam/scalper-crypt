from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from scalper.api.app import create_app
from scalper.api.research import ResearchRequest, evaluate
from scalper.backtest import replay
from tests.unit.test_black_replay import aligned
from tests.unit.test_strategy_black import candle, long_setup


def request(**kwargs):
    data = "open_time,open,high,low,close,volume\n" + "\n".join(
        f"{i*60000},100,101,99,100,1000" for i in range(1200)
    )
    return ResearchRequest(csv=data, strategy="rolling-breakout-retest",
                           quantity_step=.001, **kwargs)


def test_periods_are_disjoint_and_no_trades_is_not_a_win():
    result = evaluate(request())
    rows = result["results"]
    assert len(rows) == 6
    assert rows[0]["start"] == 500 * 60000
    assert rows[0]["end"] < rows[2]["start"]
    assert rows[2]["end"] < rows[4]["start"]
    assert rows[-1]["end"] == 1200 * 60000 - 1
    assert all(r["trade_count"] == 0 and r["win_rate_pct"] is None for r in rows)
    assert rows[1]["assumptions"]["spread_bps"] == 4
    assert result["experiment_sha256"] != evaluate(request(capital=20))["experiment_sha256"]


def test_small_budget_does_not_round_up_to_minimum():
    bars = aligned(long_setup() + [candle(110.30, 115, 105, 110.30)])
    result = replay(bars, initial=10, minimum_notional=100, quantity_step=.001)
    assert result["trade_count"] == 0
    assert result["rejected_entries"] == 1


def test_warmup_cannot_trade_and_extra_costs_reduce_profit():
    bars = aligned(long_setup() + [candle(110.30, 115, 105, 110.30)])
    assert replay(bars, start_index=len(bars))["trade_count"] == 0
    base = replay(bars, risk_fraction=.05)
    stress = replay(bars, risk_fraction=.05, spread_bps=10)
    # Compare equal units; risk sizing intentionally shrinks with increased costs.
    a, b = base["trades"][0], stress["trades"][0]
    assert b["net"] / b["quantity"] < a["net"] / a["quantity"]


def test_rejects_gaps_and_nonfinite_data():
    req = request()
    with pytest.raises(ValueError, match="consecutive"):
        evaluate(req.model_copy(update={"csv": req.csv.replace("60000,", "0,", 1)}))
    bars = aligned(long_setup())
    bars[0] = replace(bars[0], close=float("nan"))
    with pytest.raises(ValueError, match="invalid replay candle"):
        replay(bars)


def test_research_http_validation_and_origin():
    client = TestClient(create_app())
    payload = request().model_dump()
    response = client.post("/api/research/backtest", json={**payload, "leverage": 11})
    assert response.status_code == 422
    assert client.post("/api/research/backtest", json=payload,
                       headers={"Origin": "https://evil.example"}).status_code == 403
    response = client.post("/api/research/backtest", json={**payload, "csv": "bad"})
    assert response.status_code == 422
    assert "CSV needs" in response.json()["detail"]
