"""Reading what the account actually holds.

The dashboard showed profit and loss but never the capital those figures were
a fraction of, so a 2 USDT gain read the same on a 20 USDT account as on a
20000 USDT one. Equity is the denominator for every other number on the
screen.
"""

from decimal import Decimal

import pytest

from scalper.engine.account import (
    AccountBalance,
    open_positions,
    read_balance,
    read_leverage,
)


class FakeClient:
    def __init__(self, account=None, positions=None) -> None:
        self._account = account or {}
        self._positions = positions or []

    async def get_account(self):
        return self._account

    async def get_positions(self):
        return {"result": self._positions}


ACCOUNT = {
    "totalWalletBalance": "3225.82000000",
    "availableBalance": "3145.95000000",
    "totalUnrealizedProfit": "2.83000000",
    "totalMarginBalance": "3228.65000000",
}


class TestReadBalance:
    async def test_the_wallet_total_is_read(self):
        balance = await read_balance(FakeClient(account=ACCOUNT))
        assert balance.wallet == Decimal("3225.82")

    async def test_available_is_separate_from_the_wallet_total(self):
        # Margin posted against open positions is not available to open more.
        # Showing one figure for both invites sizing a trade that is refused.
        balance = await read_balance(FakeClient(account=ACCOUNT))
        assert balance.available == Decimal("3145.95")
        assert balance.available < balance.wallet

    async def test_equity_includes_open_profit(self):
        # Margin balance, which is wallet plus unrealised. It is the figure the
        # exchange liquidates against, so it is the one worth showing.
        balance = await read_balance(FakeClient(account=ACCOUNT))
        assert balance.equity == Decimal("3228.65")

    async def test_margin_in_use_is_derived_not_guessed(self):
        balance = await read_balance(FakeClient(account=ACCOUNT))
        assert balance.margin_used == Decimal("3225.82") - Decimal("3145.95")

    async def test_a_missing_field_does_not_crash_the_refresh_loop(self):
        # This runs every four seconds. A malformed payload must degrade to
        # unknown rather than take down position monitoring with it.
        balance = await read_balance(FakeClient(account={"canTrade": True}))
        assert balance.wallet == Decimal(0)

    async def test_a_non_finite_balance_is_refused(self):
        # NaN defeats every comparison, so it would pass any threshold check
        # downstream. It is rejected at the boundary instead.
        balance = await read_balance(FakeClient(account={"totalWalletBalance": "NaN"}))
        assert balance.wallet == Decimal(0)

    def test_describe_is_json_safe(self):
        described = AccountBalance(
            wallet=Decimal("3225.82"),
            available=Decimal("3145.95"),
            equity=Decimal("3228.65"),
        ).describe()
        assert described["wallet"] == "3225.82"
        assert isinstance(described["margin_used"], str)


class TestLiquidationPrice:
    """The exchange knows where liquidation is. We should not be guessing.

    Our own formula treats every position as isolated and ignores the rest of
    the wallet, which is the conservative read but not the true one. When the
    venue reports the figure, that figure wins.
    """

    def _row(self, **overrides):
        base = {
            "symbol": "SOLUSDT",
            "positionAmt": "1.83",
            "entryPrice": "109.62",
            "markPrice": "109.50",
            "liquidationPrice": "99.6988",
        }
        base.update(overrides)
        return base

    async def test_the_reported_liquidation_price_is_captured(self):
        positions = await open_positions(FakeClient(positions=[self._row()]))
        assert positions[0].liquidation_price == pytest.approx(99.6988)

    async def test_a_zero_liquidation_price_is_treated_as_absent(self):
        # Binance sends 0 for a cross-margin position with no liquidation
        # level. Rendering that as a price would put the marker at the far
        # left of every bar and read as imminent liquidation.
        positions = await open_positions(
            FakeClient(positions=[self._row(liquidationPrice="0")])
        )
        assert positions[0].liquidation_price is None

    async def test_a_missing_liquidation_price_is_absent_not_zero(self):
        row = self._row()
        del row["liquidationPrice"]
        positions = await open_positions(FakeClient(positions=[row]))
        assert positions[0].liquidation_price is None


ACCOUNT_WITH_POSITIONS = {
    **ACCOUNT,
    "positions": [
        {"symbol": "SOLUSDT", "positionAmt": "1.83", "leverage": "10"},
        {"symbol": "BTCUSDT", "positionAmt": "0.0025", "leverage": "25"},
        {"symbol": "XRPUSDT", "positionAmt": "0", "leverage": "5"},
    ],
}


class TestPerSymbolLeverage:
    """positionRisk does not report leverage; the account endpoint does.

    Adoption previously recorded every adopted position at 1x because the
    comment claimed the refresh loop would correct it and the refresh loop did
    no such thing. A position shown at 1x that is actually at 10x understates
    its return on equity tenfold and puts the liquidation warning on the wrong
    side of every threshold.
    """


    async def test_leverage_is_read_per_symbol(self):
        levels = await read_leverage(FakeClient(account=ACCOUNT_WITH_POSITIONS))
        assert levels["SOLUSDT"] == 10
        assert levels["BTCUSDT"] == 25

    async def test_flat_symbols_are_not_reported(self):
        # Every symbol on the account appears in this array, almost all flat.
        # Carrying them through would be several hundred entries of noise.
        levels = await read_leverage(FakeClient(account=ACCOUNT_WITH_POSITIONS))
        assert "XRPUSDT" not in levels

    async def test_a_missing_positions_array_is_empty_not_fatal(self):
        assert await read_leverage(FakeClient(account=ACCOUNT)) == {}

    async def test_an_unparseable_leverage_is_skipped(self):
        levels = await read_leverage(
            FakeClient(
                account={
                    "positions": [
                        {"symbol": "SOLUSDT", "positionAmt": "1.83", "leverage": "x"}
                    ]
                }
            )
        )
        assert levels == {}
