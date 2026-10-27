"""Taking responsibility for a position the engine did not open.

Adoption is the only path from "the exchange holds this and we cannot explain
it" back to a managed position. It has to be conservative, because the failure
mode is a bot believing it controls a stop that does not exist.

The protective levels are read back from the exchange. Inventing them would
silently move a level the operator had already set, and claiming a stop that is
not there is worse than refusing to adopt at all.
"""

from decimal import Decimal

import pytest

from scalper.engine.adopt import (
    AdoptionRefused,
    adopt_position,
    needs_protection,
    protective_levels,
)
from scalper.engine.reconciler import ExchangePosition


class FakeClient:
    def __init__(self, orders) -> None:
        self._orders = orders
        self.asked: list[str] = []

    async def list_algo_orders(self, symbol):
        self.asked.append(symbol)
        return self._orders


LONG = ExchangePosition(
    symbol="SOLUSDT", quantity=1.83, entry_price=109.62, liquidation_price=99.69
)
SHORT = ExchangePosition(
    symbol="SOLUSDT", quantity=-1.83, entry_price=109.62, liquidation_price=119.5
)

STOP_ORDER = {
    "type": "STOP_MARKET",
    "triggerPrice": "108.90",
    "side": "SELL",
    "closePosition": True,
}
TP_ORDER = {
    "type": "TAKE_PROFIT_MARKET",
    "triggerPrice": "111.06",
    "side": "SELL",
    "closePosition": True,
}


class TestReadingLevelsBack:
    async def test_the_stop_comes_from_the_exchange(self):
        position = await adopt_position(FakeClient([STOP_ORDER, TP_ORDER]), LONG)
        assert position.stop == Decimal("108.90")

    async def test_the_target_comes_from_the_exchange(self):
        position = await adopt_position(FakeClient([STOP_ORDER, TP_ORDER]), LONG)
        assert position.target == Decimal("111.06")

    async def test_the_entry_and_size_come_from_the_position(self):
        position = await adopt_position(FakeClient([STOP_ORDER, TP_ORDER]), LONG)
        assert position.entry == Decimal("109.62")
        assert position.quantity == Decimal("1.83")

    async def test_quantity_is_always_positive_and_side_carries_direction(self):
        # A negative quantity would flip the sign of every profit calculation
        # downstream, where the side is what is actually consulted.
        position = await adopt_position(
            FakeClient(
                [
                    {
                        "type": "STOP_MARKET",
                        "triggerPrice": "110.40",
                        "side": "BUY",
                        "closePosition": True,
                    },
                    {
                        "type": "TAKE_PROFIT_MARKET",
                        "triggerPrice": "108.10",
                        "side": "BUY",
                        "closePosition": True,
                    },
                ]
            ),
            SHORT,
        )
        assert position.side == "SHORT"
        assert position.quantity == Decimal("1.83")

    async def test_the_adopted_position_is_marked_protected(self):
        position = await adopt_position(FakeClient([STOP_ORDER, TP_ORDER]), LONG)
        assert position.stop_armed is True

    async def test_prices_stay_decimal(self):
        position = await adopt_position(FakeClient([STOP_ORDER, TP_ORDER]), LONG)
        assert isinstance(position.stop, Decimal)
        assert str(position.stop) == "108.90"


class TestRefusals:
    async def test_a_position_with_no_stop_is_refused(self):
        # Adopting it would create a bot that reports an armed stop for a
        # position the exchange will not close. That is the one lie this
        # dashboard must never tell.
        with pytest.raises(AdoptionRefused, match="stop"):
            await adopt_position(FakeClient([TP_ORDER]), LONG)

    async def test_a_position_with_no_orders_at_all_is_refused(self):
        with pytest.raises(AdoptionRefused):
            await adopt_position(FakeClient([]), LONG)

    async def test_a_long_whose_stop_sits_above_entry_is_refused(self):
        # Nonsense geometry, and it would render as a position already past
        # its own stop. Better to leave it orphaned and visible.
        with pytest.raises(AdoptionRefused, match="below"):
            await adopt_position(
                FakeClient(
                    [
                        {
                            "type": "STOP_MARKET",
                            "triggerPrice": "110.90",
                            "side": "SELL",
                            "closePosition": True,
                        },
                        TP_ORDER,
                    ]
                ),
                LONG,
            )

    async def test_a_non_finite_trigger_price_is_refused(self):
        with pytest.raises(AdoptionRefused):
            await adopt_position(
                FakeClient(
                    [
                        {
                            "type": "STOP_MARKET",
                            "triggerPrice": "NaN",
                            "side": "SELL",
                            "closePosition": True,
                        },
                        TP_ORDER,
                    ]
                ),
                LONG,
            )

    async def test_a_flat_position_is_refused(self):
        with pytest.raises(AdoptionRefused):
            await adopt_position(
                FakeClient([STOP_ORDER, TP_ORDER]),
                ExchangePosition(symbol="SOLUSDT", quantity=0.0, entry_price=109.62),
            )


