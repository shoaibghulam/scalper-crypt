"""Bots and their positions, written to disk so a restart does not lose them.

Without this the engine forgot every position it had opened the moment it
restarted, and reconciliation then classified its own live trades as positions
nobody could explain. That is the correct response to an unexplained position
and the wrong description of the situation: the engine had opened them minutes
earlier, and the record simply did not outlive the process.

Two properties matter more than the format.

Every monetary value crosses as a decimal string. Round-tripping a price
through a float loses the last place, and a price that no longer sits on the
tick grid is rejected by the exchange after the entry has already filled.

A file that cannot be read is treated as no file. Refusing to start because the
state file is malformed would leave live positions unmanaged, which is a worse
failure than starting without the record and reporting the positions as
unexplained. One unreadable bot is skipped rather than discarding the rest.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from scalper.engine.bots import (
    Bot,
    BotRegistry,
    ManagedPosition,
    normalise_margin_mode,
)
from scalper.engine.trades import ClosedTrade, TradeHistory
from scalper.envfiles import state_file

log = logging.getLogger("scalper.engine.store")

#: Bumped when a field changes meaning rather than when one is added. A reader
#: that does not recognise the version starts empty instead of guessing.
SCHEMA_VERSION = 1


def _decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not a number: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} is not finite: {value!r}")
    return parsed


def _position_to_json(position: ManagedPosition) -> dict[str, Any]:
    return {
        "symbol": position.symbol,
        "side": position.side,
        "quantity": f"{position.quantity:f}",
        "entry": f"{position.entry:f}",
        "stop": f"{position.stop:f}",
        "target": f"{position.target:f}",
        "leverage": position.leverage,
        "opened_at": position.opened_at,
        "stop_armed": position.stop_armed,
        "target_armed": position.target_armed,
        "entry_order_id": position.entry_order_id,
        "stop_client_id": position.stop_client_id,
        "target_client_id": position.target_client_id,
    }


def _position_from_json(raw: dict[str, Any]) -> ManagedPosition:
    return ManagedPosition(
        symbol=str(raw["symbol"]),
        side=str(raw["side"]),
        quantity=_decimal(raw["quantity"], "quantity"),
        entry=_decimal(raw["entry"], "entry"),
        stop=_decimal(raw["stop"], "stop"),
        target=_decimal(raw["target"], "target"),
        leverage=int(raw["leverage"]),
        opened_at=float(raw["opened_at"]),
        stop_armed=bool(raw.get("stop_armed", False)),
        target_armed=bool(raw.get("target_armed", False)),
        entry_order_id=raw.get("entry_order_id"),
        stop_client_id=raw.get("stop_client_id"),
        target_client_id=raw.get("target_client_id"),
        # Deliberately not restored. The mark price on disk is as old as the
        # file, and the refresh loop replaces it within seconds. Restoring a
        # stale one would show a profit figure from before the restart.
        mark=None,
    )


def _bot_to_json(bot: Bot) -> dict[str, Any]:
    return {
        "id": bot.id,
        "name": bot.name,
        "strategy": bot.strategy,
        "symbols": list(bot.symbols),
        "amount": f"{bot.amount:f}",
        "leverage": bot.leverage,
        "margin_mode": bot.margin_mode,
        "state": bot.state,
        "realised": f"{bot.realised:f}",
        "tp_hits": bot.tp_hits,
        "sl_hits": bot.sl_hits,
        "signal_seq": bot.signal_seq,
        "entry_pending": bot.entry_pending,
        "continuous": bot.continuous,
        "resume_after_settlement": bot.resume_after_settlement,
        "entry_intent": bot.entry_intent,
        "last_signal": bot.last_signal,
        "error": bot.error,
        "positions": [_position_to_json(p) for p in bot.positions.values()],
        "pending_closures": [_position_to_json(p) for p in bot.pending_closures.values()],
    }


def _bot_from_json(raw: dict[str, Any]) -> Bot:
    bot = Bot(
        # The id is restored rather than regenerated. Client order ids derive
        # from it, and a fresh id would let a retry past the exchange duplicate
        # guard that those ids exist to trigger.
        id=str(raw["id"]),
        name=str(raw["name"]),
        strategy=str(raw["strategy"]),
        symbols=[str(s) for s in raw["symbols"]],
        amount=_decimal(raw["amount"], "amount"),
        leverage=int(raw["leverage"]),
        # Normalised on the way back in too: a hand-edited file must not be
        # able to put an unrecognised mode in front of the exchange.
        margin_mode=normalise_margin_mode(raw.get("margin_mode")),
        state=str(raw.get("state", "running")),
        tp_hits=int(raw.get("tp_hits", 0)),
        sl_hits=int(raw.get("sl_hits", 0)),
        realised=_decimal(raw.get("realised", "0"), "realised"),
        signal_seq=int(raw.get("signal_seq", 0)),
        entry_pending=raw.get("entry_pending") is True,
        continuous=raw.get("continuous") is True,
        resume_after_settlement=raw.get("resume_after_settlement") is True,
        entry_intent=raw.get("entry_intent"),
        last_signal=raw.get("last_signal", {}),
        error=raw.get("error"),
    )
    for entry in raw.get("positions", []):
        position = _position_from_json(entry)
        bot.positions[position.symbol] = position
    for entry in raw.get("pending_closures", []):
        position = _position_from_json(entry)
        bot.pending_closures[position.symbol] = position
    return bot


def _trade_to_json(trade: ClosedTrade) -> dict[str, Any]:
    return {
        "symbol": trade.symbol,
        "bot": trade.bot,
        "strategy": trade.strategy,
        "side": trade.side,
        "quantity": f"{trade.quantity:f}",
        "entry": f"{trade.entry:f}",
        "exit_price": f"{trade.exit_price:f}",
        "leverage": trade.leverage,
        "realised": f"{trade.realised:f}",
        "fees": f"{trade.fees:f}",
        "outcome": trade.outcome,
        "opened_at": trade.opened_at,
        "closed_at": trade.closed_at,
        "verified": trade.verified,
    }


def _trade_from_json(raw: dict[str, Any]) -> ClosedTrade:
    return ClosedTrade(
        symbol=str(raw["symbol"]),
        bot=str(raw["bot"]),
        strategy=str(raw["strategy"]),
        side=str(raw["side"]),
        quantity=_decimal(raw["quantity"], "quantity"),
        entry=_decimal(raw["entry"], "entry"),
        exit_price=_decimal(raw["exit_price"], "exit_price"),
        leverage=int(raw["leverage"]),
        realised=_decimal(raw["realised"], "realised"),
        fees=_decimal(raw["fees"], "fees"),
        outcome=raw.get("outcome"),
        opened_at=float(raw["opened_at"]),
        closed_at=float(raw["closed_at"]),
        verified=raw.get("verified") is True,
    )


class StateStore:
    """Reads and writes the engine record at one path."""

    def __init__(self, path: Path | str | None = None, *, environment: str | None = None) -> None:
        # Resolved at construction rather than at import, so an override
        # set by a test or a second account is picked up.
        self.path = Path(path) if path is not None else state_file()
        if path is None and environment and not os.environ.get("SCALPER_STATE_FILE"):
            self.path = self.path.with_name(f"engine-state.{environment}.json")
        control = self._read().get("control", {})
        if not isinstance(control, dict):
            raise ValueError("invalid saved control state")
        self.control: dict[str, Any] = control

    def load(self) -> BotRegistry:
        """The saved registry, or an empty one if there is nothing usable.

        Never raises. A missing, truncated or hand-edited file leaves the
        engine in the state it would have had on a first run, and the live
        positions then surface through reconciliation as unexplained, which is
        visible rather than silent.
        """
        registry = BotRegistry()
        if not self.path.exists():
            return registry

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.exception(
                "could not read %s; starting with no saved bots. Any open "
                "position will be reported as unexplained.",
                self.path,
            )
            return registry

        if not isinstance(payload, dict) or not isinstance(payload.get("bots", []), list):
            log.error("invalid state structure in %s; reconciliation required", self.path)
            return registry
        version = payload.get("version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION:
            log.warning(
                "ignoring %s: schema version %s, this build reads %s",
                self.path,
                version,
                SCHEMA_VERSION,
            )
            return registry

        for raw in payload.get("bots", []):
            try:
                bot = _bot_from_json(raw)
            except (KeyError, ValueError, TypeError):
                # One bad record must not cost the good ones.
                log.exception("skipping an unreadable bot record in %s", self.path)
                continue
            registry.bots[bot.id] = bot

        log.info(
            "restored %d bot(s) holding %d position(s) from %s",
            len(registry.bots),
            sum(len(b.positions) for b in registry.bots.values()),
            self.path,
        )
        return registry

    def _read(self) -> dict[str, Any]:
        """The file as it stands, or an empty document.

        Never raises: a missing or unreadable file is the same as no file, for
        the same reason load() treats it that way.
        """
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def load_history(self) -> TradeHistory:
        """Closed trades from the last run.

        Marked persistent even when empty. The flag describes where history is
        kept, not whether any has accumulated, and the dashboard renders a
        warning from it.
        """
        history = TradeHistory()
        history.persistent = True

        payload = self._read()
        if payload.get("version", SCHEMA_VERSION) != SCHEMA_VERSION:
            return history

        entries = payload.get("history", [])
        if not isinstance(entries, list):
            return history
        for raw in entries:
            try:
                history.record(_trade_from_json(raw))
            except (KeyError, ValueError, TypeError):
                # One unreadable trade must not cost the rest of the record.
                log.exception("skipping an unreadable trade in %s", self.path)
                continue
        return history

    def save(self, registry: BotRegistry, history: TradeHistory | None = None) -> bool:
        """Write the registry and the trade history, atomically.

        History defaults to None meaning "leave what is already on disk",
        rather than "write nothing". A later caller that saved only the
        registry would otherwise wipe the trade record silently, and that is
        exactly the kind of footgun this file exists to avoid.

        Written to a temporary file in the same directory and renamed over the
        target, because a rename within a filesystem is atomic while a write is
        not. A crash part way through leaves the previous file intact rather
        than a half-written one that would fail to parse on the next start.
        """
        trades = (
            [_trade_to_json(t) for t in reversed(history.recent())]
            if history is not None
            else self._read().get("history", [])
        )
        payload = {
            "control": self.control,
            "version": SCHEMA_VERSION,
            "bots": [_bot_to_json(bot) for bot in registry.bots.values()],
            "history": trades,
        }

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # mkstemp rather than a context-managed temporary file: this one
            # has to outlive its handle so it can be renamed into place.
            fd, temp_name = tempfile.mkstemp(
                dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=1, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, self.path)
                if history is not None:
                    from scalper.engine.experiment_db import save_snapshot
                    experiment = self.control.get("strategy_experiment", {})
                    fingerprint = hashlib.sha256(
                        json.dumps([trades, experiment], sort_keys=True).encode()
                    ).hexdigest()
                    if getattr(self, "_experiment_fingerprint", None) != fingerprint:
                        save_snapshot(self.path.with_suffix(".sqlite"), trades, experiment)
                        self._experiment_fingerprint = fingerprint
                    history.persistent = True
            except BaseException:
                # Leaving a stray temporary file behind would accumulate one
                # per failed write for the life of the installation.
                Path(temp_name).unlink(missing_ok=True)
                raise
        except (OSError, sqlite3.Error):
            # Losing the record is recoverable; taking the engine down while
            # positions are open is not. The next save attempt may succeed.
            log.exception("could not persist engine state to %s", self.path)
            if history is not None:
                history.persistent = False
            return False
        return True
