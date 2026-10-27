import hashlib
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from scalper.api.app import create_app
from scalper.api.history import HistoryRequest, fetch_history, upstream_error
from scalper.backtest import replay
from scalper.config import LIVE_REST, Environment
from scalper.exchange.market_data import MarketDataClient
from scalper.settings import AppSettings
from scalper.strategies.research_registry import RESEARCH_STRATEGIES
from tests.unit.test_black_replay import aligned
from tests.unit.test_strategy_black import candle, long_setup


@pytest.mark.parametrize("fault", [None, "gap", "empty", "invalid"])
async def test_download_paginates_closed_bars_or_refuses_incomplete_history(fault):
    calls = []

    def respond(request):
        if request.url.path.endswith("exchangeInfo"):
            return httpx.Response(
                200,
                json={
                    "symbols": [
                        {
                            "symbol": "ETHUSDT",
                            "quoteAsset": "USDT",
                            "status": "TRADING",
                            "contractType": "PERPETUAL",
                            "filters": [
                                {"filterType": "LOT_SIZE", "stepSize": "0.01", "minQty": "0.02"},
                                {"filterType": "MIN_NOTIONAL", "notional": "20"},
                            ],
                        }
                    ]
                },
            )
        if request.url.path.endswith("time"):
            return httpx.Response(200, json={"serverTime": 2500 * 60000 + 15000})
        params = request.url.params
        start, limit = int(params["startTime"]), int(params["limit"])
        calls.append(start)
        assert params["symbol"] == "ETHUSDT" and params["interval"] == "1m"
        assert int(params["endTime"]) == 2500 * 60000 - 1
        rows = [
            [t, "100", "101", "99", "100", "10", t + 59999, "1000", 1, "5"]
            for t in range(start, start + limit * 60000, 60000)
        ]
        if fault == "empty":
            rows = []
        elif fault == "gap":
            rows[0][0] += 60000
        elif fault == "invalid":
            rows[0][2] = "98"
        return httpx.Response(200, json=rows)

    async with httpx.AsyncClient(
        base_url=LIVE_REST, transport=httpx.MockTransport(respond)
    ) as http:
        market = MarketDataClient(LIVE_REST, http=http)
        request = HistoryRequest(symbol="ETHUSDT", candles=2000)
        if fault:
            with pytest.raises(ValueError):
                await fetch_history(request, market)
            return
        result = await fetch_history(request, market)
        assert len(calls) == 5 and calls[0] == 500 * 60000
        assert result["candles"] == 2000 and len(result["csv"].splitlines()) == 2001
        assert result["end"] == 2500 * 60000 - 1
        assert result["quantity_step"] == 0.01 and result["minimum_quantity"] == 0.02
        assert result["minimum_notional"] == 20
        assert result["dataset_sha256"] == hashlib.sha256(result["csv"].encode()).hexdigest()


def test_history_validation_and_unavailable_upstream():
    client = TestClient(create_app())
    for body in ({"symbol": "http://evil/USDT"}, {"symbol": "ETHUSDT", "candles": 10001}):
        assert client.post("/api/research/history", json=body).status_code == 422
    assert (
        client.post(
            "/api/research/history",
            json={"symbol": "ETHUSDT"},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    response = httpx.Response(429, request=httpx.Request("GET", LIVE_REST))
    error = upstream_error(
        httpx.HTTPStatusError("rate limit", request=response.request, response=response)
    )
    assert error.status_code == 503 and "429" in error.detail


def test_testnet_catalog_and_live_creation_gate():
    testnet = TestClient(create_app(AppSettings(BINANCE_ENV=Environment.TESTNET)))
    entries = testnet.get("/api/strategies").json()["strategies"]
    assert set(RESEARCH_STRATEGIES) <= {s["id"] for s in entries}
    assert all(
        s["testnet_only"] and not s["manual_only"]
        for s in entries
        if s["id"] in RESEARCH_STRATEGIES and not s.get("backtest_only")
    )
    app = create_app(AppSettings(BINANCE_ENV=Environment.LIVE))
    app.state.engine.on_snapshot({"seq": 1}, now=time.monotonic())
    live = TestClient(app)
    assert {key for key, value in RESEARCH_STRATEGIES.items() if not value.backtest_only}.isdisjoint(
        s["id"] for s in live.get("/api/strategies").json()["strategies"]
    )
    assert (
        live.post(
            "/api/bots",
            json={"symbols": ["ETHUSDT"], "amount": "10", "leverage": 3, "strategy": "lab-01"},
        ).status_code
        == 400
    )
    assert live.get("/api/scan?strategy=lab-01").status_code == 404
    assert len(testnet.get("/api/research/catalog").json()) == 155


def test_downloaded_minimum_quantity_is_enforced_in_replay():
    bars = aligned(long_setup() + [candle(110.30, 115, 105, 110.30)])
    result = replay(bars, initial=10, minimum_quantity=100, quantity_step=0.001)
    assert result["trade_count"] == 0 and result["rejected_entries"] == 1
