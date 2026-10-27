"""Bots, and the trades they hold.

A bot is one strategy configuration holding positions across several symbols.
That shape is what makes the fleet counts on the dashboard meaningful: running
versus stopped describes strategies, not individual trades.

Entry timing here is the operator. The scanner says which symbols are worth
trading and why; the operator decides when. That was the chosen model, and it
keeps a human on the entry decision while the bot handles the part humans do
badly: attaching protection immediately and never moving a stop against itself.

The immediacy has a price and the interface states it. An operator-initiated
entry crosses the spread, which roughly doubles the round-trip cost against a
resting post-only entry. Strategy-initiated entries, when those arrive, rest.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

log = logging.getLogger("scalper.engine.bots")

#: A label, not an identifier. Long enough to be descriptive, short enough that
#: the position table does not reflow around one bot.
MAX_BOT_NAME_LENGTH = 64

#: Binance spells cross margin CROSSED. Everybody types CROSS and every
#: interface labels it that way, so both are accepted and normalised rather
#: than letting the order be rejected over a spelling.
MARGIN_MODES = {
    "ISOLATED": "ISOLATED",
    "CROSS": "CROSSED",
    "CROSSED": "CROSSED",
}

#: The posture that risks less. An unrecognised value falls back here, because
#: guessing CROSSED would put the whole wallet behind a position on the
#: strength of a typo.
DEFAULT_MARGIN_MODE = "ISOLATED"


def normalise_margin_mode(value: str | None) -> str:
    """ISOLATED or CROSSED, whatever the caller typed."""
    return MARGIN_MODES.get((value or "").strip().upper(), DEFAULT_MARGIN_MODE)


def default_bot_name(strategy_id: str, symbols: list[str], index: int) -> str:
    """A name that says what the bot does, not what order it was created in.

    bot-1 and bot-2 told an operator nothing a week later. The strategy is the
    useful part, and the index only has to separate two bots running the same
    one. Imported lazily because the strategy registry imports this module.
    """
    try:
        from scalper.strategies import STRATEGIES

        strategy = STRATEGIES.get(strategy_id)
        label = strategy.name if strategy is not None else strategy_id
    except Exception:
        # A missing or broken registry must not stop a bot being created. The
        # id is a worse label than the display name and a fine fallback.
        label = strategy_id
    return f"{label} {index}"[:MAX_BOT_NAME_LENGTH]


@dataclass
class ManagedPosition:
    symbol: str
    side: str
    quantity: Decimal
    entry: Decimal
    stop: Decimal
    target: Decimal
    leverage: int
    opened_at: float
    stop_armed: bool = False
    mark: Decimal | None = None
    #: As reported by the exchange, not derived. None until the refresh loop
    #: has read it, and omitted from the payload rather than sent as zero.
    liquidation: Decimal | None = None
    target_armed: bool = False
    entry_order_id: int | None = None
    stop_client_id: str | None = None
    target_client_id: str | None = None

    def describe(self, bot_name: str, decimals: int = 4, bot_id: str = "") -> dict[str, Any]:
        mark = self.mark if self.mark is not None else self.entry
        direction = Decimal(1) if self.side == "LONG" else Decimal(-1)
        pnl = (mark - self.entry) * self.quantity * direction
        notional = self.quantity * self.entry
        margin = notional / Decimal(self.leverage or 1)
        return {
            # Keyed on the bot ID, not its name. The name is a label the
            # operator can change, and building the id from it meant a
            # rename silently changed the identity of every position the
            # bot held - so a client tracking them across a refresh would
            # see them vanish and come back as new rows.
            "id": f"{bot_id or bot_name}-{self.symbol}",
            "bot": bot_name,
            "symbol": self.symbol,
            "side": self.side,
            "leverage": self.leverage,
            "entry": float(self.entry),
            "mark": float(mark),
            "stop": float(self.stop),
            "target": float(self.target),
            "quantity": float(self.quantity),
            "notional": float(notional),
            "unrealised_pnl": float(pnl),
            "roe_pct": float(pnl / margin * 100) if margin else 0.0,
            "margin_ratio": 0.0,
            "opened_at": self.opened_at,
            "price_decimals": decimals,
            "stop_armed": self.stop_armed,
            "target_armed": self.target_armed,
            "liquidation": (float(self.liquidation) if self.liquidation is not None else None),
        }


@dataclass
class Bot:
    id: str
    name: str
    strategy: str
    symbols: list[str]
    amount: Decimal
    leverage: int
    #: ISOLATED caps a loss at this position's margin. CROSSED backs it with
    #: the whole wallet: liquidation sits further away, but what is at stake
    #: is the account rather than one position.
    margin_mode: str = DEFAULT_MARGIN_MODE
    state: str = "running"
    positions: dict[str, ManagedPosition] = field(default_factory=dict)
    pending_closures: dict[str, ManagedPosition] = field(default_factory=dict)
    continuous: bool = False
    resume_after_settlement: bool = False
    entry_pending: bool = False
    entry_intent: dict[str, Any] | None = None
    last_signal: dict[str, int] = field(default_factory=dict)
    tp_hits: int = 0
    sl_hits: int = 0
    realised: Decimal = Decimal(0)
    signal_seq: int = 0
    error: str | None = None
    symbol_notes: dict[str, str] = field(default_factory=dict)
    last_checked_at: float = 0.0

    def next_seq(self) -> int:
        """Monotonic per bot, so every client order id is unique and derived."""
        self.signal_seq += 1
        return self.signal_seq

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "strategy": self.strategy,
            "state": self.state,
            "open_positions": len(self.positions),
            "pnl_today": float(self.realised),
            "pending_closures": len(self.pending_closures),
            "entry_pending": self.entry_pending,
            "continuous": self.continuous,
            "tp_hits": self.tp_hits,
            "sl_hits": self.sl_hits,
            "symbols": self.symbols,
            "margin_mode": self.margin_mode,
            "error": self.error,
            "symbol_notes": self.symbol_notes,
            "last_checked_at": self.last_checked_at,
        }


class BotRegistry:
    """Every bot this engine is running."""

    def __init__(self) -> None:
        self.bots: dict[str, Bot] = {}

    def create(
        self,
        *,
        symbols: list[str],
        amount: Decimal,
        leverage: int,
        strategy: str,
        name: str | None = None,
        margin_mode: str | None = None,
    ) -> Bot:
        chosen = (name or "").strip()[:MAX_BOT_NAME_LENGTH]
        if not chosen:
            # Numbered within the strategy, so two breakout bots read as 1 and
            # 2 rather than as whichever position they hold in the whole fleet.
            same = sum(1 for b in self.bots.values() if b.strategy == strategy)
            chosen = default_bot_name(strategy, symbols, same + 1)
        name = chosen
        bot = Bot(
            id=uuid.uuid4().hex[:8],
            name=name,
            strategy=strategy,
            symbols=symbols,
            amount=amount,
            leverage=leverage,
            margin_mode=normalise_margin_mode(margin_mode),
        )
        self.bots[bot.id] = bot
        log.info(
            "created %s on %s with %s USDT at %sx",
            name,
            ", ".join(symbols),
            amount,
            leverage,
        )
        return bot

    def remove(self, bot_id: str) -> bool:
        """Forget a bot entirely. True when one was removed.

        Refused while the bot still holds a position: deleting the record
        would leave the exchange holding something nobody can explain, which
        is exactly the orphan state that disables trading on the next restart.

        Idempotent. The dashboard may ask twice, or ask about a bot that is
        already gone, and neither is an error worth raising.
        """
        bot = self.bots.get(bot_id)
        if bot is None:
            return False
        if bot.positions or bot.pending_closures or bot.entry_pending:
            log.warning(
                "refusing to remove %s: it still holds %d position(s)",
                bot.name,
                len(bot.positions),
            )
            return False
        del self.bots[bot_id]
        log.info("removed %s", bot.name)
        return True

    def stop(self, bot_id: str) -> Bot | None:
        bot = self.bots.get(bot_id)
        if bot is not None:
            bot.resume_after_settlement = False
            bot.state = "stopped"
        return bot

    def positions(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for bot in self.bots.values():
            out.extend(p.describe(bot.name, bot_id=bot.id) for p in bot.positions.values())
        # A stable order, by age. Sorting by profit would move rows under the
        # cursor of somebody reaching for a close button.
        return sorted(out, key=lambda p: p["opened_at"])

    def describe(self) -> list[dict[str, Any]]:
        return [bot.describe() for bot in self.bots.values()]

    def totals(self) -> dict[str, Any]:
        positions = self.positions()
        unrealised = sum(p["unrealised_pnl"] for p in positions)
        states = [bot.state for bot in self.bots.values()]
        return {
            "unrealised_pnl": f"{unrealised:.2f}",
            "realised_today": f"{sum((b.realised for b in self.bots.values()), Decimal(0)):.2f}",
            "open_positions": len(positions),
            "bots_running": states.count("running"),
            "bots_stopped": states.count("stopped"),
            "bots_error": states.count("error"),
            "tp_hits_today": sum(b.tp_hits for b in self.bots.values()),
            "sl_hits_today": sum(b.sl_hits for b in self.bots.values()),
        }


def now() -> float:
    return time.time()
