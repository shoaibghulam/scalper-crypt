"""The bracket is measured from the price actually paid.

This is a regression test for a bug found by running the system rather than by
reading it. A market order returns avgPrice of 0.00 when the fill has not been
booked yet. The code fell back to the expected price and computed the whole
bracket from a number that was never paid: expected 105.76 against an actual
105.52, which left the stop 0.23 percent away instead of 0.48 and below the
floor where fees stop being survivable.

So the entry is read back from the position, and the bracket is derived from
that, and the result is validated again.
"""

from decimal import Decimal

import pytest

from scalper.core.filters import SymbolFilters
from scalper.engine.bots import Bot
from scalper.engine.executor import OrderExecutor
from scalper.engine.plan import PlanRejected
from scalper.engine.trader import open_for_bot
from scalper.exchange.market_data import Candle
from tests.unit.test_trader_plan_building import FakeMarket, strategy_fixture  # noqa: F401

SOLUSDT = SymbolFilters(
    symbol="SOLUSDT",
    tick_size=Decimal("0.01"),
    step_size=Decimal("0.01"),
    min_qty=Decimal("0.01"),
    min_notional=Decimal("5"),
)


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


class FakeClient:
    """Records what was sent and reports a fill price of our choosing."""

    def __init__(self, actual_entry: str) -> None:
        self.actual_entry = actual_entry
        self.orders: list[dict] = []
        self.algo_orders: list[dict] = []

    async def set_margin_type(self, symbol, margin_type):
        return {}

    async def max_leverage(self, symbol):
        # No published cap in these fixtures, so the requested leverage stands.
        return None

    async def set_leverage(self, symbol, leverage):
        return {}

    async def place_order(self, **kwargs):
        self.orders.append(kwargs)
        # Exactly the observed behaviour: no usable fill price in the response.
        return {"orderId": 1, "avgPrice": "0.00"}

    async def place_algo_order(self, **kwargs):
        self.algo_orders.append(kwargs)
        return {"algoId": 1}

    async def get_positions(self):
        if not self.orders:
            return {"result": []}
        return {
            "result": [
                {
                    "symbol": "SOLUSDT",
                    "positionAmt": "2.36",
                    "entryPrice": self.actual_entry,
                }
            ]
        }


def a_bot() -> Bot:
    return Bot(
        id="x",
        name="bot-1",
        strategy="s",
        symbols=["SOLUSDT"],
        amount=Decimal("50"),
        leverage=5,
    )


class TestEntryPrice:
    async def test_the_recorded_entry_is_the_price_actually_paid(self):
        client = FakeClient("105.52")
        position = await open_for_bot(
            a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 105.76, 0.4)),
            OrderExecutor(client),
        )
        assert position.entry == Decimal("105.52")

    async def test_the_stop_is_measured_from_the_real_entry(self):
        client = FakeClient("105.52")
        position = await open_for_bot(
            a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 105.76, 0.4)),
            OrderExecutor(client),
        )
        distance = (position.entry - position.stop) / position.entry
        # The floor, not the distance that would result from the expected price.
        assert distance >= Decimal("0.0045")

    async def test_an_adverse_fill_is_closed_instead_of_moving_the_target(self):
        client = FakeClient("110.90")
        with pytest.raises(PlanRejected):
            await open_for_bot(a_bot(), "SOLUSDT", SOLUSDT,
                               FakeMarket(bars(120, 105.76, 0.4)), OrderExecutor(client))
        assert any(o.get("reduce_only") for o in client.orders)

    async def test_the_target_remains_the_pre_entry_strategy_target(self):
        from scalper.engine.trader import build_plan
        market = FakeMarket(bars(120, 105.76, 0.4))
        planned = await build_plan(a_bot(), "SOLUSDT", SOLUSDT, market)
        position = await open_for_bot(a_bot(), "SOLUSDT", SOLUSDT, market,
                                      OrderExecutor(FakeClient("105.52")))
        assert position.target == planned.target
        assert position.stop == planned.stop
        assert position.quantity == Decimal("2.36")


