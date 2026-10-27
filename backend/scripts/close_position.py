"""Close a position and clear both order namespaces.

Also a live check of the thing that is easy to get wrong: algo orders are
invisible to openOrders and unaffected by allOpenOrders, so a sweep that
touches only one namespace leaves a live stop attached to a closed position.

Run: python scripts/close_position.py SYMBOL
"""

import asyncio
import sys
from decimal import Decimal

from scalper.config import ExchangeConfig
from scalper.exchange.client import BinanceFuturesClient


async def main(symbol: str) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    async with BinanceFuturesClient(ExchangeConfig()) as client:
        payload = await client.get_positions()
        rows = payload.get("result", payload)
        row = next(
            (r for r in rows if r.get("symbol") == symbol
             and float(r.get("positionAmt", 0)) != 0),
            None,
        )
        if row is None:
            print(f"{symbol}: nothing open")
            return 0

        amount = Decimal(str(row["positionAmt"]))
        side = "SELL" if amount > 0 else "BUY"
        print(f"closing {symbol}: {amount} via {side}")

        before = await client.list_algo_orders(symbol)
        print(f"protective orders before: {len(before)}")

        await client.place_order(
            symbol=symbol,
            side=side,
            order_type="MARKET",
            quantity=abs(amount),
            reduce_only=True,
            client_order_id=f"manual-close-{symbol[:8]}",
        )
        print("position closed at market")

        await client.cancel_all_orders(symbol)
        after = await client.list_algo_orders(symbol)
        print(f"protective orders after sweep: {len(after)}")
        if after:
            print("WARNING: orders survived the sweep; they would be orphaned")
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "SOLUSDT")))
