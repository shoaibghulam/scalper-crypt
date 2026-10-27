"""Closed trades, and what they add up to.

The summary deliberately leads with profit factor and expectancy rather than
win rate. Win rate on its own is the most misleading number in this domain: a
cost-aware study of 78 mean-reversion backtests found fourteen configurations
that won 65 percent or more of their trades and still lost money, one of them
returning minus 12.5 percent at a 68 percent win rate.

Fees are reported separately for the same reason. At this holding period they
are a large fraction of the edge, and a gross figure makes a losing strategy
look viable.

History is persisted with engine state. Legacy estimated records remain visible
but are excluded from verified performance statistics.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

DEFAULT_LIMIT = None


@dataclass(frozen=True)
class ClosedTrade:
    symbol: str
    bot: str
    strategy: str
    side: str
    quantity: Decimal
    entry: Decimal
    exit_price: Decimal
    leverage: int
    realised: Decimal
    fees: Decimal
    outcome: str | None
    opened_at: float
    closed_at: float
    verified: bool = True

    @property
    def duration_seconds(self) -> float:
        return self.closed_at - self.opened_at

    @property
    def is_win(self) -> bool:
        """Net of fees. A scratch is not a victory."""
        return self.realised > 0

    @property
    def notional(self) -> Decimal:
        return self.entry * self.quantity

    @property
    def roe_pct(self) -> float:
        margin = self.notional / Decimal(self.leverage or 1)
        return float(self.realised / margin * 100) if margin else 0.0

    def describe(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bot": self.bot,
            "strategy": self.strategy,
            "side": self.side,
            "quantity": float(self.quantity),
            "entry": float(self.entry),
            "exit_price": float(self.exit_price),
            "leverage": self.leverage,
            "realised": float(self.realised),
            "fees": float(self.fees),
            "outcome": self.outcome,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
            "verified": self.verified,
            "duration_seconds": self.duration_seconds,
            "is_win": self.is_win,
            "roe_pct": self.roe_pct,
        }


class TradeHistory:
    """Recent closed trades, newest first."""

    def __init__(self, limit: int | None = DEFAULT_LIMIT) -> None:
        self._trades: deque[ClosedTrade] = deque(maxlen=limit)
        #: Set by the store when this history was loaded from, or will be
        #: written to, disk. The dashboard carries a warning while it is false,
        #: and that warning must disappear once it stops being true.
        self.persistent = False

    def record(self, trade: ClosedTrade) -> None:
        self._trades.append(trade)

    def recent(self, count: int | None = None) -> list[ClosedTrade]:
        ordered = sorted(self._trades, key=lambda t: t.closed_at, reverse=True)
        return ordered if count is None else ordered[:count]

    def summary(self) -> dict[str, Any]:
        trades = [t for t in self._trades if t.verified]
        if not trades:
            return {
                "trades": 0,
                "win_rate": None,
                "profit_factor": None,
                "expectancy": None,
                "net": 0.0,
                "fees": 0.0,
                "tp_hits": 0,
                "sl_hits": 0,
                "wins": 0,
                "losses": 0,
            }

        wins = [t for t in trades if t.is_win]
        losses = [t for t in trades if not t.is_win]

        gross_win = sum((t.realised for t in wins), Decimal(0))
        gross_loss = -sum((t.realised for t in losses), Decimal(0))
        net = sum((t.realised for t in trades), Decimal(0))

        # Undefined rather than infinite when nothing has lost yet. Reporting
        # infinity reads as a perfect strategy, and four winning trades is not
        # evidence of one.
        profit_factor = float(gross_win / gross_loss) if gross_loss > 0 else None

        return {
            "trades": len(trades),
            "win_rate": len(wins) / len(trades) * 100,
            "profit_factor": profit_factor,
            "expectancy": float(net / len(trades)),
            "net": float(net),
            "fees": float(sum((t.fees for t in trades), Decimal(0))),
            "tp_hits": sum(1 for t in trades if t.outcome == "tp"),
            "sl_hits": sum(1 for t in trades if t.outcome == "sl"),
            "wins": len(wins),
            "losses": len(losses),
        }

    def today_totals(self) -> dict[str, Any]:
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        trades = [t for t in self._trades if t.verified and t.closed_at >= start]
        return {
            "realised_today": f"{sum((t.realised for t in trades), Decimal(0)):.2f}",
            "tp_hits_today": sum(t.outcome == "tp" for t in trades),
            "sl_hits_today": sum(t.outcome == "sl" for t in trades),
        }

    def describe(self, count: int = 100) -> dict[str, Any]:
        return {
            "trades": [t.describe() for t in self.recent(count)],
            "summary": self.summary(),
            # Written to the engine state file alongside the bot registry, so
            # the counters on the Overview tiles and the trades on this tab now
            # come back from the same place after a restart. They used to
            # disagree: per-bot counters were saved and closed trades were not.
            "persistent": self.persistent,
        }