class TestMissingTarget:
    async def test_a_missing_target_is_derived_from_the_stop(self):
        # Unlike the stop, a missing target is survivable: the position is
        # still protected, and a derived target only decides where the bar
        # ends. Derived at 2R and flagged rather than refusing the adoption.
        position = await adopt_position(FakeClient([STOP_ORDER]), LONG)
        risk = Decimal("109.62") - Decimal("108.90")
        assert position.target == Decimal("109.62") + risk * 2


LIVE_ROW_STOP = {
    "algoId": 1000000209383477,
    "clientAlgoId": "bot-2-1-sl",
    "algoType": "CONDITIONAL",
    "orderType": "STOP_MARKET",
    "symbol": "SOLUSDT",
    "side": "SELL",
    "positionSide": "BOTH",
    "timeInForce": "GTE_GTC",
    "quantity": "0.0",
    "closePosition": True,
    "algoStatus": "NEW",
    "triggerPrice": "108.9",
    "price": "0.0",
}
LIVE_ROW_TP = {
    **LIVE_ROW_STOP,
    "clientAlgoId": "bot-2-1-tp",
    "orderType": "TAKE_PROFIT_MARKET",
    "triggerPrice": "111.06",
}


class TestTheFieldNameTheAlgoEndpointActuallyUses:
    """Regression: the algo namespace names the field orderType, not type.

    Verified against the live testnet account. openAlgoOrders returns rows
    shaped like the one below, and reading `type` found nothing, so every
    adoption was refused with "the exchange holds no stop for it" while both
    protective legs were sitting right there. A refusal that says the stop is
    missing when the stop exists is worse than a crash: it is a confident
    wrong answer about whether money is protected.
    """

    async def test_a_real_algo_payload_is_adopted(self):
        position = await adopt_position(FakeClient([LIVE_ROW_TP, LIVE_ROW_STOP]), LONG)
        assert position.stop == Decimal("108.9")
        assert position.target == Decimal("111.06")

    async def test_the_legacy_type_field_is_still_read(self):
        # Kept because an account can hold orders placed before the algo
        # migration, and those use `type`.
        position = await adopt_position(FakeClient([STOP_ORDER, TP_ORDER]), LONG)
        assert position.stop == Decimal("108.90")


class TestProtectingAnUnprotectedPosition:
    """The deadlock this resolves.

    A live testnet account held 1204 ENA with no stop of any kind. Adoption
    refused it, correctly: recording a stop that does not exist is the one lie
    this dashboard must never tell. But refusing also meant the engine would
    not manage it, trading stayed disabled, and the only way out was closing a
    real position by hand on Binance.

    So there is a second door. The operator may ask the engine to ATTACH a
    stop and then adopt. That places a real order, so it is never automatic
    and never implied by a plain adopt - it is a separate, explicit request.

    The levels are derived from volatility rather than invented: the same ATR
    and fee-floor arithmetic every other trade in this platform uses.
    """

    async def test_a_protected_position_needs_no_new_orders(self):
        # The ordinary path must not place orders on a position that is
        # already protected, or every adoption would double its brackets.
        client = FakeClient([STOP_ORDER, TP_ORDER])
        assert needs_protection(await client.list_algo_orders("SOLUSDT")) is False

    async def test_a_position_with_no_stop_needs_protection(self):
        assert needs_protection([]) is True

    async def test_a_position_with_only_a_target_needs_protection(self):
        # A take-profit is not protection. The downside is still unbounded.
        assert needs_protection([TP_ORDER]) is True

    def test_levels_sit_the_right_side_of_the_entry_for_a_long(self):
        levels = protective_levels(entry=Decimal("0.1667"), atr=Decimal("0.0009"), side="LONG")
        assert levels.stop < Decimal("0.1667") < levels.target

    def test_levels_are_mirrored_for_a_short(self):
        levels = protective_levels(entry=Decimal("0.1667"), atr=Decimal("0.0009"), side="SHORT")
        assert levels.target < Decimal("0.1667") < levels.stop

    def test_the_stop_clears_the_fee_floor(self):
        # A tiny ATR must not produce a stop that fees would eat. This is the
        # same floor every other trade is held to.
        from scalper.core.fees import MIN_STOP_FRACTION

        entry = Decimal("0.1667")
        levels = protective_levels(entry=entry, atr=Decimal("0.000001"), side="LONG")
        assert (entry - levels.stop) / entry >= MIN_STOP_FRACTION

    def test_a_wide_atr_widens_the_stop(self):
        # Volatility-derived, not a fixed percentage: a symbol that moves more
        # needs more room before the stop is noise rather than protection.
        entry = Decimal("0.1667")
        tight = protective_levels(entry=entry, atr=Decimal("0.0005"), side="LONG")
        wide = protective_levels(entry=entry, atr=Decimal("0.005"), side="LONG")
        assert wide.stop < tight.stop

    def test_the_target_clears_the_minimum_reward(self):
        from scalper.engine.plan import MIN_REWARD_TO_RISK

        entry = Decimal("0.1667")
        levels = protective_levels(entry=entry, atr=Decimal("0.0009"), side="LONG")
        risk = entry - levels.stop
        assert (levels.target - entry) / risk >= MIN_REWARD_TO_RISK
