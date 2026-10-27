"""Compiled candle replay. Decisions at close, execution at next open.

Stops win ambiguous stop/target candles. Limits live for one candle and require
price to trade THROUGH the limit. Trailing levels update only after a bar closes.
This is an OHLC approximation, not an order-book or liquidation simulator.
"""

import math

import numpy as np
from numba import njit

from scalper.research.data.models import INTERVALS
from scalper.research.features import Features
from scalper.research.schema import SearchConfig, StrategyDefinition


@njit(cache=True, nogil=True)
def replay(
    prices,
    times,
    entries,
    exits,
    funding,
    first,
    last,
    capital,
    direction,
    stop_pct,
    target_pct,
    trail_pct,
    fraction,
    leverage,
    cooldown,
    max_hold,
    limit_order,
    offset,
    fee,
    friction,
    daily_limit,
    dd_limit,
    loss_limit,
    killed,
    finalize=True,
):
    n = last - first
    equity = np.empty(n)
    # entry index, exit index, entry, exit, quantity, NET pnl, fees, funding, reason
    trades = np.zeros((n, 9))
    count, entry_i, next_entry, consecutive = 0, -1, first, 0
    balance, peak, day_start = capital, capital, capital
    day = times[first] // 86400000
    qty, entry, stop, target, paid_fee, paid_funding = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    halted, exposure = killed, 0
    total_fees, total_funding = 0.0, 0.0
    for i in range(first, last):
        o, h, low, close = prices[i, 0], prices[i, 1], prices[i, 2], prices[i, 3]
        if times[i] // 86400000 != day:
            day = times[i] // 86400000
            day_start = balance + qty * direction * (o - entry)
        # Funding at the opening timestamp belongs to positions already held.
        if qty > 0:
            payment = qty * direction * funding[i]
            balance -= payment
            paid_funding += payment
            total_funding += payment
        new_entry = False
        if qty == 0 and not halted and i >= next_entry and i > 0 and entries[i - 1]:
            fill = o * (1 + direction * friction)
            allowed = True
            if limit_order:
                limit = prices[i - 1, 3] * (1 - direction * offset)
                allowed = low < limit if direction == 1 else h > limit
                # No optimistic improvement and never violate limit price.
                fill = (
                    min(o * (1 + friction), limit)
                    if direction == 1
                    else max(o * (1 - friction), limit)
                )
            if allowed and balance > 0:
                entry, entry_i = fill, i
                qty = balance * fraction * leverage / entry
                paid_fee, paid_funding = qty * entry * fee, 0.0
                balance -= paid_fee
                total_fees += paid_fee
                stop, target = (
                    entry * (1 - direction * stop_pct),
                    entry * (1 + direction * target_pct),
                )
                new_entry = True
        if qty > 0:
            exposure += 1
            reason, price = 0, 0.0
            # Worst intrabar marked equity enforces account loss ceilings.
            risk_floor = max(peak * (1 - dd_limit), day_start * (1 - daily_limit))
            risk_price = entry + direction * (risk_floor - balance) / qty
            effective_stop = max(stop, risk_price) if direction == 1 else min(stop, risk_price)
            stop_hit = low <= effective_stop if direction == 1 else h >= effective_stop
            target_hit = h >= target if direction == 1 else low <= target
            gap_stop = o <= effective_stop if direction == 1 else o >= effective_stop
            if not new_entry and not gap_stop and (exits[i - 1] or i - entry_i >= max_hold):
                price, reason = o, 3
            elif stop_hit:
                price = min(o, effective_stop) if direction == 1 else max(o, effective_stop)
                # New limit orders cannot exit at the pre-fill opening price.
                if new_entry and limit_order:
                    price = effective_stop
                reason = 5 if effective_stop != stop else 1
            elif target_hit and not (new_entry and limit_order):
                price, reason = target, 2
            elif i == last - 1 and finalize:
                price, reason = close, 4
            if reason:
                price *= 1 - direction * friction
                closing_fee = qty * price * fee
                total_fees += closing_fee
                raw_pnl = qty * direction * (price - entry)
                balance += raw_pnl - closing_fee
                net = raw_pnl - paid_fee - closing_fee - paid_funding
                trades[count] = np.array(
                    [
                        entry_i,
                        i,
                        entry,
                        price,
                        qty,
                        net,
                        paid_fee + closing_fee,
                        paid_funding,
                        reason,
                    ]
                )
                count += 1
                consecutive = consecutive + 1 if net < 0 else 0
                qty = 0.0
                next_entry = i + cooldown + 1
                if reason == 5 or consecutive >= loss_limit:
                    halted = True
            elif trail_pct > 0:
                stop = (
                    max(stop, close * (1 - trail_pct))
                    if direction == 1
                    else min(stop, close * (1 + trail_pct))
                )
        marked = balance + qty * direction * (close - entry)
        peak = max(peak, marked)
        if (
            marked <= 0
            or marked <= peak * (1 - dd_limit)
            or marked <= day_start * (1 - daily_limit)
        ):
            halted = True
        equity[i - first] = marked
    return equity, trades[:count], exposure, halted, total_fees, total_funding, qty, entry


