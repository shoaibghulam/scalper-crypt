import time
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scalper.config import TESTNET_REST, Environment
from scalper.engine.bots import Bot
from scalper.engine.plan import PlanRejected
from scalper.engine.trader import build_plan, open_for_bot
from scalper.scanner.scan import run_scan
from scalper.strategies import STRATEGIES
from tests.unit.test_ema_pullback import context
from tests.unit.test_order_flow_research import evidence
from tests.unit.test_research_candidates import breakout, reversion, sweep
from tests.unit.test_trader_plan_building import SOLUSDT


class Market:
    base_url = TESTNET_REST

    def __init__(self, ctx):
        self.ctx = ctx
        self.fail_flow = False

    async def candles(self, symbol, interval, limit=200):
        return self.ctx.candles_1m if interval == "1m" else self.ctx.candles_5m

    async def tickers(self, symbol=None):
        return {self.ctx.symbol: self.ctx.ticker}

    async def book_tops(self, symbol=None):
        return {self.ctx.symbol: self.ctx.book}

    async def order_flow(self, symbol):
        if self.fail_flow:
            raise TimeoutError("fixture stream unavailable")
        return replace(evidence(), bid=self.ctx.book.bid, ask=self.ctx.book.ask)

    async def symbol_filters(self):
        return {self.ctx.symbol: SOLUSDT}

    async def candles_for(self, symbols, interval, limit=200):
        return {self.ctx.symbol: await self.candles(self.ctx.symbol, interval, limit)}


def bot(strategy):
    return Bot(
        id="research",
        name="Research",
        strategy=strategy,
        symbols=["SOLUSDT"],
        amount=Decimal("50"),
        leverage=3,
    )


@pytest.mark.parametrize(
    "strategy,factory",
    [
        ("ema-trend-pullback", context),
        ("rolling-breakout-retest", breakout),
        ("range-vwap-reversion", reversion),
        ("liquidity-sweep-reclaim", sweep),
        ("order-flow-pullback", context),
    ],
)
async def test_planner_uses_each_registered_strategy(monkeypatch, strategy, factory):
    ctx = factory()
    monkeypatch.setattr(
        "scalper.engine.trader.time.time", lambda: ctx.candles_1m[-1].close_time / 1000 + 1
    )
    plan = await build_plan(bot(strategy), "SOLUSDT", SOLUSDT, Market(ctx))
    assert plan.side == "LONG"
    assert plan.stop < plan.entry < plan.target
    plan.validate(SOLUSDT)


async def test_flow_planner_refuses_live_data_and_missing_stream(monkeypatch):
    ctx = context()
    monkeypatch.setattr(
        "scalper.engine.trader.time.time", lambda: ctx.candles_1m[-1].close_time / 1000 + 1
    )
    market = Market(ctx)
    market.base_url = "https://fapi.binance.com"
    with pytest.raises(PlanRejected, match="only on testnet"):
        await build_plan(bot("order-flow-pullback"), "SOLUSDT", SOLUSDT, market)
    market.base_url = TESTNET_REST
    market.fail_flow = True
    with pytest.raises(PlanRejected, match="acquisition failed"):
        await build_plan(bot("order-flow-pullback"), "SOLUSDT", SOLUSDT, market)


async def test_scanner_passes_evidence_and_reports_unavailable_stream():
    market = Market(context())
    result = await run_scan(market, strategy=STRATEGIES.get("order-flow-pullback"))
    assert result.candidates[0].setup_ready
    market.fail_flow = True
    result = await run_scan(market, strategy=STRATEGIES.get("order-flow-pullback"))
    assert not result.candidates
    assert result.rejections[0].stage == "order_flow"


@pytest.mark.parametrize("fault", ["stale", "reversed", "moved", "unavailable"])
async def test_order_preflight_rechecks_events_before_submission(monkeypatch, fault):
    ctx = context()
    monkeypatch.setattr(
        "scalper.engine.trader.time.time", lambda: ctx.candles_1m[-1].close_time / 1000 + 1
    )
    market = Market(ctx)
    plan = await build_plan(bot("order-flow-pullback"), "SOLUSDT", SOLUSDT, market)
    monkeypatch.setattr("scalper.engine.trader.build_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr("scalper.engine.account.open_positions", AsyncMock(return_value=[]))
    flow = evidence()
    if fault == "stale":
        flow = replace(flow, received_at=time.monotonic() - 2)
    if fault == "reversed":
        flow = replace(flow, trade_imbalance=-0.5)
    if fault == "moved":
        flow = replace(flow, bid=102, ask=102.01)
    market.order_flow = AsyncMock(
        return_value=flow, side_effect=TimeoutError() if fault == "unavailable" else None
    )
    client = SimpleNamespace(
        config=SimpleNamespace(env=Environment.TESTNET),
        max_leverage=AsyncMock(return_value=3),
        set_margin_type=AsyncMock(),
        set_leverage=AsyncMock(),
        place_order=AsyncMock(),
    )
    with pytest.raises(PlanRejected, match="preflight refused"):
        await open_for_bot(
            bot("order-flow-pullback"), "SOLUSDT", SOLUSDT, market, SimpleNamespace(client=client)
        )
    client.place_order.assert_not_called()


@pytest.mark.parametrize(
    "strategy_id", ["order-flow-pullback", "lab-01", "user-20", "funded-price-action"]
)
async def test_order_flow_never_mutates_live_account(strategy_id):
    client = SimpleNamespace(config=SimpleNamespace(env=Environment.LIVE))
    with pytest.raises(PlanRejected, match="testnet-only"):
        await open_for_bot(
            bot(strategy_id),
            "SOLUSDT",
            SOLUSDT,
            Market(context()),
            SimpleNamespace(client=client),
        )
