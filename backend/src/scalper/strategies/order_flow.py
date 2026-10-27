"""EMA pullback confirmed by synchronized depth and aggressive trade events."""

from decimal import Decimal

from scalper.strategies.base import Levels, Setup, StrategyContext
from scalper.strategies.ema_pullback import EmaTrendPullback


class OrderFlowPullback(EmaTrendPullback):
    id = "order-flow-pullback"
    name = "Order-Flow Pullback (Research)"
    summary = "EMA pullback with persistent top-20 depth and aggressive trade confirmation."
    requires_order_flow = True
    testnet_only = True

    def candle_setup(self, ctx: StrategyContext) -> Setup:
        """Reject absent candle setups before opening expensive event streams."""
        return EmaTrendPullback().assess(ctx)
    assumptions: tuple[str, ...] = (
        *EmaTrendPullback.assumptions,
        "Testnet execution only. Event confirmation, not an HFT or market-making strategy.",
        "Five depth observations spanning >=400ms; depth and trade imbalance >=20%.",
        "At least three aggregate trades and $1000 notional in a common <=1.5s window.",
        "Data age <=750ms; stream latency <=1s; fresh confirmation before submission.",
        "Candle-only replay cannot validate this strategy and is refused.",
    )

    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        if ctx.order_flow is None:
            raise ValueError("waiting for synchronized order-flow evidence")
        ctx.order_flow.validate(ctx.symbol, direction)
        quote = ctx.order_flow.ask if direction == "LONG" else ctx.order_flow.bid
        if abs(entry - Decimal(str(quote))) / entry > Decimal(".0005"):
            raise ValueError("entry diverged from event book")
        return super().levels(ctx, entry, direction)
