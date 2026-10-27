"""Verify the configured API credentials against the exchange.

Run: python scripts/check_credentials.py

Prints only a key preview, never the key or the secret.
"""

import asyncio
import sys

from scalper.config import ExchangeConfig
from scalper.envfiles import found_env_file
from scalper.exchange.client import BinanceFuturesClient
from scalper.exchange.errors import BinanceError

ACCOUNT_PATH = "/fapi/v2/account"


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("env file   :", found_env_file())
    try:
        config = ExchangeConfig()
    except Exception as exc:
        print("config     : FAILED to load")
        print(exc)
        return 1

    preview = f"{config.api_key[:6]}...{config.api_key[-4:]}"
    print("environment:", config.env.value)
    print("rest url   :", config.rest)
    print("api key    :", preview)
    print()

    async with BinanceFuturesClient(config) as client:
        try:
            account = await client._signed("GET", ACCOUNT_PATH, {})
        except BinanceError as exc:
            print("RESULT     : REJECTED by the exchange")
            print("code       :", exc.code)
            print("message    :", exc.exchange_message)
            print("what to do :", exc.guidance)
            return 1
        except Exception as exc:
            print("RESULT     : could not reach the exchange")
            print(type(exc).__name__, exc)
            return 1

    balance = account.get("totalWalletBalance", "unknown")
    available = account.get("availableBalance", "unknown")
    print("RESULT     : ACCEPTED")
    print("wallet     :", balance, "USDT")
    print("available  :", available, "USDT")
    print("can trade  :", account.get("canTrade"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