def metrics(equity, trades, capital, interval_ms, exposure, halted):
    curve = np.r_[capital, equity]
    changes = np.diff(curve)
    returns = np.divide(changes, curve[:-1], out=np.zeros_like(changes), where=curve[:-1] > 0)
    peaks = np.maximum.accumulate(curve)
    dd = 1 - curve / peaks
    pnl = trades[:, 5]
    gross_profit, gross_loss = float(pnl[pnl > 0].sum()), float(-pnl[pnl < 0].sum())
    annual = 365 * 86400000 / interval_ms
    deviation = float(returns.std())
    downside = float(np.sqrt(np.mean(np.minimum(returns, 0) ** 2)))
    wins = losses = max_wins = max_losses = 0
    for trade in pnl:
        wins = wins + 1 if trade > 0 else 0
        losses = losses + 1 if trade < 0 else 0
        max_wins, max_losses = max(max_wins, wins), max(max_losses, losses)
    days = len(equity) * interval_ms / 86400000
    final = float(curve[-1])
    annualized = None
    if days >= 30 and final > 0:
        exponent = math.log(final / capital) * 365 / days
        annualized = math.expm1(exponent) if exponent < 700 else None
    return {
        "total_return": final / capital - 1,
        "net_profit": final - capital,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "win_rate": float(np.mean(pnl > 0)) if len(pnl) else 0.0,
        "trades": len(pnl),
        "average_trade": float(pnl.mean()) if len(pnl) else 0.0,
        "expectancy": float(pnl.mean()) if len(pnl) else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "sharpe": float(returns.mean() / deviation * math.sqrt(annual)) if deviation else 0.0,
        "sortino": float(returns.mean() / downside * math.sqrt(annual)) if downside else None,
        "max_drawdown": float(dd.max()),
        "average_drawdown": float(dd.mean()),
        "max_consecutive_wins": max_wins,
        "max_consecutive_losses": max_losses,
        "annualized_return": annualized,
        "exposure": exposure / len(equity),
        "fees_paid": float(trades[:, 6].sum()),
        "funding_paid": float(trades[:, 7].sum()),
        "final_equity": final,
        "halted": bool(halted),
    }


class Backtester:
    def __init__(self, features: Features):
        self.features = features
        self.dataset = features.dataset
        self.funding = np.zeros(len(self.dataset.bars))
        interval = INTERVALS[self.dataset.request.timeframe]
        for event in self.dataset.funding_events:
            i = (event.timestamp - self.dataset.request.start) // interval
            # Binance settlement records can be milliseconds after the nominal
            # boundary. Treat <=1s settlement jitter as funding before open orders;
            # retain original timestamps in the immutable source dataset.
            if event.timestamp - self.dataset.bars[i].timestamp > 1000:
                raise ValueError(
                    "Funding is over 1s from a candle open; this replay cannot order it safely"
                )
            if event.mark_price is None:
                raise ValueError(
                    "Funding mark price is missing; research cannot assume zero funding"
                )
            self.funding[i] += event.rate * event.mark_price

    def run(
        self,
        strategy: StrategyDefinition,
        config: SearchConfig,
        first=1,
        last=None,
        mask=None,
        finalize=True,
    ):
        last = len(self.dataset.bars) if last is None else last
        if strategy.timeframe != self.dataset.request.timeframe:
            raise ValueError("Strategy timeframe must match the candle dataset")
        if not 1 <= first < last <= len(self.dataset.bars):
            raise ValueError("Invalid replay bounds")
        if config.costs.require_funding and self.dataset.funding_status != "queried":
            raise ValueError("Funding history is required by this experiment")
        risk, safety, costs = strategy.risk, config.safety, config.costs
        if risk.position_size > safety.max_position_size or risk.leverage > safety.max_leverage:
            raise ValueError("Strategy exceeds the operator position/leverage ceiling")
        entries, exits = self.features.signals(strategy)
        if mask is not None:
            entries = entries & mask
        # The period starts flat: no entry from the preceding split or embargo.
        entries = entries.copy()
        entries[:first] = False
        curve, trades, exposure, halted, fees, funding, quantity, entry_price = replay(
            self.features.ohlcv,
            self.features.timestamps,
            entries,
            exits,
            self.funding,
            first,
            last,
            config.capital,
            1 if strategy.direction == "long" else -1,
            risk.stop_loss,
            risk.take_profit,
            risk.trailing_stop,
            risk.position_size,
            risk.leverage,
            risk.cooldown,
            risk.max_hold,
            strategy.order_type == "limit",
            strategy.limit_offset,
            costs.fee,
            costs.slippage + costs.spread / 2,
            safety.max_daily_loss,
            safety.max_drawdown,
            safety.max_consecutive_losses,
            safety.kill_switch,
            finalize,
        )
        measured = metrics(
            curve, trades, config.capital, INTERVALS[strategy.timeframe], exposure, halted
        )
        measured.update(fees_paid=fees, funding_paid=funding)
        return {
            "metrics": measured,
            "equity": curve,
            "trades": trades,
            "first": first,
            "last": last,
            "open_position": {
                "quantity": quantity,
                "entry": entry_price,
                "direction": strategy.direction,
            }
            if quantity
            else None,
        }


def score(result, strategy, config):
    m, w = result["metrics"], config.weights
    if m["trades"] < config.min_trades or m["halted"]:
        return -1000.0 - abs(m["net_profit"]) - strategy.complexity
    pf = m["profit_factor"] if m["profit_factor"] is not None else (3 if m["gross_profit"] else 0)
    return (
        w.sharpe * max(-10, min(10, m["sharpe"]))
        - w.drawdown * m["max_drawdown"]
        + w.profit_factor * min(3, pf)
        + w.net_return * m["total_return"]
        - w.complexity * strategy.complexity
    )
