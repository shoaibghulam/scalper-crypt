"""Shared read-only screening. Execution always revalidates a fresh plan."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from scalper.engine.bots import Bot
from scalper.exchange.market_data import MarketDataClient
from scalper.scanner.config import ScanConfig
from scalper.scanner.evaluate import Rejection, evaluate_symbol
from scalper.scanner.scan import liquidity_stage
from scalper.strategies import STRATEGIES
from scalper.strategies.ema_pullback import EmaTrendPullback


#: Requests in flight during a screening pass.
#:
#: The bound exists to stay inside the request weight budget, not to be
#: polite: at weight 2 a kline and roughly 0.8s a round trip, this many in
#: flight costs about 3,000 weight a minute against a 4,800 pause threshold,
#: leaving room for account reads and order placement. It was 8, chosen when
#: every bot re-downloaded its own candles; with that waste gone the budget
#: supports more, and a fleet watching the whole exchange needs it.
SCREEN_CONCURRENCY = 20

#: Seconds allowed for a pass before the work is abandoned.
_SCREEN_BASE_SECONDS = 20.0
_SCREEN_SECONDS_PER_SYMBOL = 2 * 0.8 / SCREEN_CONCURRENCY
_SCREEN_MAX_SECONDS = 600.0


def screening_deadline(symbols: int) -> float:
    """How long a pass over this many symbols is allowed to take.

    A flat deadline is wrong in both directions: too generous for a handful of
    symbols, and too short for the whole exchange, where it aborted every
    cycle and left every bot reporting a timeout that was really our own
    budget. Two requests per symbol, SCREEN_CONCURRENCY in flight, with a
    generous multiple for jitter - and a ceiling, because waiting forever
    would hide a genuinely stuck exchange.
    """
    estimate = _SCREEN_BASE_SECONDS + symbols * _SCREEN_SECONDS_PER_SYMBOL * 3
    return min(estimate, _SCREEN_MAX_SECONDS)


async def screen_watchlist(
    bots: list[Bot],
    market: MarketDataClient,
    auto_ids: set[str] | None = None,
    coverage: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Fetch each symbol once per history size, independent of bot count."""
    ready: dict[str, list[str]] = {bot.id: [] for bot in bots}
    if not bots:
        return ready
    filters, tickers, books = await asyncio.gather(
        market.symbol_filters(),
        market.tickers(),
        market.book_tops(),
    )
    config = ScanConfig()
    if auto_ids:
        selected, _ = liquidity_stage(tickers, books, set(filters), config)
        if coverage is not None:
            coverage.update(
                universe=len(filters),
                eligible=len(selected),
                checked_at=time.time(),
                completed=False,
            )
        for bot in bots:
            if bot.id in auto_ids and not (
                bot.positions or bot.entry_pending or bot.pending_closures
            ):
                bot.symbols = selected.copy()
                bot.symbol_notes = {}
    strategies = {bot.id: STRATEGIES.get(bot.strategy) for bot in bots}
    # One widest download per interval, shared by every strategy; then slice
    # back to each strategy's expected window to preserve signal semantics.
    symbols = sorted({symbol for bot in bots for symbol in bot.symbols} & filters.keys())
    five_limit = max(max(200, s.history_5m + 1) for s in strategies.values())
    one_limit = max(s.fetch_limit_1m for s in strategies.values())
    five_all, one_all = await asyncio.gather(
        market.candles_for(symbols, "5m", five_limit, concurrency=SCREEN_CONCURRENCY),
        market.candles_for(symbols, "1m", one_limit, concurrency=SCREEN_CONCURRENCY),
    )
    for bot in bots:
        bot.error = None
        bot.last_checked_at = time.time()
        strategy = strategies[bot.id]
        # Event confirmation belongs to execution, after a candle setup exists.
        assessment = EmaTrendPullback() if strategy.requires_order_flow else strategy
        five = {key: value[-max(200, strategy.history_5m + 1) :] for key, value in five_all.items()}
        one = {key: value[-strategy.fetch_limit_1m :] for key, value in one_all.items()}
        for symbol in bot.symbols:
            if symbol not in filters:
                note = "Unavailable on this exchange: not a tradeable USDT perpetual"
            elif symbol not in tickers or symbol not in books:
                note = "Market quote unavailable; retrying next cycle"
            elif not five.get(symbol) or not one.get(symbol):
                note = "Closed candle data unavailable; retrying next cycle"
            else:
                ticker, book = tickers[symbol], books[symbol]
                if ticker.quote_volume < config.min_quote_volume_24h:
                    note = (
                        f"24h volume ${ticker.quote_volume:,.0f} below "
                        f"${config.min_quote_volume_24h:,.0f} minimum"
                    )
                elif book.spread_bps > config.max_spread_bps:
                    note = (
                        f"Spread {book.spread_bps:.2f} bps exceeds "
                        f"{config.max_spread_bps:.2f} bps maximum"
                    )
                else:
                    candidate = await asyncio.to_thread(
                        evaluate_symbol,
                        symbol,
                        ticker,
                        book,
                        five[symbol],
                        one[symbol],
                        config,
                        assessment,
                    )
                    if isinstance(candidate, Rejection):
                        note = candidate.reason
                    elif not candidate.setup_ready:
                        note = candidate.setup_note
                    elif candidate.expires_at is not None and time.time() > candidate.expires_at:
                        note = "Confirmed setup expired; waiting for the next closed candle"
                    else:
                        ready[bot.id].append(symbol)
                        note = "Confirmed setup; checking fresh execution and account limits"
            bot.symbol_notes[symbol] = note
    if coverage is not None:
        coverage["completed"] = True
    return ready
