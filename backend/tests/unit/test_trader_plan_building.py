"""Turning a symbol into a sized, protected trade.

The stop comes from five-minute volatility and is floored where the round-trip
fee stops being a small fraction of it. That floor is the single most important
number in the system: a stop tighter than it cannot be profitable no matter how
good the entry was.
"""

import time
from dataclasses import replace
from decimal import Decimal

import pytest

from scalper.core.fees import MIN_STOP_FRACTION
from scalper.core.filters import SymbolFilters
from scalper.engine.bots import Bot
from scalper.engine.plan import PlanRejected
from scalper.engine.trader import build_plan
from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.indicators.core import atr
from scalper.strategies import STRATEGIES
from scalper.strategies.base import Levels, Setup, Strategy

SOLUSDT = SymbolFilters(
    symbol="SOLUSDT",
    tick_size=Decimal("0.01"),
    step_size=Decimal("0.01"),
    min_qty=Decimal("0.01"),
    min_notional=Decimal("5"),
)


class FakeMarket:
    """Supplies candles without a network."""

    def __init__(self, series: list[Candle]) -> None:
        # Not named `candles`: the attribute would shadow the method below.
        self.series = series

    async def candles(self, symbol: str, interval: str, limit: int = 200):
        interval_ms = 60_000 if interval == "1m" else 300_000
        end = int(time.time() * 1000) // interval_ms * interval_ms - 1
        return [replace(c, open_time=end - (len(self.series)-i)*interval_ms + 1,
                        close_time=end - (len(self.series)-1-i)*interval_ms)
                for i, c in enumerate(self.series)]

    async def tickers(self, symbol=None):
        return {"SOLUSDT": Ticker("SOLUSDT", self.series[-1].close, 0, 1e9)}

    async def book_tops(self, symbol=None):
        price = self.series[-1].close
        return {"SOLUSDT": BookTop("SOLUSDT", price, price)}


def bars(count: int, price: float, swing: float) -> list[Candle]:
    out = []
    for i in range(count):
        close = price + (swing if i % 2 else -swing)
        out.append(
            Candle(
                open_time=i,
                open=close,
                high=close + swing,
                low=close - swing,
                close=close,
                volume=1000.0,
                close_time=i,
                quote_volume=close * 1000.0,
                taker_buy_base=500.0,
            )
        )
    return out


def a_bot(amount: str = "50", leverage: int = 5) -> Bot:
    return Bot(
        id="x",
        name="bot-1",
        strategy="s",
        symbols=["SOLUSDT"],
        amount=Decimal(amount),
        leverage=leverage,
    )


class TestStrategy(Strategy):
    __test__ = False
    id = "s"
    direction = "LONG"
    ready = True

    def assess(self, ctx):
        return Setup.entry(self.direction, "fixture") if self.ready else Setup.waiting("no signal")

    def levels(self, ctx, entry, direction):
        value = atr([c.high for c in ctx.candles_5m], [c.low for c in ctx.candles_5m],
                    [c.close for c in ctx.candles_5m])
        distance = max(Decimal(str(value)) * Decimal("1.5"), entry * MIN_STOP_FRACTION)
        sign = Decimal(1) if direction == "LONG" else Decimal(-1)
        return Levels(entry-sign*distance, entry+sign*distance*2)


@pytest.fixture(autouse=True)
def strategy_fixture(monkeypatch):
    strategy = TestStrategy()
    monkeypatch.setitem(STRATEGIES._strategies, "s", strategy)
    return strategy


class TestSizing:
    async def test_notional_is_margin_times_leverage(self):
        plan = await build_plan(
            a_bot("50", 5), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 100.0, 0.5))
        )
        # 50 USDT of margin at 5x is 250 of exposure.
        assert plan.notional == pytest.approx(Decimal("250"), abs=Decimal("2"))

    async def test_more_leverage_buys_more_of_the_asset(self):
        market = FakeMarket(bars(120, 100.0, 0.5))
        low = await build_plan(a_bot("50", 2), "SOLUSDT", SOLUSDT, market)
        high = await build_plan(a_bot("50", 10), "SOLUSDT", SOLUSDT, market)
        assert high.quantity > low.quantity

    async def test_quantity_lands_on_the_step_size(self):
        plan = await build_plan(
            a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 100.0, 0.5))
        )
        assert plan.quantity % SOLUSDT.step_size == 0


class TestStopPlacement:
    async def test_a_quiet_market_is_refused_by_the_economic_gate(self):
        with pytest.raises(PlanRejected, match="ATR"):
            await build_plan(a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 100.0, 0.005)))

    async def test_a_volatile_market_gets_a_wider_stop(self):
        quiet = await build_plan(a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 100.0, 0.2)))
        wild = await build_plan(a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 100.0, 0.6)))
        assert wild.stop_fraction > quiet.stop_fraction

    async def test_the_target_is_twice_the_risk(self):
        plan = await build_plan(
            a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 100.0, 0.5))
        )
        assert float(plan.reward_to_risk) == pytest.approx(2.0, abs=0.05)

    async def test_the_plan_passes_its_own_validation(self):
        plan = await build_plan(
            a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 100.0, 0.5))
        )
        plan.validate(SOLUSDT)


class TestGuards:
    async def test_too_little_history_is_refused(self):
        with pytest.raises(PlanRejected, match="candles"):
            await build_plan(
                a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(10, 100.0, 0.5))
            )

    async def test_the_sequence_advances_so_order_ids_stay_unique(self):
        bot = a_bot()
        market = FakeMarket(bars(120, 100.0, 0.5))
        first = await build_plan(bot, "SOLUSDT", SOLUSDT, market)
        second = await build_plan(bot, "SOLUSDT", SOLUSDT, market)
        assert first.client_order_id("entry") != second.client_order_id("entry")


async def test_short_signal_builds_short_bracket(strategy_fixture):
    strategy_fixture.direction = "SHORT"
    plan = await build_plan(a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 100, 0.5)))
    assert plan.side == "SHORT"
    assert plan.target < plan.entry < plan.stop
    assert plan.bot_id == "x"


async def test_absent_signal_does_not_generate_an_entry(strategy_fixture):
    strategy_fixture.ready = False
    with pytest.raises(PlanRejected, match="no signal"):
        await build_plan(a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 100, 0.5)))
