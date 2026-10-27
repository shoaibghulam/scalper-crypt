"""Run a real scan against live Binance market data.

Run: python scripts/smoke_scan.py
Needs no API credentials.
"""

import asyncio
import sys

from scalper.exchange.market_data import MarketDataClient
from scalper.scanner.scan import run_scan


async def main() -> None:
    # Some listed symbols carry non-ASCII characters, and the Windows console
    # defaults to a codepage that cannot encode them.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    async with MarketDataClient("https://fapi.binance.com") as market:
        result = await run_scan(market)

    print("universe        :", result.universe_size, "USDT perpetuals")
    print("after liquidity :", result.shortlist_size)
    print("candidates      :", len(result.candidates))
    ready = [c for c in result.candidates if c.setup_ready]
    print("setup ready now :", len(ready))
    print("duration        :", result.duration_s, "s")
    print("rejected by     :", result.rejection_counts)
    print()

    print(
        f"{'SYMBOL':<13}{'SCORE':>6} {'REGIME':>9} {'ATR%':>6} {'ADX':>6} "
        f"{'ER':>5} {'RVOL':>6} {'STOP%':>7} {'C/S':>5}  SETUP"
    )
    for c in result.candidates[:15]:
        flag = "READY" if c.setup_ready else "     "
        print(
            f"{c.symbol:<13}{c.score:>6.2f} {c.regime.value:>9} {c.atr_pct:>6.2f} "
            f"{c.adx:>6.1f} {c.efficiency_ratio:>5.2f} {c.rvol_5m:>6.2f} "
            f"{c.stop_distance_pct:>7.2f} {c.cost_to_stop:>5.2f}  {flag} {c.setup_note}"
        )

    print()
    print("rejection reasons, one per stage:")
    seen: set[str] = set()
    for r in result.rejections:
        if r.stage in seen:
            continue
        seen.add(r.stage)
        print(f"  {r.symbol:<14} [{r.stage}] {r.reason}")


if __name__ == "__main__":
    asyncio.run(main())