class TestProtection:
    async def test_both_protective_orders_are_placed(self):
        client = FakeClient("105.52")
        await open_for_bot(
            a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 105.76, 0.4)),
            OrderExecutor(client),
        )
        kinds = {o["order_type"] for o in client.algo_orders}
        assert kinds == {"STOP_MARKET", "TAKE_PROFIT_MARKET"}

    async def test_the_position_is_marked_protected_only_after_the_bracket(self):
        client = FakeClient("105.52")
        position = await open_for_bot(
            a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 105.76, 0.4)),
            OrderExecutor(client),
        )
        assert position.stop_armed is True

    async def test_the_entry_is_a_market_order_for_an_operator_entry(self):
        client = FakeClient("105.52")
        await open_for_bot(
            a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 105.76, 0.4)),
            OrderExecutor(client),
        )
        assert client.orders[0]["order_type"] == "MARKET"


class TestFloatNoiseFromTheExchange:
    """Prices come back with binary float noise and must be put on a tick.

    Observed live: an entry price of 108.80000000000001 against a tick size of
    0.01. Converting that straight to Decimal and validating rejected the plan
    after the entry had already filled, which left a real position with no
    stop attached.
    """

    async def test_average_entry_precision_is_preserved(self):
        client = FakeClient("108.80000000000001")
        position = await open_for_bot(
            a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 108.8, 0.4)),
            OrderExecutor(client),
        )
        assert position.entry == Decimal("108.80000000000001")

    async def test_the_bracket_also_lands_on_ticks(self):
        client = FakeClient("108.80000000000001")
        position = await open_for_bot(
            a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 108.8, 0.4)),
            OrderExecutor(client),
        )
        assert position.stop % SOLUSDT.tick_size == 0
        assert position.target % SOLUSDT.tick_size == 0

    async def test_the_position_is_still_protected(self):
        client = FakeClient("108.80000000000001")
        await open_for_bot(
            a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 108.8, 0.4)),
            OrderExecutor(client),
        )
        kinds = {o["order_type"] for o in client.algo_orders}
        assert kinds == {"STOP_MARKET", "TAKE_PROFIT_MARKET"}


class TestNothingIsLeftNaked:
    """Any failure after the entry fills must close the position.

    The executor already handled a stop that would not attach. It did not
    handle an exception raised between the fill and the bracket attempt, and
    that gap left a live position with no protection and no alert.
    """

    async def test_a_failure_after_the_fill_closes_the_position(self):
        class BrokenBracket(FakeClient):
            async def place_algo_order(self, **kwargs):
                raise RuntimeError("something unexpected")

        client = BrokenBracket("105.52")
        # Specifically the failure the bracket raised, not any error: a
        # connection fault would also satisfy a blind assertion here.
        with pytest.raises(RuntimeError, match="unexpected"):
            await open_for_bot(
                a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 105.76, 0.4)),
                OrderExecutor(client),
            )
        closes = [o for o in client.orders if o.get("reduce_only")]
        assert closes, "the position was left open after the bracket failed"

    async def test_the_emergency_close_is_reduce_only(self):
        class BrokenBracket(FakeClient):
            async def place_algo_order(self, **kwargs):
                raise RuntimeError("something unexpected")

        client = BrokenBracket("105.52")
        # Specifically the failure the bracket raised, not any error: a
        # connection fault would also satisfy a blind assertion here.
        with pytest.raises(RuntimeError, match="unexpected"):
            await open_for_bot(
                a_bot(), "SOLUSDT", SOLUSDT, FakeMarket(bars(120, 105.76, 0.4)),
                OrderExecutor(client),
            )
        closes = [o for o in client.orders if o.get("reduce_only")]
        assert closes[0]["order_type"] == "MARKET"


async def test_unavailable_fill_never_uses_expected_price(monkeypatch):
    from scalper.engine import trader
    monkeypatch.setattr(trader, "ENTRY_PRICE_DELAY_S", 0)
    client = FakeClient("0")
    with pytest.raises(PlanRejected, match="actual fill unavailable"):
        await open_for_bot(a_bot(), "SOLUSDT", SOLUSDT,
                           FakeMarket(bars(120, 105.76, 0.4)), OrderExecutor(client))
    assert not client.algo_orders
    assert any(o.get("reduce_only") for o in client.orders)
