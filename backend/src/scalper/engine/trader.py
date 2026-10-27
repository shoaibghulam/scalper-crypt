"""Execute a fresh signal from the selected strategy with frozen protection."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from decimal import ROUND_DOWN, Decimal
from itertools import pairwise

from scalper.core.filters import Rounding, SymbolFilters, round_qty_down, round_to_tick
from scalper.engine.bots import Bot, ManagedPosition
from scalper.engine.executor import OrderExecutor, UnprotectedPosition
from scalper.engine.plan import EntryAborted, PlanRejected, TradePlan
from scalper.exchange.market_data import MarketDataClient
from scalper.settings import AppSettings

log = logging.getLogger("scalper.engine.trader")
ENTRY_PRICE_ATTEMPTS = 4
ENTRY_PRICE_DELAY_S = 0.3


async def build_plan(
    bot: Bot,
    symbol: str,
    filters: SymbolFilters,
    market: MarketDataClient,
    leverage: int | None = None,
) -> TradePlan:
    from scalper.scanner.config import ScanConfig
    from scalper.scanner.evaluate import Rejection, evaluate_symbol
    from scalper.strategies import STRATEGIES
    from scalper.strategies.base import StrategyContext

    strategy = STRATEGIES.get(bot.strategy)
    if strategy.testnet_only:
        from scalper.config import TESTNET_REST

        if market.base_url != TESTNET_REST:
            raise PlanRejected("this research strategy executes only on testnet")
    candles_5m, candles_1m, tickers, books = await asyncio.gather(
        market.candles(symbol, "5m", limit=max(200, strategy.history_5m + 1)),
        market.candles(symbol, "1m", limit=strategy.fetch_limit_1m),
        market.tickers(symbol),
        market.book_tops(symbol),
    )
    if not candles_1m or not candles_5m:
        raise PlanRejected(f"{symbol}: not enough closed candles")
    now_ms = int(time.time() * 1000)
    for bars, interval in ((candles_1m, 60_000), (candles_5m, 300_000)):
        if any(b.open_time - a.open_time != interval for a, b in pairwise(bars)):
            raise PlanRejected(f"{symbol}: candle history has gaps or duplicates")
        if not 0 <= now_ms - bars[-1].close_time <= interval + 30_000:
            raise PlanRejected(f"{symbol}: stale or unfinished candles")
    max_age = getattr(strategy, "max_signal_age_s", 90) * 1000
    ticker, book = tickers.get(symbol), books.get(symbol)
    if ticker is None or book is None:
        raise PlanRejected(f"{symbol}: no current ticker or book")
    evidence = None
    if strategy.requires_order_flow:
        from scalper.strategies.order_flow import OrderFlowPullback

        if isinstance(strategy, OrderFlowPullback):
            preliminary = strategy.candle_setup(
                StrategyContext(symbol, candles_5m, candles_1m, ticker, book)
            )
            if not preliminary.ready:
                raise PlanRejected(f"{symbol}: {preliminary.note}")
        try:
            evidence = await market.order_flow(symbol)
        except Exception as exc:
            raise PlanRejected(f"order-flow acquisition failed: {type(exc).__name__}") from exc
        from scalper.exchange.market_data import BookTop

        book = BookTop(symbol, evidence.bid, evidence.ask)
    config = ScanConfig()
    if ticker.quote_volume < config.min_quote_volume_24h:
        raise PlanRejected(f"{symbol}: 24h volume ${ticker.quote_volume:,.0f} below "
                           f"${config.min_quote_volume_24h:,.0f} minimum")
    if book.spread_bps > config.max_spread_bps:
        raise PlanRejected(f"{symbol}: spread {book.spread_bps:.2f} bps exceeds "
                           f"{config.max_spread_bps:.2f} bps maximum")
    ctx = StrategyContext(symbol, candles_5m, candles_1m, ticker, book, evidence)
    candidate = evaluate_symbol(
        symbol, ticker, book, candles_5m, candles_1m, config, strategy, evidence
    )
    if isinstance(candidate, Rejection):
        raise PlanRejected(f"{symbol}: {candidate.reason}")
    if not candidate.setup_ready or candidate.direction not in {"LONG", "SHORT"}:
        raise PlanRejected(f"{symbol}: {candidate.setup_note}")
    if now_ms - candles_1m[-1].close_time > max_age:
        raise PlanRejected(f"{symbol}: strategy signal has expired")
    direction = candidate.direction
    entry = Decimal(str(book.ask if direction == "LONG" else book.bid))
    entry = round_to_tick(
        entry, filters.tick_size, Rounding.UP if direction == "LONG" else Rounding.DOWN
    )
    try:
        levels = strategy.execution_levels(ctx, entry, direction, filters.tick_size)
    except ValueError as exc:
        raise PlanRejected(f"{symbol}: {exc}") from exc
    stop = round_to_tick(
        levels.stop, filters.tick_size, Rounding.DOWN if direction == "LONG" else Rounding.UP
    )
    target = round_to_tick(
        levels.target, filters.tick_size, Rounding.UP if direction == "LONG" else Rounding.DOWN
    )
    effective = bot.leverage if leverage is None else leverage
    quantity = round_qty_down(bot.amount * Decimal(effective) / entry, filters.step_size)
    plan = TradePlan(
        bot.id,
        bot.next_seq(),
        symbol,
        direction,
        quantity,
        entry,
        stop,
        target,
        effective,
        candles_1m[-1].close_time,
    )
    # The strategy's own ratio, not the platform default: CTK trades at the
    # 0.86 its source specifies, and would otherwise be refused every time.
    plan.validate(filters, min_reward_to_risk=strategy.min_reward_to_risk)
    try:
        filters.validate_market(plan.quantity)
    except ValueError as exc:
        raise PlanRejected(str(exc)) from exc
    return plan


async def open_for_bot(
    bot: Bot,
    symbol: str,
    filters: SymbolFilters,
    market: MarketDataClient,
    executor: OrderExecutor,
    prepare: Callable[[TradePlan, float], Awaitable[None]] | None = None,
    budget: Decimal | None = None,
    settings: AppSettings | None = None,
) -> ManagedPosition:
    from scalper.config import Environment
    from scalper.engine.account import open_positions
    from scalper.strategies import STRATEGIES

    if (
        STRATEGIES.get(bot.strategy).testnet_only
        and executor.client.config.env is not Environment.TESTNET
    ):
        raise PlanRejected("this strategy is testnet-only")

    plan = await build_plan(bot, symbol, filters, market)
    if any(p.symbol == symbol for p in await open_positions(executor.client)):
        raise PlanRejected(f"{symbol}: the exchange already holds a position")
    cap = await executor.client.max_leverage(symbol)
    leverage = min(bot.leverage, cap) if cap else bot.leverage
    if leverage != bot.leverage:
        plan = await build_plan(bot, symbol, filters, market, leverage=leverage)
    if budget is not None:
        from scalper.engine.single_coin import size_to_budget

        if settings is None:
            raise PlanRejected("budget sizing requires account risk settings")
        plan = size_to_budget(plan, filters, budget, await executor.client.get_account(), settings)
    opened_at = time.time()
    if prepare is not None:
        await prepare(plan, opened_at)
    await executor.client.set_margin_type(symbol, bot.margin_mode)
    await executor.client.set_leverage(symbol, leverage)
    from scalper.strategies import STRATEGIES

    strategy = STRATEGIES.get(bot.strategy)
    if strategy.testnet_only:
        from scalper.config import Environment

        if executor.client.config.env is not Environment.TESTNET:
            raise PlanRejected("this strategy is testnet-only")
    if strategy.requires_order_flow:
        try:
            flow = await market.order_flow(symbol)
            flow.validate(symbol, plan.side)
            quote = Decimal(str(flow.ask if plan.is_long else flow.bid))
            if abs(quote - plan.entry) / plan.entry > Decimal(".0005"):
                raise ValueError("event quote moved beyond preflight tolerance")
            replace(plan, entry=quote).validate(filters)
        except Exception as exc:
            raise PlanRejected(f"order-flow preflight refused: {exc}") from exc
    expiry = getattr(strategy, "max_signal_age_s", 90)
    if time.time() * 1000 - plan.signal_time > expiry * 1000:
        raise PlanRejected("signal expired during preflight; no entry sent")
    if bot.state == "stopped":
        raise PlanRejected("Entries stopped before submission; no entry sent")
    try:
        # EMA targets sit close to the minimum reward/risk. A market fill one
        # tick worse can invalidate the frozen bracket. IOC cannot fill at a
        # worse price than the validated plan and never leaves a resting entry.
        bounded_entry = strategy.id == "ema-trend-pullback"
        response = await executor.client.place_order(
            symbol=symbol,
            side=plan.entry_side,
            order_type="LIMIT" if bounded_entry else "MARKET",
            price=plan.entry if bounded_entry else None,
            time_in_force="IOC" if bounded_entry else None,
            quantity=plan.quantity,
            client_order_id=plan.client_order_id("entry"),
        )
    except Exception as exc:
        # A timeout may occur after a fill. Never blindly retry an entry.
        raise UnprotectedPosition(
            f"{symbol}: entry outcome unknown; reconcile before retrying"
        ) from exc
    if bounded_entry:
        try:
            status = response["status"]
            executed = Decimal(str(response["executedQty"]))
            if status not in {"FILLED", "EXPIRED", "CANCELED", "EXPIRED_IN_MATCH"}:
                raise ValueError("entry has not reached a terminal status")
            if not executed.is_finite() or not 0 <= executed <= plan.quantity:
                raise ValueError("invalid filled quantity")
        except (KeyError, ValueError, ArithmeticError) as exc:
            raise UnprotectedPosition(
                f"{symbol}: IOC outcome unknown; reconcile before retrying"
            ) from exc
        if executed == 0:
            if status == "FILLED":
                raise UnprotectedPosition(
                    f"{symbol}: inconsistent IOC fill; reconcile before retrying"
                )
            raise PlanRejected(f"{symbol}: IOC expired unfilled; waiting for a new signal")
    position = ManagedPosition(
        symbol=symbol,
        side=plan.side,
        quantity=plan.quantity,
        entry=plan.entry,
        stop=plan.stop,
        target=plan.target,
        leverage=leverage,
        opened_at=opened_at,
        entry_order_id=response.get("orderId"),
        stop_client_id=plan.client_order_id("sl"),
        target_client_id=plan.client_order_id("tp"),
    )
    # Keep the record if an emergency exit fails; reconciliation must see it.
    bot.positions[symbol] = position
    try:
        price, quantity = await _actual_fill(executor, symbol, plan.side)
        position.entry, position.quantity, position.mark = price, quantity, price
        check_entry = round_to_tick(
            price, filters.tick_size, Rounding.UP if plan.is_long else Rounding.DOWN
        )
        actual = replace(plan, entry=check_entry, quantity=quantity)
        actual.validate(filters)
        if price <= plan.stop if plan.is_long else price >= plan.stop:
            raise PlanRejected("actual fill is beyond the planned stop")
        position.target_armed = await executor.attach_bracket(actual)
        position.stop_armed = True
    except UnprotectedPosition:
        # The executor already attempted an emergency exit.
        raise
    except PlanRejected as exc:
        await _close_unprotected(executor, replace(plan, quantity=position.quantity))
        raise EntryAborted(f"{symbol}: entry aborted after fill: {exc}; verifying exit") from exc
    except Exception:
        await _close_unprotected(executor, replace(plan, quantity=position.quantity))
        raise
    return position


async def _close_unprotected(executor: OrderExecutor, plan: TradePlan) -> None:
    try:
        await executor.client.place_order(
            symbol=plan.symbol,
            side=plan.exit_side,
            order_type="MARKET",
            quantity=plan.quantity,
            reduce_only=True,
            client_order_id=plan.client_order_id("panic"),
        )
    except Exception as exc:
        raise UnprotectedPosition(
            f"{plan.symbol}: emergency close failed; exposure needs reconciliation"
        ) from exc


async def _actual_fill(
    executor: OrderExecutor,
    symbol: str,
    side: str,
) -> tuple[Decimal, Decimal]:
    for attempt in range(ENTRY_PRICE_ATTEMPTS):
        try:
            payload = await executor.client.get_positions()
            rows = payload.get("result", payload)
            rows = [rows] if isinstance(rows, dict) else rows
            for row in rows:
                if row.get("symbol") != symbol:
                    continue
                quantity = Decimal(str(row.get("positionAmt", "0")))
                price = Decimal(str(row.get("entryPrice", "0")))
                if not quantity.is_finite() or not price.is_finite():
                    continue
                if price > 0 and (quantity > 0 if side == "LONG" else quantity < 0):
                    return price, abs(quantity)
        except Exception:
            log.warning("could not read filled position for %s", symbol)
        if attempt + 1 < ENTRY_PRICE_ATTEMPTS:
            await asyncio.sleep(ENTRY_PRICE_DELAY_S * (attempt + 1))
    raise PlanRejected(f"{symbol}: actual fill unavailable; refusing an estimated bracket")


def quantise(value: Decimal, places: str = "0.01") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_DOWN)
