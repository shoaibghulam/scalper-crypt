"""Public production-market history for research; no credentials or orders."""

import asyncio
import csv
import hashlib
import io
import math
from typing import Any

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field

from scalper.config import LIVE_REST
from scalper.exchange.market_data import Candle, MarketDataClient
from scalper.symbols import SYMBOL_PATTERN

_download_gate = asyncio.Lock()


class HistoryRequest(BaseModel):
    symbol: str = Field(pattern=SYMBOL_PATTERN)
    candles: int = Field(default=4320, ge=2000, le=10000)
    end_time: int | None = Field(default=None, ge=0)


def upstream_error(exc: Exception) -> HTTPException:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return HTTPException(
            503, f"Binance historical data unavailable (HTTP {code}). Retry later or upload a CSV."
        )
    return HTTPException(503, "Cannot load Binance historical data. Retry later or upload a CSV.")


async def symbols() -> dict[str, object]:
    try:
        async with MarketDataClient(LIVE_REST) as market:
            filters = await market.symbol_filters()
        return {
            "source": "Binance USD-M Futures public live-market data",
            "symbols": sorted(filters),
        }
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise upstream_error(exc) from exc


async def fetch_history(request: HistoryRequest, market: MarketDataClient) -> dict[str, Any]:
    filters = await market.symbol_filters()
    if request.symbol not in filters:
        raise ValueError("Choose an active USDT perpetual symbol from the coin list")
    rules = filters[request.symbol]
    now = int((await market._get("/fapi/v1/time"))["serverTime"])
    # Freeze one interval for every page; never include the forming candle.
    end = now // 60000 * 60000
    if request.end_time is not None:
        if request.end_time > end:
            raise ValueError("History end must not be in the future or an unfinished candle")
        end = request.end_time // 60000 * 60000
    start = end - request.candles * 60000
    if start < 0:
        raise ValueError("History period starts before the Unix epoch")
    cursor = start
    bars: list[Candle] = []
    while cursor < end:
        page = await market._get(
            "/fapi/v1/klines",
            {
                "symbol": request.symbol,
                "interval": "1m",
                "startTime": cursor,
                "endTime": end - 1,
                "limit": min(499, (end - cursor) // 60000),
            },
        )
        if not isinstance(page, list) or not page:
            raise ValueError(
                "Not enough history in this period; choose another coin or shorter period"
            )
        for raw in page:
            bar = Candle.from_raw(raw)
            if (
                bar.open_time != cursor
                or bar.close_time != cursor + 59999
                or cursor >= end
                or bar.low <= 0
                or bar.volume < 0
                or bar.low > min(bar.open, bar.close)
                or bar.high < max(bar.open, bar.close)
                or not all(
                    math.isfinite(v) for v in (bar.open, bar.high, bar.low, bar.close, bar.volume)
                )
            ):
                raise ValueError(
                    "Historical candles are incomplete, invalid or have gaps; no test was run"
                )
            bars.append(bar)
            cursor += 60000
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["open_time", "open", "high", "low", "close", "volume"])
    writer.writerows((b.open_time, b.open, b.high, b.low, b.close, b.volume) for b in bars)
    data = out.getvalue()
    if len(data.encode()) > 2_000_000:
        raise ValueError("Dataset exceeds 2 MB; choose a shorter period")
    return {
        "csv": data,
        "symbol": request.symbol,
        "candles": len(bars),
        "start": start,
        "end": end - 1,
        "fetched_at": now,
        "source": "Binance USD-M Futures public live-market data",
        "dataset_sha256": hashlib.sha256(data.encode()).hexdigest(),
        "minimum_notional": float(rules.min_notional),
        "quantity_step": float(rules.step_size),
        "minimum_quantity": float(rules.min_qty),
        "note": "Current filters may differ historically. Fees/slippage are assumptions.",
    }


async def history(request: HistoryRequest) -> dict[str, Any]:
    if _download_gate.locked():
        raise HTTPException(429, "A history download is active; retry after it finishes")
    async with _download_gate:
        try:
            async with asyncio.timeout(90), MarketDataClient(LIVE_REST) as market:
                return await fetch_history(request, market)
        except (httpx.HTTPError, TimeoutError) as exc:
            raise upstream_error(exc) from exc
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise HTTPException(422, str(exc)) from exc
