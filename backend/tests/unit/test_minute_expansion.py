from dataclasses import replace
from decimal import Decimal

import pytest

from scalper.api.research import ResearchRequest, catalog
from scalper.engine.auto_trade import start_fleet
from scalper.engine.bots import BotRegistry
from scalper.engine.commands import AutoTradeRequest
from scalper.exchange.market_data import BookTop
from scalper.strategies import STRATEGIES
from scalper.strategies.minute_expansion import MINUTE_STRATEGIES
from tests.unit.test_laboratory import context


def test_twenty_new_candidates_are_discoverable_and_testnet_only():
    assert len(MINUTE_STRATEGIES) == 20
    assert len({s.name for s in MINUTE_STRATEGIES.values()}) == 20
    fleet = start_fleet(BotRegistry(), {}, AutoTradeRequest(amount="10", leverage=3))
    # Every registered strategy gets a bot. The literal is a tripwire:
    # bump it deliberately when a strategy ships, so an accidental
    # registration cannot slip in unnoticed.
    assert len(fleet["bot_ids"]) == len(STRATEGIES.ids()) == 148
    assert set(MINUTE_STRATEGIES) <= {s["id"] for s in catalog()}
    for strategy in MINUTE_STRATEGIES.values():
        assert strategy.testnet_only and not strategy.manual_only
        assert strategy.signal_interval_s == 60
        assert strategy.describe()["research_only"]
        assert str(strategy.describe()["source"]).startswith("https://")
        ResearchRequest(csv="unused", strategy=strategy.id, quantity_step=0.001)


@pytest.mark.parametrize("strategy", MINUTE_STRATEGIES.values(), ids=MINUTE_STRATEGIES.keys())
def test_data_guards_and_common_bracket(strategy):
    ctx = context()
    assert strategy.assess(ctx).direction in {None, "LONG", "SHORT"}
    assert not strategy.assess(replace(ctx, candles_1m=ctx.candles_1m[-199:])).ready
    assert not strategy.assess(replace(ctx, book=BookTop("LABUSDT", float("nan"), 1))).ready
    bad = [*ctx.candles_1m]
    bad[-1] = replace(bad[-1], open_time=bad[-1].open_time + 60000)
    assert "gap" in strategy.assess(replace(ctx, candles_1m=bad)).note
    for side in ("LONG", "SHORT"):
        entry = Decimal(str(ctx.price))
        levels = strategy.levels(ctx, entry, side)
        if side == "LONG":
            assert levels.stop < entry < levels.target
        else:
            assert levels.target < entry < levels.stop
        assert abs(entry - levels.stop) >= entry * Decimal("0.0045")
        assert abs(levels.target - entry) == 2 * abs(entry - levels.stop)


def test_api_and_replay_share_the_same_research_windows():
    from scalper.strategies.base import Strategy

    ctx = context()
    for strategy in MINUTE_STRATEGIES.values():
        normalized = Strategy.research_context(strategy, ctx)
        assert len(normalized.candles_1m) == 200
        assert strategy.assess(ctx) == strategy.assess(normalized)
