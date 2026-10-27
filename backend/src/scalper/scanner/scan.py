"""Run a full market scan.

Four stages, cheapest first, which is what keeps the whole thing inside the
request weight budget. Two whole-market requests reduce roughly five hundred
symbols to a few dozen, and only those few dozen cost a kline request each.

Every rejected symbol is kept, with the stage and the reason. A scanner that
returns a short list and no explanation cannot be tuned, and tuning it blindly
against live money is how thresholds get loosened until the edge disappears.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field, replace

from scalper.exchange.market_data import BookTop, MarketDataClient, Ticker
from scalper.scanner.config import ScanConfig
from scalper.scanner.evaluate import (
    Candidate,
    Rejection,
    evaluate_symbol,
    score_candidate,
)
from scalper.strategies import DEFAULT_STRATEGY_ID, STRATEGIES
from scalper.strategies.base import Strategy


@dataclass
class ScanResult:
    candidates: list[Candidate] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    universe_size: int = 0
    shortlist_size: int = 0
    duration_s: float = 0.0
    scanned_at: float = 0.0
    strategy_id: str = DEFAULT_STRATEGY_ID

    @property
    def rejection_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rejection in self.rejections:
            counts[rejection.stage] = counts.get(rejection.stage, 0) + 1
        return counts


def liquidity_stage(
    tickers: dict[str, Ticker], books: dict[str, BookTop], tradeable: set[str], config: ScanConfig
) -> tuple[list[str], list[Rejection]]:
    """Reduce the universe using only the two whole-market requests.

    Deliberately the first gate, because it is free relative to everything
    after it and removes the large majority of symbols.
    """
    rejections: list[Rejection] = []
    passed: list[tuple[str, float]] = []

    for symbol in sorted(tradeable):
        ticker = tickers.get(symbol)
        book = books.get(symbol)
        if ticker is None or book is None:
            rejections.append(Rejection(symbol, "liquidity", "no market data"))
            continue

        # Defence in depth. The parser already refuses non-finite values, but
        # this is where the consequence lands: every filter below is a
        # comparison, and every comparison against NaN is False, so a poisoned
        # value would pass all of them and then sort unpredictably in the list
        # an operator uses to choose what to trade.
        if not math.isfinite(ticker.quote_volume) or not math.isfinite(book.spread_bps):
            rejections.append(
                Rejection(
                    symbol,
                    "liquidity",
                    "exchange returned a non-finite price or volume",
                )
            )
            continue

        if ticker.quote_volume < config.min_quote_volume_24h:
            rejections.append(
                Rejection(
                    symbol,
                    "liquidity",
                    f"24h volume ${ticker.quote_volume / 1e6:.0f}M is below the "
                    f"${config.min_quote_volume_24h / 1e6:.0f}M minimum",
                )
            )
            continue

        spread = book.spread_bps
        if spread > config.max_spread_bps:
            rejections.append(
                Rejection(
                    symbol,
                    "liquidity",
                    f"spread {spread:.1f} bps exceeds {config.max_spread_bps:.1f} bps, "
                    "which would eat the edge on entry",
                )
            )
            continue

        passed.append((symbol, ticker.quote_volume))

    passed.sort(key=lambda item: item[1], reverse=True)
    if config.top_n_by_volume <= 0:
        return [symbol for symbol, _ in passed], rejections
    keep = [symbol for symbol, _ in passed[: config.top_n_by_volume]]

    for symbol, _ in passed[config.top_n_by_volume :]:
        rejections.append(
            Rejection(
                symbol,
                "liquidity",
                f"outside the top {config.top_n_by_volume} by volume",
            )
        )
    return keep, rejections


async def run_scan(
    market: MarketDataClient,
    config: ScanConfig | None = None,
    strategy: Strategy | None = None,
) -> ScanResult:
    config = config or ScanConfig()
    strategy = strategy or STRATEGIES.get(DEFAULT_STRATEGY_ID)
    started = time.monotonic()

    filters, tickers, books = await asyncio.gather(
        market.symbol_filters(), market.tickers(), market.book_tops()
    )

    shortlist, rejections = liquidity_stage(tickers, books, set(filters), config)

    candles_5m, candles_1m = await asyncio.gather(
        market.candles_for(shortlist, "5m", limit=max(200, strategy.history_5m + 1)),
        market.candles_for(
            shortlist, "1m", limit=strategy.fetch_limit_1m
        ),
    )

    candidates: list[Candidate] = []
    flow_results = {}
    if strategy.requires_order_flow:
        selected = shortlist[:3]
        results = await asyncio.gather(
            *(market.order_flow(symbol) for symbol in selected), return_exceptions=True
        )
        flow_results = dict(zip(selected, results, strict=True))
    for symbol in shortlist:
        evidence = None
        if strategy.requires_order_flow:
            result = flow_results.get(symbol)
            if result is None or isinstance(result, BaseException):
                reason = (
                    f"order-flow unavailable: {type(result).__name__}"
                    if result is not None
                    else "outside top-three event sampling limit"
                )
                rejections.append(Rejection(symbol, "order_flow", reason))
                continue
            evidence = result
            books[symbol] = BookTop(symbol, evidence.bid, evidence.ask)
        outcome = evaluate_symbol(
            symbol,
            tickers[symbol],
            books[symbol],
            candles_5m.get(symbol, []),
            candles_1m.get(symbol, []),
            config,
            strategy,
            evidence,
        )
        if isinstance(outcome, Rejection):
            rejections.append(outcome)
        else:
            candidates.append(outcome)

    # Score after evaluation so ranking sees the full picture rather than
    # deciding while filters are still running.
    # replace rather than reconstructing through vars(): the latter works only
    # while the field set happens to match the constructor signature, and would
    # break silently the first time Candidate grows a computed field.
    ranked = sorted(
        (replace(c, score=score_candidate(c, config)) for c in candidates),
        key=lambda c: c.score,
        reverse=True,
    )

    return ScanResult(
        candidates=ranked,
        rejections=rejections,
        universe_size=len(filters),
        shortlist_size=len(shortlist),
        duration_s=round(time.monotonic() - started, 2),
        scanned_at=time.time(),
        strategy_id=strategy.id,
    )
