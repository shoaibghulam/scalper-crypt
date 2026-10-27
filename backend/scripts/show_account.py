"""What the exchange actually holds. The source of truth, not our record."""

import asyncio
import sys

from scalper.config import ExchangeConfig
from scalper.exchange.client import BinanceFuturesClient


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    config = ExchangeConfig()

    async with BinanceFuturesClient(config) as client:
        positions = await client.get_positions()
        rows = positions.get("result", positions)
        open_rows = [r for r in rows if float(r.get("positionAmt", 0)) != 0]

        print("OPEN POSITIONS ON THE EXCHANGE:", len(open_rows))
        for r in open_rows:
            print(
                f"  {r['symbol']:<10} qty {r['positionAmt']:<10} "
                f"entry {r['entryPrice']:<12} mark {r.get('markPrice','?'):<12} "
                f"uPnL {r.get('unRealizedProfit','?')}"
            )
            print(f"             liquidation {r.get('liquidationPrice','?')}")

        for r in open_rows:
            symbol = r["symbol"]
            orders = await client.list_algo_orders(symbol)
            print()
            print(f"PROTECTIVE ORDERS HELD BY THE EXCHANGE FOR {symbol}:", len(orders))
            for o in orders:
                print(
                    f"  {o.get('type','?'):<20} trigger {o.get('triggerPrice','?'):<12} "
                    f"closePosition={o.get('closePosition','?')} "
                    f"workingType={o.get('workingType','?')}"
                )


asyncio.run(main())
