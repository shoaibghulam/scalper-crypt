"""Find the endpoint that lists open algo orders.

The documented path returned -5000. The orphan sweeper depends on being able to
list protective orders, so guessing is not acceptable.
"""

import asyncio
import sys

from scalper.config import ExchangeConfig
from scalper.exchange.client import BinanceFuturesClient
from scalper.exchange.errors import BinanceError

CANDIDATES = [
    "/fapi/v1/algoOpenOrders",
    "/fapi/v1/algoOrders",
    "/fapi/v1/openAlgoOrders",
    "/fapi/v1/algo/openOrders",
    "/sapi/v1/algo/futures/openOrders",
    "/fapi/v1/openOrders",
]


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    async with BinanceFuturesClient(ExchangeConfig()) as client:
        for path in CANDIDATES:
            try:
                result = await client._signed("GET", path, {"symbol": "SOLUSDT"})
                body = str(result)
                print(f"OK    {path}")
                print(f"      {body[:220]}")
            except BinanceError as exc:
                print(f"FAIL  {path}  [{exc.code}] {exc.exchange_message[:70]}")
            except Exception as exc:
                print(f"ERR   {path}  {type(exc).__name__}")


asyncio.run(main())
