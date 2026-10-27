"""Prove the public market data path works against the real exchange.

Run: python scripts/smoke_market_data.py
Needs no API credentials, which is the whole point.
"""

import asyncio

from scalper.exchange.market_data import MarketDataClient


async def main() -> None:
    async with MarketDataClient("https://fapi.binance.com") as md:
        filters = await md.symbol_filters()
        tickers = await md.tickers()
        books = await md.book_tops()

        print("USDT perpetuals trading :", len(filters))
        print("tickers                 :", len(tickers))
        print("book tops               :", len(books))

        btc = books["BTCUSDT"]
        print("BTCUSDT spread          : %.3f bps" % btc.spread_bps)

        candles = await md.candles("BTCUSDT", "5m", 50)
        print("closed 5m candles       :", len(candles))
        print("last close              :", candles[-1].close)
        print("last bar delta pct      : %+.3f" % candles[-1].delta_pct)


if __name__ == "__main__":
    asyncio.run(main())
