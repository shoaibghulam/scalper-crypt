"""Offline, causal replay of video-inspired and research strategies.

No credentials, HTTP or exchange mutations.

This evaluates the pattern, not order-book liquidity or the live scanner. Input
is consecutive closed 1m candles. Same-bar stop/target ambiguity resolves to stop.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal
from math import isclose
from pathlib import Path

from scalper.core.fees import MAX_COST_TO_STOP_RATIO, MIN_STOP_FRACTION
from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.strategies import STRATEGIES
from scalper.strategies.base import Strategy, StrategyContext
from scalper.strategies.research_registry import RESEARCH_STRATEGIES


@dataclass(frozen=True)
class ReplayTrade:
    opened_at: int
    closed_at: int
    side: str
    entry: float
    exit: float
    quantity: float
    fees: float
    net: float
    reason: str


def aggregate_five(candles: list[Candle]) -> tuple[list[Candle], list[int]]:
    """Complete 5m groups and their close times. Partial groups are dropped."""
    groups: dict[int, list[Candle]] = {}
    for c in candles:
        groups.setdefault(c.open_time // 300000 * 300000, []).append(c)
    five = [
        Candle(
            start,
            bars[0].open,
            max(c.high for c in bars),
            min(c.low for c in bars),
            bars[-1].close,
            sum(c.volume for c in bars),
            start + 299999,
            sum(c.quote_volume for c in bars),
            sum(c.taker_buy_base for c in bars),
        )
        for start, bars in groups.items()
        if len(bars) == 5
    ]
    return five, [c.close_time for c in five]


def context_at(
    candles: list[Candle],
    five: list[Candle],
    closes: list[int],
    strategy: Strategy,
    index: int,
    prev: Candle,
    *,
    candles_validated: bool = False,
) -> StrategyContext:
    """The strategy's view at `index`, from closed bars only.

    Shared by replay and the signal pass on purpose: if the two built contexts
    separately, a cached signal series could stop matching what a live replay
    would have decided, and the cache would be silently wrong.
    """
    end = bisect.bisect_right(closes, prev.close_time)
    return StrategyContext(
        "REPLAYUSDT",
        five[max(0, end - max(100, strategy.history_5m)) : end],
        candles[max(0, index - max(strategy.candle_limit_1m, strategy.history_1m)) : index],
        Ticker("REPLAYUSDT", prev.close, 0, 0),
        BookTop("REPLAYUSDT", prev.close, prev.close),
        candles_validated=candles_validated,
    )


def resolve_strategy(strategy_id: str, research_strategy: Strategy | None = None) -> Strategy:
    """The strategy object a replay or signal pass will use."""
    if research_strategy is not None and (
        not research_strategy.backtest_only or research_strategy.id != strategy_id
    ):
        raise ValueError("custom replay strategies must be backtest-only and match strategy_id")
    strategy = research_strategy or (
        RESEARCH_STRATEGIES[strategy_id]
        if strategy_id in RESEARCH_STRATEGIES
        else STRATEGIES.get(strategy_id)
    )
    if strategy.requires_order_flow:
        raise ValueError("order-flow strategy requires event data; candle replay is unsupported")
    return strategy


def signal_series(
    candles: list[Candle],
    *,
    strategy_id: str,
    start_index: int = 0,
    research_strategy: Strategy | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[int, str]:
    """Which bars the strategy would enter on, and which way.

    Keyed by open time, so one series can serve any slice of the same candles.

    This deliberately answers for every eligible bar, including ones a given
    replay would skip because it already held a position or had spent its
    session quota. Those limits depend on execution - capital, costs, which
    period is being scored - while the signal does not, so a superset computed
    once is reusable by every pass over the same candles. A subset would not be.
    """
    strategy = resolve_strategy(strategy_id, research_strategy)
    five, closes = aggregate_five(candles)
    interval = strategy.signal_interval_s * 1000
    # One pass over the series instead of one per assessed bar. The checks are
    # the same ones data_error applies; doing them here means a context can
    # say so and skip re-reading the identical candles thousands of times.
    validated = Strategy.validate_candles(candles) is None and (
        Strategy.validate_candles(five) is None
    )
    found: dict[int, str] = {}
    for i in range(max(1, start_index), len(candles)):
        if progress is not None and i % 1000 == 0:
            progress(i, len(candles))
        bar = candles[i]
        if bar.open_time % interval:
            continue
        setup = strategy.assess(
            context_at(
                candles, five, closes, strategy, i, candles[i - 1],
                candles_validated=validated,
            )
        )
        if setup.ready and setup.direction is not None:
            found[bar.open_time] = setup.direction
    return found


def replay(
    candles: list[Candle],
    *,
    strategy_id: str = "the-minute-black",
    initial: float = 1000,
    risk_fraction: float = 0.01,
    fee_rate: float = 0.0005,
    slippage_bps: float = 2,
    spread_bps: float = 0,
    leverage: float = 5,
    minimum_notional: float = 0,
    quantity_step: float = 0,
    minimum_quantity: float = 0,
    start_index: int = 0,
    progress: Callable[[int, int], None] | None = None,
    research_strategy: Strategy | None = None,
    signals: Mapping[int, str] | None = None,
) -> dict[str, object]:
    if not all(
        math.isfinite(v)
        for v in (
            initial,
            risk_fraction,
            fee_rate,
            slippage_bps,
            spread_bps,
            leverage,
            minimum_notional,
            minimum_quantity,
            quantity_step,
        )
    ):
        raise ValueError("replay parameters must be finite")
    if initial <= 0 or not 0 < risk_fraction <= 0.05 or fee_rate < 0 or slippage_bps < 0:
        raise ValueError("invalid replay economics")
    if minimum_quantity < 0:
        raise ValueError("invalid minimum quantity")
    if spread_bps < 0 or not 1 <= leverage <= 10 or minimum_notional < 0 or quantity_step < 0:
        raise ValueError("invalid execution assumptions")
    if not 0 <= start_index <= len(candles):
        raise ValueError("invalid evaluation start")
    strategy = resolve_strategy(strategy_id, research_strategy)
    for i, c in enumerate(candles):
        values = (c.open, c.high, c.low, c.close, c.volume)
        if (
            any(not math.isfinite(v) for v in values)
            or c.low <= 0
            or c.volume < 0
            or c.low > min(c.open, c.close)
            or c.high < max(c.open, c.close)
        ):
            raise ValueError("invalid replay candle data")
        if c.open_time % 60000 or c.close_time != c.open_time + 59999:
            raise ValueError("replay requires aligned closed 1m candles")
        if i and c.open_time - candles[i - 1].open_time != 60000:
            raise ValueError("replay candles must be consecutive and ordered")
    # Aggregate only complete 5m groups; contexts slice by close time below.
    five, closes = aggregate_five(candles)
    equity, peak, drawdown = initial, initial, 0.0
    trades: list[ReplayTrade] = []
    # Position is created only from a signal observed on the previous close.
    position: tuple[int, str, float, float, float, float] | None = None
    slip = (slippage_bps + spread_bps / 2) / 10000
    min_stop = float(MIN_STOP_FRACTION)
    max_cost_ratio = float(MAX_COST_TO_STOP_RATIO)
    rejected = 0
    untradeable = 0
    no_fill = 0
    ambiguous_bars = 0
    session_entries: dict[str, int] = {}
    for i, bar in enumerate(candles):
        if progress is not None and i % 1000 == 0:
            progress(i, len(candles))
        if position is None and i >= max(1, start_index) and equity > 0:
            session = strategy.session_key(bar.open_time)
            if (strategy.max_trades_per_session
                    and session_entries.get(session, 0) >= strategy.max_trades_per_session):
                continue
            prev = candles[i - 1]
            if bar.open_time % (strategy.signal_interval_s * 1000):
                continue
            # A precomputed signal series is the only thing v2 supplies. It is
            # keyed by open time rather than index so the same cache serves any
            # period slice, and it decides only WHETHER a bar trades - every
            # price, size and exit below is still simulated here, so a cached
            # run and a live one cannot drift apart.
            ctx: StrategyContext | None = None
            if signals is None:
                ctx = context_at(candles, five, closes, strategy, i, prev)
                setup = strategy.assess(ctx)
                ready, direction = setup.ready, setup.direction
            else:
                direction = signals.get(bar.open_time)
                ready = direction is not None
            if ready and direction is not None:
                sign = 1 if direction == "LONG" else -1
                entry = bar.open * (1 + sign * slip)
                try:
                    # Signals use the preceding close; entry-sensitive validation
                    # and brackets must use the price this simulated trade pays.
                    if ctx is None:
                        ctx = context_at(candles, five, closes, strategy, i, prev)
                    levels = strategy.levels(ctx, Decimal(str(entry)), direction)
                except ValueError:
                    rejected += 1
                    continue
                stop, target = float(levels.stop), float(levels.target)
                # EMA live entries use bounded IOC. OHLC cannot establish queue
                # fills: conservatively skip any open beyond the signal limit.
                if strategy_id == "ema-trend-pullback" and sign * (entry - prev.close) > 0:
                    no_fill += 1
                    continue
                if sign * (entry - stop) <= 0 or sign * (target - entry) <= 0:
                    rejected += 1
                    continue
                # The same economic gate the live engine applies. Without it a
                # backtest scores trades the engine would refuse to place -
                # typically the ones whose stop is so tight that fees eat a
                # third of it - and reports them as if they had been available.
                stop_fraction = abs(entry - stop) / entry
                # Both bounds are inclusive. Strategies floor their stop at
                # exactly MIN_STOP_FRACTION, and the live path compares in
                # Decimal where that is an exact equality; here the same
                # quantity is a float and lands a hair either side of it. A
                # naive `<` rejected half of a perfectly legal sample.
                if (
                    stop_fraction < min_stop and not isclose(stop_fraction, min_stop, rel_tol=1e-9)
                ) or (
                    2 * fee_rate / stop_fraction > max_cost_ratio
                    and not isclose(2 * fee_rate / stop_fraction, max_cost_ratio, rel_tol=1e-9)
                ):
                    untradeable += 1
                    continue
                unit_loss = abs(entry - stop) + (entry + stop) * fee_rate + stop * slip
                qty = min(
                    equity * risk_fraction / unit_loss,
                    equity * 0.5 * leverage / entry,
                    equity * 0.95 / (entry / leverage + entry * fee_rate),
                )
                if quantity_step:
                    qty = float(
                        (Decimal(str(qty)) // Decimal(str(quantity_step)))
                        * Decimal(str(quantity_step))
                    )
                if qty <= 0 or qty < minimum_quantity or qty * entry < minimum_notional:
                    rejected += 1
                    continue
                position = (bar.open_time, direction, entry, stop, target, qty)
                session_entries[session] = session_entries.get(session, 0) + 1
        if position is None:
            continue
        opened, side, entry, stop, target, qty = position
        sign = 1 if side == "LONG" else -1
        deadline = strategy.scheduled_exit(opened)
        session_exit = deadline is not None and bar.open_time >= deadline
        if not session_exit and ((bar.low <= stop and bar.high >= target) if sign == 1 else (bar.high >= stop and bar.low <= target)):
            ambiguous_bars += 1
        reason = ""
        price = bar.close
        if session_exit:
            price = bar.open
            reason = "session_end"
        elif bar.low <= stop if sign == 1 else bar.high >= stop:
            price = min(bar.open, stop) if sign == 1 else max(bar.open, stop)
            reason = "stop"
        elif bar.high >= target if sign == 1 else bar.low <= target:
            price = target
            reason = "target"
        elif i == len(candles) - 1:
            reason = "end_of_data"
        if reason:
            price *= 1 - sign * slip
            fees = (entry + price) * qty * fee_rate
            net = (price - entry) * qty * sign - fees
            equity += net
            peak = max(peak, equity)
            drawdown = max(drawdown, (peak - equity) / peak)
            trades.append(
                ReplayTrade(opened, bar.open_time if session_exit else bar.close_time,
                            side, entry, price, qty, fees, net, reason)
            )
            position = None
    wins = sum(t.net for t in trades if t.net > 0)
    losses = -sum(t.net for t in trades if t.net < 0)
    winning = [t.net for t in trades if t.net > 0]
    losing = [t.net for t in trades if t.net < 0]
    max_streak = streak = 0
    curve = [{"at": candles[start_index].open_time if start_index < len(candles) else 0,
              "equity": initial}]
    running = initial
    for trade in trades:
        streak = streak + 1 if trade.net < 0 else 0
        max_streak = max(max_streak, streak)
        running += trade.net
        curve.append({"at": trade.closed_at, "equity": running})
    return {
        "strategy_id": strategy.id,
        "strategy": strategy.id,
        "initial_equity": initial,
        "final_equity": equity,
        "net": equity - initial,
        "closed_equity_drawdown_pct": drawdown * 100,
        "profit_factor": wins / losses if losses > 0 else None,
        "trade_count": len(trades),
        "win_rate_pct": 100 * sum(t.net > 0 for t in trades) / len(trades) if trades else None,
        "expectancy": (equity - initial) / len(trades) if trades else None,
        "total_fees": sum(t.fees for t in trades),
        "rejected_entries": rejected,
        # Signals the engine would have refused on economics alone. Reported
        # separately from `rejected_entries` so a strategy whose stops are
        # simply too tight to trade is visible as that, rather than looking
        # like a strategy that rarely signals.
        "untradeable_entries": untradeable,
        "unfilled_entries": no_fill,
        "return_pct": (equity / initial - 1) * 100,
        "average_win": sum(winning) / len(winning) if winning else None,
        "average_loss": sum(losing) / len(losing) if losing else None,
        "max_consecutive_losses": max_streak,
        "ambiguous_bars": ambiguous_bars,
        "tp_hits": sum(t.reason == "target" for t in trades),
        "sl_hits": sum(t.reason == "stop" for t in trades),
        "forced_exits": sum(t.reason == "end_of_data" for t in trades),
        "session_exits": sum(t.reason == "session_end" for t in trades),
        "equity_curve": curve,
        "trades": [asdict(t) for t in trades],
        "assumptions": {
            "fee_rate_per_side": fee_rate,
            "slippage_bps_per_side": slippage_bps,
            "spread_bps": spread_bps,
            "leverage": leverage,
            "minimum_notional": minimum_notional,
            "quantity_step": quantity_step,
            "minimum_quantity": minimum_quantity,
            "funding_included": False,
            "live_scanner_simulated": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--slippage-bps", type=float, default=2)
    parser.add_argument(
        "--strategy",
        default="the-minute-black",
        choices=[s for s in STRATEGIES.ids() if not STRATEGIES.get(s).requires_order_flow]
        + list(RESEARCH_STRATEGIES),
    )
    args = parser.parse_args()
    with args.csv.open(newline="", encoding="utf-8-sig") as handle:
        bars = [
            Candle.from_raw(
                [
                    int(r["open_time"]),
                    r["open"],
                    r["high"],
                    r["low"],
                    r["close"],
                    r["volume"],
                    int(r["open_time"]) + 59999,
                    float(r["volume"]) * float(r["close"]),
                    0,
                    float(r["volume"]) / 2,
                ]
            )
            for r in csv.DictReader(handle)
        ]
    result = replay(bars, strategy_id=args.strategy, slippage_bps=args.slippage_bps)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
