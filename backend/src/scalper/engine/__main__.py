"""Entrypoint for the trading process.

Run with: python -m scalper.engine

This process owns every piece of account-scoped state: the rate-limit budget,
the user data stream, the reconciler and all open positions. Exactly one may
run per exchange account, which is enforced three ways: it is not inside the
web process, it takes an exclusive lease before doing anything, and every order
it sends carries a deterministic client order id that Binance itself will
reject on a duplicate.

It starts with trading DISABLED and fails closed. Nothing may open a position
until reconciliation against the exchange has returned clean.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import signal
import sys
import time
from contextlib import suppress
from decimal import Decimal

import httpx
from pydantic import ValidationError

from scalper.config import Environment, ExchangeConfig
from scalper.core.filters import SymbolFilters
from scalper.engine.account import (
    AccountBalance,
    open_positions,
    positions_and_marks,
    read_balance,
    read_leverage,
)
from scalper.engine.adopt import (
    AdoptionRefused,
    adopt_position,
)
from scalper.engine.bots import Bot, ManagedPosition
from scalper.engine.executor import OrderExecutor, UnprotectedPosition
from scalper.engine.experiments import configure as configure_experiment
from scalper.engine.experiments import disabled as disabled_strategies
from scalper.engine.experiments import enforce as enforce_experiments
from scalper.engine.experiments import report as experiment_report
from scalper.engine.lock import EngineLock, EngineLockHeld
from scalper.engine.monitor import diff_positions
from scalper.engine.operations import intent_plan, persist, prepare_entry, recover_entry
from scalper.engine.plan import EntryAborted, PlanRejected, TradePlan
from scalper.engine.publisher import SnapshotPublisher
from scalper.engine.reconciler import ExchangePosition, KnownPosition, Reconciliation, reconcile
from scalper.engine.screening import screen_watchlist, screening_deadline
from scalper.engine.settlement import settle
from scalper.engine.store import StateStore
from scalper.engine.trader import open_for_bot
from scalper.exchange.client import BinanceFuturesClient
from scalper.exchange.credentials import CredentialStatus, verify
from scalper.exchange.errors import BinanceError
from scalper.exchange.market_data import MarketDataClient
from scalper.exchange.market_limits import banned_for, blocked_for, describe
from scalper.settings import AppSettings
from scalper.strategies import DEFAULT_STRATEGY_ID, STRATEGIES

log = logging.getLogger("scalper.engine")

HEARTBEAT_SECONDS = 1.0

#: How often the engine re-reads the account. Fast enough that a closed
#: position does not linger on screen, slow enough to be trivial against
#: the request budget: one weight-5 call every few seconds.
POSITION_REFRESH_SECONDS = 4.0


class Engine:
    """Supervisor for the trading tasks.

    Phase 0 scope: prove the process boundary and the safety posture. The
    reconciler, market data, user data stream and bot tasks attach here in
    later phases.
    """

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.safe_mode = True  # Fail closed. Cleared only by a clean reconcile.
        self._refresh_failed = False
        self.running = False
        self.ticks = 0
        self._last_signal_cycle = -1
        self.credentials = CredentialStatus.missing()
        self.reconciliation: Reconciliation | None = None
        self.store = StateStore(environment=settings.env.value)
        # Restored, not created. Starting blank is what turned this
        # engine's own open positions into unexplained ones on restart.
        self.registry = self.store.load()
        # Restored for the same reason the registry is. The Overview counters
        # and this history describe the same events, and keeping one across a
        # restart while discarding the other made them disagree on screen.
        self.history = self.store.load_history()
        self.balance = AccountBalance.unknown()
        # The outcome of the last operator command, for the dashboard. A
        # refusal is a result and has to travel back the same way an
        # acceptance would; logging it and returning silence is what made the
        # Adopt button look broken.
        self.last_action: dict[str, object] | None = None
        self._stop = asyncio.Event()
        self._account_lock = asyncio.Lock()

    def should_reverify(self) -> bool:
        """Whether another credential check is worth making.

        Only when the last failure was transient - the exchange unreachable,
        or this address rate limited. A single ConnectTimeout during the
        startup window used to leave the engine with unusable credentials for
        the rest of its life: the refresh loop stops, the balance reads zero,
        and nothing trades, while the keys were fine the whole time.

        A rejected key is deliberately excluded. Retrying it cannot help and
        risks the key being rate-limited.

        While the address is under a known ban this waits. The refresh loop
        runs every few seconds and a signed request bypasses the public pacing
        on purpose - that exists so a protective stop always goes out, not so
        a health check can knock three hundred times into a twenty-minute ban
        and have the exchange extend it.
        """
        if not self.credentials.retryable:
            return False
        try:
            rest = ExchangeConfig().rest
        except Exception:
            # Cannot tell which origin this is, so do not invent a reason to
            # stay dark: retrying is the behaviour that was already correct.
            return True
        return banned_for(rest) <= 0

    def _rate_limit_pause(self) -> float:
        """Seconds to wait before touching signed endpoints again.

        Logged once per pause rather than once per attempt: at a four second
        cadence an hour-long ban would otherwise produce nine hundred
        identical lines and bury everything else.
        """
        try:
            remaining = banned_for(ExchangeConfig().rest)
        except Exception:
            return 0.0
        if remaining <= 0:
            self._rate_limit_logged = False
            return 0.0
        if not getattr(self, "_rate_limit_logged", False):
            self._rate_limit_logged = True
            log.warning(
                "exchange is rate limiting this address; pausing account reads for %s. "
                "Trading stays disabled until it clears and reconciliation succeeds.",
                describe(remaining),
            )
        return remaining

    def _check_account(self, config: ExchangeConfig) -> None:
        recorded = self.store.control.get("account_fingerprint")
        if recorded is not None and recorded != hashlib.sha256(config.api_key.encode()).hexdigest():
            self.safe_mode = True
            raise ValueError("account key differs from saved state; use a separate state file")

    async def verify_credentials(self) -> None:
        """Check the credentials and publish the result.

        Done eagerly at startup rather than on first use: a key that was never
        valid should surface while the operator is looking at the screen, not
        at the moment a signal fires. Also called from the refresh loop when
        the last attempt failed for a reason worth retrying.
        """
        try:
            config = ExchangeConfig()
        except ValidationError:
            # Absent rather than wrong. The distinction matters: telling
            # somebody to add keys they have already added sends them to
            # re-enter a value that was never the problem.
            self.credentials = CredentialStatus.missing()
            log.warning("%s", self.credentials.message)
            return

        fingerprint = hashlib.sha256(config.api_key.encode()).hexdigest()
        recorded = self.store.control.get("account_fingerprint")
        if recorded is not None and recorded != fingerprint:
            self.credentials = CredentialStatus.rejected(
                "configured key",
                self.settings.env.value,
                "saved state belongs to a different API key; use a separate state file",
            )
            self.safe_mode = True
            return
        was_usable = self.credentials.valid
        self.credentials = await verify(config)
        if self.credentials.valid:
            self.store.control["account_fingerprint"] = fingerprint
            if not self.store.save(self.registry, self.history):
                self.safe_mode = True
                return
            log.info(
                "credentials accepted for %s (%s), wallet %s USDT",
                self.credentials.environment,
                self.credentials.key_preview,
                self.credentials.balance,
            )
            if not was_usable:
                # Recovered from a failure. Reconciliation is skipped while
                # credentials are unusable, so without this the engine would
                # sit in safe mode with working keys.
                log.info("credentials recovered; reconciling")
                await self.reconcile_account()
        else:
            log.warning("%s", self.credentials.message)

    def request_stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict[str, object]:
        """State for the dashboard.

        Built here rather than read from the database so the dashboard sees
        what the engine currently believes, including safe mode, rather than
        the last thing that happened to be persisted.
        """
        return {
            "mode": self.settings.env.value,
            "max_leverage": self.settings.max_leverage,
            "max_positions": self.settings.max_positions,
            "auto_trade": self.store.control.get("auto_trade", {"enabled": False}),
            "single_coin": self.store.control.get("single_coin", {"enabled": False}),
            "safe_mode": self.safe_mode,
            "running": self.running,
            "ticks": self.ticks,
            "credentials": self.credentials.describe(),
            "history": self.history.describe(),
            "strategy_monitor": experiment_report(self.history, self.store.control),
            "last_action": self.last_action,
            # Explicit rather than inferred from an alert id: the form
            # needs to know before it submits whether the engine will
            # accept anything at all.
            "trading_enabled": not self.safe_mode
            and not self.store.control.get("halted", False)
            and self.credentials.can_trade is True,
            "halted": bool(self.store.control.get("halted")),
            "reconciliation": (self.reconciliation.describe() if self.reconciliation else None),
            "bots": self.registry.describe(),
            "positions": self.registry.positions(),
            # Positions on the account this engine does not manage - a second
            # bot, or a hand-placed trade. Reported separately from `positions`
            # so they can be SEEN without ever being counted as ours.
            "external_positions": (
                self.reconciliation.external_positions() if self.reconciliation else []
            ),
            "alerts": self._alerts(),
            "totals": {
                **self.registry.totals(),
                **self.balance.describe(),
                **self.history.today_totals(),
            },
        }

    def note_action(self, kind: str, *, ok: bool, detail: str) -> None:
        """Record what an operator command did, for the next snapshot.

        Carries a timestamp so the dashboard can retire a stale result, and so
        two identical messages in a row are distinguishable from one that
        never updated.
        """
        self.last_action = {
            "kind": kind,
            "ok": ok,
            "detail": detail,
            "at": time.time(),
        }
        (log.info if ok else log.error)("%s: %s", kind, detail)

    async def handle_command(self, command: dict[str, object]) -> None:
        # These commands only stop future entries; they never cancel an exchange
        # operation. Process immediately while a read-only scan holds the lock.
        if command.get("type") in {"stop_auto_trade", "stop_single_coin", "stop_bot"}:
            await self._execute_command(command)
        else:
            async with self._account_lock:
                await self._execute_command(command)

    async def _execute_command(self, command: dict[str, object]) -> None:
        try:
            if (
                "issued_at" in command
                and not 0 <= time.time() - float(str(command["issued_at"])) <= 30
            ):
                self.note_action(
                    str(command.get("type")),
                    ok=False,
                    detail="command expired; submit a fresh request",
                )
                return
            await self._handle_command(command)
        except Exception as exc:
            self.safe_mode = True
            self.note_action(str(command.get("type", "unknown")), ok=False, detail=str(exc))
            log.exception("command failed; new entries disabled")
        finally:
            if not self.store.save(self.registry, self.history):
                self.safe_mode = True

    async def _handle_command(self, command: dict[str, object]) -> None:
        """Execute one operator command.

        Every command runs here, in the process that owns the account. The web
        process only relays; it cannot place an order even if it wanted to,
        which is what makes its worker count irrelevant.
        """
        kind = command.get("type")
        if kind == "panic":
            await self._panic()
        elif kind == "resume":
            self.store.control["halted"] = True
            self.reconciliation = None
            await self.reconcile_account()
            if (
                self.reconciliation is None
                or not self.reconciliation.is_clean
                or self.reconciliation.resized
                or any(b.entry_pending or b.pending_closures for b in self.registry.bots.values())
                or any(
                    not p.stop_armed
                    for b in self.registry.bots.values()
                    for p in b.positions.values()
                )
            ):
                self.note_action("resume", ok=False, detail="account recovery is not complete")
                return
            self.store.control["halted"] = False
            persist(self.store, self.registry, self.history)
            await self.reconcile_account()
            self.note_action(
                "resume",
                ok=not self.safe_mode,
                detail="reconciliation completed; stopped bots remain stopped",
            )
        elif kind == "create_bot":
            await self._create_bot(command)
        elif kind in {"start_auto_trade", "stop_auto_trade"}:
            await self._configure_auto_trade(command)
        elif kind in {"start_single_coin", "stop_single_coin"}:
            await self._configure_single_coin(command)
        elif kind == "strategy_experiment":
            configure_experiment(command, self.store.control)
            enforce_experiments(self.history, self.store.control, self.registry)
            self.note_action("strategy_experiment", ok=True,
                             detail="Settings saved. Stopped bots stay stopped until restarted.")
        elif kind == "stop_bot":
            bot_id = str(command.get("bot_id", ""))
            stopped = self.registry.stop(bot_id)
            self.note_action(
                "stop_bot",
                ok=stopped is not None,
                detail=(
                    f"stopped {stopped.name}"
                    if stopped is not None
                    else f"no bot with id {bot_id!r}"
                ),
            )
        elif kind == "adopt_orphans":
            await self._adopt_orphans(command)
        elif kind == "dismiss_bots":
            from scalper.engine.commands import DismissBotsRequest

            request = DismissBotsRequest.model_validate(command)
            dismissed_ids = {key for key in set(request.bot_ids) if self.registry.remove(key)}
            auto = self.store.control.get("auto_trade", {})
            if auto:
                auto["bot_ids"] = [
                    key for key in auto.get("bot_ids", []) if key not in dismissed_ids
                ]
                if not auto["bot_ids"]:
                    auto["enabled"] = False
            single = self.store.control.get("single_coin", {})
            if single.get("bot_id") in dismissed_ids:
                single["enabled"] = False
            self.note_action(
                "dismiss_bots", ok=True,
                detail=f"Dismissed {len(dismissed_ids)} bots; "
                f"kept {len(set(request.bot_ids)) - len(dismissed_ids)} bots with pending state "
                "or unavailable records. "
                "Trade history preserved.",
            )
        elif kind == "remove_bot":
            bot_id = str(command.get("bot_id", ""))
            bot = self.registry.bots.get(bot_id)
            removed = self.registry.remove(bot_id)
            self.note_action(
                "remove_bot",
                ok=removed,
                detail=(
                    f"removed {bot.name}"
                    if removed and bot is not None
                    else (
                        f"{bot.name} still holds {len(bot.positions)} position(s); "
                        "removing it would leave them unexplained"
                        if bot is not None
                        else f"no bot with id {bot_id!r}"
                    )
                ),
            )
        else:
            log.warning("ignoring unknown command: %s", kind)
            return
        # Written after the command rather than inside each branch, so a new
        # command cannot be added that mutates the registry without saving.
        self.store.save(self.registry, self.history)

    async def _adopt_orphans(self, command: dict[str, object]) -> None:
        """Take responsibility for positions the exchange holds and we do not.

        The usual cause is this engine having opened them before a restart that
        predates persistence, or the operator having opened them by hand on
        Binance. Either way they are real money and the alternative to adopting
        them is leaving them unmanaged behind a permanent warning banner.

        The protective levels are read back from the exchange rather than
        invented. A bot that adopted a position and then attached a stop of its
        own choosing would move a level the operator had already set.
        """
        if self.reconciliation is None or not self.reconciliation.orphans:
            self.note_action("adopt", ok=False, detail="nothing to adopt: there are no orphans")
            return

        raw_symbols = command.get("symbols", [])
        if not isinstance(raw_symbols, list) or not all(isinstance(s, str) for s in raw_symbols):
            raise ValueError("symbols must be a list of strings")
        wanted = {str(s).upper() for s in raw_symbols}
        orphans = [
            o for o in self.reconciliation.orphans if not wanted or o.symbol.upper() in wanted
        ]
        if not orphans:
            self.note_action("adopt", ok=False, detail="none of the requested symbols are orphaned")
            return

        # Explicit, never implied. Attaching a stop places a real order, so a
        # plain adopt still refuses an unprotected position and says so.
        protect = command.get("protect", False)
        if not isinstance(protect, bool):
            raise ValueError("protect must be a boolean")

        config = ExchangeConfig()
        self._check_account(config)
        adopted: list[ManagedPosition] = []
        refusals: list[str] = []
        async with (
            BinanceFuturesClient(config) as client,
            MarketDataClient(config.rest) as market,
        ):
            levels = await read_leverage(client)
            filters = await market.symbol_filters() if protect else {}
            executor = OrderExecutor(client)
            for orphan in orphans:
                try:
                    if protect:
                        await self._protect_orphan(client, market, executor, filters, orphan)
                    position = await adopt_position(client, orphan, levels.get(orphan.symbol, 1))
                except AdoptionRefused as exc:
                    # Named explicitly rather than swallowed: a position the
                    # engine cannot manage must keep saying so, and the reason
                    # has to reach the screen rather than only the log.
                    refusals.append(f"{orphan.symbol}: {exc}")
                    continue
                except Exception as exc:
                    log.exception("could not adopt %s", orphan.symbol)
                    refusals.append(f"{orphan.symbol}: {exc}")
                    continue
                adopted.append(position)

        if not adopted:
            self.note_action(
                "adopt",
                ok=False,
                detail="; ".join(refusals) or "adopted nothing; the warning stands",
            )
            return

        bot = self.registry.create(
            symbols=[p.symbol for p in adopted],
            amount=Decimal(str(command.get("amount", "0"))),
            leverage=max(p.leverage for p in adopted),
            strategy=str(command.get("strategy", DEFAULT_STRATEGY_ID)),
            name=str(command.get("name") or "Adopted positions"),
        )
        for position in adopted:
            bot.positions[position.symbol] = position

        adopted_names = ", ".join(p.symbol for p in adopted)
        self.note_action(
            "adopt",
            ok=not refusals,
            detail=(
                f"adopted {adopted_names} into {bot.name}"
                + (f"; refused {'; '.join(refusals)}" if refusals else "")
            ),
        )
        # Re-run rather than assuming the result. Anything that failed to adopt
        # is still an orphan and must keep blocking.
        await self.reconcile_account()

    async def _protect_orphan(
        self,
        client: BinanceFuturesClient,
        market: MarketDataClient,
        executor: OrderExecutor,
        filters: dict[str, SymbolFilters],
        orphan: ExchangePosition,
    ) -> None:
        """Attach only a missing stop, then let adoption verify it on exchange."""
        from uuid import uuid4

        from scalper.core.filters import Rounding, round_to_tick
        from scalper.engine.adopt import closing_orders, needs_protection
        from scalper.indicators.core import atr

        current = next((p for p in await open_positions(client) if p.symbol == orphan.symbol), None)
        if current is None or current.quantity != orphan.quantity:
            raise AdoptionRefused("position changed; refresh before protecting")
        orders = closing_orders(await client.list_algo_orders(orphan.symbol), current)
        if not needs_protection(orders):
            return
        symbol_filter = filters.get(orphan.symbol)
        if symbol_filter is None or current.mark_price is None:
            raise AdoptionRefused("current mark or symbol filters unavailable")
        candles = await market.candles(orphan.symbol, "1m", limit=100)
        if len(candles) < 15 or time.time() * 1000 - candles[-1].close_time > 90_000:
            raise AdoptionRefused("fresh volatility history unavailable")
        volatility = atr(
            [c.high for c in candles], [c.low for c in candles], [c.close for c in candles], 14
        )
        if volatility is None or volatility <= 0:
            raise AdoptionRefused("volatility unavailable")
        reference = Decimal(str(current.mark_price))
        distance = max(Decimal(str(volatility)) * Decimal("1.5"), reference * Decimal("0.0045"))
        long = current.quantity > 0
        stop = round_to_tick(
            reference - distance if long else reference + distance,
            symbol_filter.tick_size,
            Rounding.DOWN if long else Rounding.UP,
        )
        if stop <= 0:
            raise AdoptionRefused("stop would not be a positive price")
        await client.place_algo_order(
            symbol=current.symbol,
            side="SELL" if long else "BUY",
            order_type="STOP_MARKET",
            trigger_price=stop,
            close_position=True,
            client_algo_id=f"protect-{uuid4().hex[:24]}",
        )

    async def _recover_entries(self, client: BinanceFuturesClient) -> bool:
        had_pending = any(b.entry_pending for b in self.registry.bots.values())
        for bot in self.registry.bots.values():
            if not bot.entry_pending:
                continue
            self.safe_mode = True
            if bot.entry_intent is None:
                continue
            try:
                if await recover_entry(client, bot):
                    bot.error = None
                    single = self.store.control.get("single_coin", {})
                    if single.get("bot_id") == bot.id and (bot.positions or bot.pending_closures):
                        single["enabled"] = False
                        bot.continuous = False
                persist(self.store, self.registry, self.history)
            except Exception as exc:
                bot.error = f"entry recovery pending: {exc}"
                log.warning("entry recovery pending for %s", bot.id)

        return had_pending and not any(b.entry_pending for b in self.registry.bots.values())

    async def _panic(self) -> None:
        """Halt new entries durably, then flatten only engine-owned exposure."""
        self.safe_mode = True
        self.store.control["halted"] = True
        single = self.store.control.get("single_coin")
        if single:
            self.store.control["single_coin"] = {**single, "enabled": False}
        auto = self.store.control.get("auto_trade")
        if auto:
            self.store.control["auto_trade"] = {**auto, "enabled": False}
        for bot in self.registry.bots.values():
            bot.state = "stopped"
        saved = self.store.save(self.registry, self.history)
        failures = [] if saved else ["halt could not be persisted; do not restart"]
        config = ExchangeConfig()
        self._check_account(config)
        async with BinanceFuturesClient(config) as client:
            for bot in self.registry.bots.values():
                if bot.entry_pending and bot.entry_intent:
                    plan = intent_plan(bot)
                    try:
                        await client.cancel_order(plan.symbol, plan.client_order_id("entry"))
                    except Exception as exc:
                        if getattr(exc, "code", None) not in {-2011, -2013}:
                            failures.append(f"{plan.symbol}: entry cancellation unverified")
            live = await open_positions(client)
            owned = {p.symbol for b in self.registry.bots.values() for p in b.positions.values()}
            owned.update(
                intent_plan(b).symbol for b in self.registry.bots.values() if b.entry_intent
            )
            for position in live:
                if position.symbol not in owned:
                    continue
                try:
                    from uuid import uuid4

                    await client.place_order(
                        symbol=position.symbol,
                        side="SELL" if position.quantity > 0 else "BUY",
                        order_type="MARKET",
                        quantity=abs(Decimal(str(position.quantity))),
                        reduce_only=True,
                        client_order_id=f"panic-{uuid4().hex[:24]}",
                    )
                except Exception:
                    failures.append(f"{position.symbol}: close outcome unverified")
            remaining = {p.symbol for p in await open_positions(client)} & owned
            failures.extend(f"{symbol}: still open" for symbol in sorted(remaining))
        await self._refresh_positions()
        self.note_action(
            "panic",
            ok=not failures,
            detail="New entries halted. "
            + (
                "; ".join(failures)
                if failures
                else "Managed positions verified flat; trading remains halted."
            ),
        )

    async def _configure_single_coin(self, command: dict[str, object]) -> None:
        from scalper.engine.commands import SingleCoinRequest
        from scalper.engine.single_coin import ranked_candidates, start_search

        previous = self.store.control.get("single_coin", {})
        if command["type"] == "stop_single_coin":
            self.registry.stop(previous.get("bot_id", ""))
            self.store.control["single_coin"] = {**previous, "enabled": False}
        else:
            try:
                request = SingleCoinRequest.model_validate(command)
                if self.settings.env is not Environment.TESTNET:
                    raise ValueError("Single Coin is currently testnet-only")
                if self.safe_mode or self.store.control.get("halted"):
                    raise ValueError("Resolve account alerts before starting")
                if not self.credentials.valid or self.credentials.can_trade is not True:
                    raise ValueError("Account does not permit trading")
                if request.leverage > self.settings.max_leverage:
                    raise ValueError(f"Leverage cap is {self.settings.max_leverage}x")
                config = ExchangeConfig()
                config.assert_armed()
                if config.env != self.settings.env:
                    raise ValueError("execution environment mismatch")
                self._check_account(config)
                ranking = ranked_candidates(
                    experiment_report(self.history, self.store.control)["rows"], request.timeframe,
                )
                if not ranking:
                    raise ValueError("No qualifying strategy yet: requires at least one verified closed trade, "
                                     "positive net profit, enabled status and no loss-rule flag")
                chosen = ranking[0]
                self.store.control["single_coin"] = {
                    **start_search(self.registry, previous, request, strategy=chosen["id"]),
                    "selection": {k: chosen[k] for k in ("name", "wins", "trades", "win_rate", "net")},
                }
            except ValueError as exc:
                self.note_action("single_coin", ok=False, detail=str(exc))
                return
        persist(self.store, self.registry, self.history)
        self.note_action("single_coin", ok=True, detail=(
            "Searching for one confirmed entry within your budget"
            if self.store.control["single_coin"].get("enabled")
            else "Search stopped; existing position remains managed"
        ))

    async def _configure_auto_trade(self, command: dict[str, object]) -> None:
        from scalper.engine.auto_trade import start_fleet, stop_fleet
        from scalper.engine.commands import AutoTradeRequest

        previous = self.store.control.get("auto_trade", {})
        if command["type"] == "stop_auto_trade":
            self.store.control["auto_trade"] = stop_fleet(self.registry, previous)
            persist(self.store, self.registry, self.history)
            self.note_action("auto_trade", ok=True,
                             detail="Auto Trade stopped; position protection remains active")
            return
        try:
            request = AutoTradeRequest.model_validate(command)
            if self.settings.env is not Environment.TESTNET:
                raise ValueError("Auto Trade is currently available on testnet only")
            if self.safe_mode or self.store.control.get("halted"):
                raise ValueError("resolve account alerts and resume trading first")
            if not self.credentials.valid or self.credentials.can_trade is not True:
                raise ValueError("account does not permit trading")
            if request.leverage > self.settings.max_leverage:
                raise ValueError(f"configured leverage cap is {self.settings.max_leverage}x")
            config = ExchangeConfig()
            if config.env != self.settings.env:
                raise ValueError("execution environment mismatch")
            config.assert_armed()
            self._check_account(config)
            self.store.control["auto_trade"] = start_fleet(
                self.registry, previous, request, set(disabled_strategies(self.store.control))
            )
        except ValueError as exc:
            self.note_action("auto_trade", ok=False, detail=str(exc))
            return
        persist(self.store, self.registry, self.history)
        self.note_action("auto_trade", ok=True,
                         detail=("Auto Trade updated: missing enabled strategies added; "
                                 "settings apply to future entries; stopped bots remain stopped"
                                 if previous.get("enabled") else
                                 "Auto Trade started: enabled strategies watch for confirmed entries"))

    async def _create_bot(self, command: dict[str, object]) -> None:
        from scalper.engine.commands import CreateBotRequest

        try:
            request = CreateBotRequest.model_validate({"leverage": 1, **command})
        except ValidationError as exc:
            self.note_action("create_bot", ok=False, detail=f"invalid request: {exc}")
            return
        if self.safe_mode or self.store.control.get("halted"):
            blocked = (
                ", ".join(sorted(self.reconciliation.blocked_symbols))
                if self.reconciliation is not None
                else ""
            )
            self.note_action(
                "create_bot",
                ok=False,
                detail=(
                    "refused: the engine is in safe mode"
                    + (
                        f" because the exchange holds {blocked}, which it cannot "
                        f"explain. Adopt or close it first."
                        if blocked
                        else " until reconciliation against the exchange is clean."
                    )
                ),
            )
            return

        symbols = request.symbols
        if not symbols:
            self.note_action("create_bot", ok=False, detail="refused: no symbols were selected")
            return

        amount = request.amount
        leverage = request.leverage
        if leverage > self.settings.max_leverage:
            self.note_action(
                "create_bot", ok=False,
                detail=f"refused: configured leverage cap is {self.settings.max_leverage}x",
            )
            return
        strategy_id = request.strategy
        if strategy_id in disabled_strategies(self.store.control):
            self.note_action("create_bot", ok=False, detail="Strategy disabled in Strategy Monitor")
            return
        if strategy_id not in STRATEGIES.ids():
            self.note_action(
                "create_bot",
                ok=False,
                detail=f"refused: unknown strategy {strategy_id!r}",
            )
            return

        if (STRATEGIES.get(strategy_id).testnet_only
                and self.settings.env is not Environment.TESTNET):
            self.note_action("create_bot", ok=False, detail="This strategy is testnet-only")
            return

        occupied = {
            symbol
            for b in self.registry.bots.values()
            for symbol in (*b.positions, *b.pending_closures)
        }
        if occupied.intersection(symbols):
            self.note_action(
                "create_bot",
                ok=False,
                detail=f"already managed: {', '.join(sorted(occupied.intersection(symbols)))}",
            )
            return
        config = ExchangeConfig()
        if config.env != self.settings.env or config.env is Environment.PAPER:
            self.note_action(
                "create_bot",
                ok=False,
                detail="environment mismatch or paper execution is not implemented",
            )
            return
        config.assert_armed()
        if not self.credentials.valid or self.credentials.can_trade is not True:
            self.note_action("create_bot", ok=False, detail="account does not permit trading")
            return

        bot = self.registry.create(
            symbols=symbols,
            amount=amount,
            leverage=leverage,
            strategy=strategy_id,
            name=request.name,
            margin_mode=request.margin_mode,
        )
        bot.continuous = request.continuous
        if not self.store.save(self.registry, self.history):
            self.safe_mode = True
            raise RuntimeError("state persistence failed; no entry sent")

        if bot.continuous:
            self.note_action(
                "create_bot", ok=True,
                detail=f"{bot.name}: watching {', '.join(symbols)} for fresh confirmed signals",
            )
            return
        await self._open_bot(bot)

    async def _open_bot(self, bot: Bot, symbols: list[str] | None = None) -> None:
        if bot.state == "stopped":
            return
        if bot.strategy in disabled_strategies(self.store.control):
            self.registry.stop(bot.id)
            return
        symbols = bot.symbols if symbols is None else symbols
        config = ExchangeConfig()
        if config.env != self.settings.env or config.env is Environment.PAPER:
            raise ValueError("execution environment changed or unsupported")
        config.assert_armed()
        recorded = self.store.control.get("account_fingerprint")
        fingerprint = hashlib.sha256(config.api_key.encode()).hexdigest()
        if recorded is not None and recorded != fingerprint:
            raise ValueError("account credentials changed; restart and reconcile")
        opened: list[str] = []
        failures: list[str] = []
        bot.error = None

        async with (
            BinanceFuturesClient(config) as client,
            MarketDataClient(config.rest) as market,
        ):
            filters = await market.symbol_filters()
            executor = OrderExecutor(client)
            strategy = STRATEGIES.get(bot.strategy)
            prefetch = getattr(market, "prefetch_candles", None)
            if prefetch is not None:
                await prefetch(symbols, max(200, strategy.history_5m + 1),
                               max(strategy.candle_limit_1m, strategy.history_1m + 1))
            for symbol in symbols:
                if self.safe_mode or bot.state == "stopped":
                    break
                bot.last_checked_at = time.time()
                bot.symbol_notes[symbol] = "Checking fresh signal and account limits"
                if symbol not in filters:
                    bot.symbol_notes[symbol] = "Not a tradeable USDT perpetual"
                    failures.append(f"{symbol}: not a tradeable USDT perpetual")
                    continue
                try:

                    async def prepare(plan: TradePlan, opened_at: float) -> None:
                        await prepare_entry(
                            client,
                            bot,
                            plan,
                            opened_at,
                            self.settings,
                            self.store,
                            self.registry,
                            self.history,
                        )

                    single = self.store.control.get("single_coin", {})
                    is_single = single.get("bot_id") == bot.id
                    if is_single:
                        await open_for_bot(bot, symbol, filters[symbol], market, executor, prepare,
                                           budget=bot.amount, settings=self.settings)
                    else:
                        await open_for_bot(bot, symbol, filters[symbol], market, executor, prepare)
                    bot.entry_pending, bot.entry_intent = False, None
                    opened.append(symbol)
                    bot.symbol_notes[symbol] = "Position opened with protection"
                    if is_single:
                        single["enabled"] = False
                        bot.continuous = False
                        self.note_action("single_coin", ok=True,
                                         detail=f"Opened {symbol}; one-trade search completed")
                        break
                except PlanRejected as exc:
                    bot.entry_pending, bot.entry_intent = False, None
                    bot.symbol_notes[symbol] = str(exc)
                    if isinstance(exc, EntryAborted) and bot.continuous:
                        bot.resume_after_settlement = True
                        single = self.store.control.get("single_coin", {})
                        if single.get("bot_id") == bot.id:
                            single["enabled"] = False
                            bot.continuous = False
                            bot.resume_after_settlement = False
                    failures.append(str(exc))
                    if not bot.continuous:
                        bot.state = "error"
                        bot.error = str(exc)
                    if symbol in bot.positions:
                        bot.error = str(exc)
                        bot.state = "error"
                        self.safe_mode = True
                        break
                except UnprotectedPosition as exc:
                    # The position was closed rather than left exposed. The bot
                    # is marked so the operator sees it rather than reading logs.
                    bot.state = "error"
                    bot.error = str(exc)
                    bot.symbol_notes[symbol] = str(exc)
                    failures.append(str(exc))
                    log.error("%s", exc)
                    self.safe_mode = True
                    break
                except Exception as exc:
                    # A transport failure before a durable entry intent is safe
                    # to retry on a later candle. Unknown order outcomes arrive
                    # as UnprotectedPosition above and must never be retried.
                    retryable = (
                        (isinstance(exc, (httpx.TransportError, TimeoutError))
                         or (isinstance(exc, BinanceError) and exc.code == -1003)
                         or (isinstance(exc, httpx.HTTPStatusError)
                             and exc.response.status_code in {418, 429, 408, 500, 502, 503, 504}))
                        and not bot.entry_pending
                        and bot.entry_intent is None
                        and symbol not in bot.positions
                    )
                    bot.error = f"{symbol}: {type(exc).__name__}"
                    if isinstance(exc, BinanceError):
                        bot.error = (f"{symbol}: Binance error {exc.code}"
                                     + ("; rate limit reached" if exc.code == -1003 else ""))
                    if isinstance(exc, httpx.HTTPStatusError):
                        # Never include signed query strings, headers or response bodies.
                        bot.error = (f"{symbol}: Binance HTTP {exc.response.status_code} "
                                     f"on {exc.request.url.path}")
                    bot.symbol_notes[symbol] = bot.error
                    failures.append(bot.error)
                    if retryable and bot.continuous:
                        bot.symbol_notes[symbol] += "; retrying on the next signal cycle"
                        log.warning("temporary data failure for %s: %s", symbol, type(exc).__name__)
                        if ((isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {418, 429})
                                or (isinstance(exc, BinanceError) and exc.code == -1003)):
                            bot.error += "; market-data cooldown; retrying automatically"
                            break
                        continue
                    bot.state = "error"
                    log.exception("could not open %s", symbol)
                    if symbol in bot.positions or bot.entry_pending:
                        self.safe_mode = True
                    break
                finally:
                    if not self.store.save(self.registry, self.history):
                        self.safe_mode = True
                if self.safe_mode:
                    break

        if not opened and not bot.continuous:
            bot.state = "error"
            bot.error = "; ".join(failures) or "no entry was opened"

        if bot.continuous and not opened and bot.error is None:
            # Ordinary waiting is reported per symbol, not as a failed command.
            return
        if opened and self.store.control.get("single_coin", {}).get("bot_id") == bot.id:
            return

        if opened and bot.error is None and not self.safe_mode:
            self.note_action(
                "create_bot", ok=True,
                detail=f"{bot.name}: opened {', '.join(opened)}"
                + (f"; {len(failures)} candidates skipped. See symbol status for reasons."
                   if failures else ""),
            )
            return

        self.note_action(
            "create_bot",
            ok=False,
            detail=(
                f"{bot.name}: opened {', '.join(opened)}"
                if opened
                else f"{bot.name}: opened nothing"
            )
            + (f"; {'; '.join(failures)}" if failures else ""),
        )

    async def _signal_cycle(self) -> None:
        if self.safe_mode or self.store.control.get("halted"):
            return
        async with self._account_lock:
            from scalper.engine.auto_trade import recover_legacy_market_errors

            enforce_experiments(self.history, self.store.control, self.registry)
            recover_legacy_market_errors(
                self.registry, self.store.control.get("auto_trade", {}),
                set(disabled_strategies(self.store.control)),
            )
            if not self.store.save(self.registry, self.history):
                self.safe_mode = True
                return
            due: list[Bot] = []
            # Slow event collection must not starve candle strategies' entry windows.
            for bot in sorted(self.registry.bots.values(),
                              key=lambda b: b.strategy == "order-flow-pullback"):
                if self.safe_mode:
                    break
                if (
                    bot.continuous
                    and bot.state == "running"
                    and not bot.positions
                    and not bot.pending_closures
                    and not bot.entry_pending
                ):
                    if bot.leverage > self.settings.max_leverage:
                        bot.error = (
                            f"Saved leverage {bot.leverage}x exceeds configured cap "
                            f"{self.settings.max_leverage}x; reduce bot leverage"
                        )
                        bot.symbol_notes = dict.fromkeys(bot.symbols, bot.error)
                        continue
                    strategy = (STRATEGIES.get(bot.strategy)
                                if bot.strategy in STRATEGIES.ids() else None)
                    interval = strategy.signal_interval_s if strategy else 60
                    if int(time.time()) % interval >= 60:
                        bot.error = None
                        for symbol in bot.symbols:
                            bot.symbol_notes.setdefault(
                                symbol, f"Waiting for the next closed {interval // 60}m candle"
                            )
                        continue
                    due.append(bot)
            known = [bot for bot in due if bot.strategy in STRATEGIES.ids()]
            candidates: dict[str, list[str]] = {}
            if known:
                try:
                    config = ExchangeConfig()
                    if config.env != self.settings.env:
                        raise ValueError("screening environment mismatch")
                    async with MarketDataClient(config.rest) as market:
                        # Read-only work may time out; order submission is never
                        # cancelled by this deadline.
                        # Scaled to the watchlist: a flat deadline aborted
                        # every pass once the fleet watched the whole exchange.
                        watched = len({s for bot in known for s in bot.symbols})
                        async with asyncio.timeout(screening_deadline(watched)):
                            auto = self.store.control.get("auto_trade", {})
                            discovery = (
                                set(auto.get("bot_ids", [])) if auto.get("enabled") else set()
                            )
                            single = self.store.control.get("single_coin", {})
                            if single.get("enabled"):
                                discovery.add(single["bot_id"])
                            if discovery:
                                candidates = await screen_watchlist(
                                    known, market, discovery,
                                    coverage=(auto.setdefault("coverage", {})
                                              if auto.get("enabled") else None),
                                )
                            else:
                                candidates = await screen_watchlist(known, market)
                except (httpx.HTTPError, TimeoutError) as exc:
                    for bot in known:
                        bot.error = f"Market data unavailable: {type(exc).__name__}; retrying"
                        bot.symbol_notes = dict.fromkeys(bot.symbols, bot.error)
                    return
                except Exception:
                    self.safe_mode = True
                    log.exception("watchlist screening failed; entries disabled")
                    return
            # Rotate first access to shared capital so fixed registry order does
            # not permanently favor the same strategy during testnet experiments.
            offset = int(time.time() // 60) % len(due) if due else 0
            ordered = sorted(due[offset:] + due[:offset],
                             key=lambda b: b.strategy == "order-flow-pullback")
            refreshed_at = float("-inf")
            for bot in ordered:
                if (any(b.positions for b in self.registry.bots.values())
                        and time.monotonic() - refreshed_at >= POSITION_REFRESH_SECONDS):
                    await self._refresh_positions()
                    if not self.store.save(self.registry, self.history):
                        self.safe_mode = True
                    refreshed_at = time.monotonic()
                if self.safe_mode or self.store.control.get("halted"):
                    break
                try:
                    if bot.id in candidates:
                        if not candidates[bot.id]:
                            continue
                        await self._open_bot(bot, candidates[bot.id])
                    else:
                        await self._open_bot(bot)
                except (httpx.HTTPError, TimeoutError) as exc:
                    if bot.entry_pending or bot.positions:
                        self.safe_mode = True
                    bot.error = f"Market/account data unavailable: {type(exc).__name__}"
                    bot.symbol_notes = dict.fromkeys(bot.symbols, bot.error)
                except Exception:
                    self.safe_mode = True
                    log.exception("signal cycle failed; entries disabled")

    async def refresh_positions(self) -> None:
        async with self._account_lock:
            await self._refresh_positions()
            if not self.store.save(self.registry, self.history):
                self.safe_mode = True

    async def _refresh_positions(self) -> None:
        """Re-read the account and make our record match it.

        Reconciling once at startup is not enough. A position closes by
        take-profit, by stop, or by a hand on the exchange, and nothing tells
        the engine unless it asks. Without asking, the dashboard keeps showing
        a position that no longer exists at a profit frozen since it opened,
        which is indistinguishable from live data and is acted on as if it were.
        """
        # Only the credential check short-circuits. The balance is account
        # state rather than position state, and gating it on a bot existing
        # showed an equity of zero on a funded account, which is the kind of
        # figure an operator reads once and sizes a trade against.
        if not self.credentials.valid:
            self.safe_mode = True
            # A transient failure is recoverable, so try again rather than
            # staying dark forever. reconcile_account runs on success, which
            # is what lifts safe mode.
            if self.should_reverify():
                await self.verify_credentials()
            return

        # A rate-limited address must be left alone. This loop runs every few
        # seconds, and every attempt while banned is itself another request
        # that Binance counts - the ban deadline was observed moving from
        # 08:20 to 09:16 purely because the engine kept knocking. Backing off
        # is what lets it expire, which is what lets trading resume.
        if self._rate_limit_pause():
            self.safe_mode = True
            self._refresh_failed = True
            return

        try:
            config = ExchangeConfig()
            self._check_account(config)
            async with BinanceFuturesClient(config) as client:
                recovered = await self._recover_entries(client)
                live, marks = await positions_and_marks(client)
                known = [
                    KnownPosition(
                        p.symbol, float(p.quantity if p.side == "LONG" else -p.quantity), b.id
                    )
                    for b in self.registry.bots.values()
                    for p in b.positions.values()
                ]
                self.reconciliation = reconcile(live, known)
                if not self.reconciliation.is_clean or self.reconciliation.resized:
                    self.safe_mode = True
                    self._refresh_failed = True
                # Capital first: it is the denominator for every other figure
                # on the dashboard, and it costs one request already in flight.
                refreshed_balance = await read_balance(client)
                if not refreshed_balance.known:
                    self.safe_mode = True
                    self._refresh_failed = True
                else:
                    self.balance = refreshed_balance
                # Re-read rather than trusted from open time: leverage can be
                # changed on the exchange while a position is open.
                levels = await read_leverage(client)

                # The venue knows where liquidation is. Our own formula treats
                # every position as isolated and ignores the rest of the
                # wallet, which is conservative but not true.
                liquidations = {
                    p.symbol: p.liquidation_price for p in live if p.liquidation_price is not None
                }

                for bot in self.registry.bots.values():
                    if not bot.positions:
                        continue
                    result = diff_positions(bot.positions, live, marks)

                    for symbol, mark in result.marks.items():
                        bot.positions[symbol].mark = Decimal(str(mark))
                        if symbol in liquidations:
                            bot.positions[symbol].liquidation = Decimal(str(liquidations[symbol]))
                        if symbol in levels:
                            bot.positions[symbol].leverage = levels[symbol]

                    for symbol, position in result.still_open.items():
                        actual = next(p for p in live if p.symbol == symbol)
                        if (actual.quantity > 0) != (position.side == "LONG"):
                            position.stop_armed = False
                            bot.error = (
                                f"{symbol}: exchange direction changed; "
                                "manual reconciliation required"
                            )
                            self.safe_mode = True
                            continue
                        position.quantity = abs(Decimal(str(actual.quantity)))
                        position.entry = Decimal(str(actual.entry_price))
                        try:
                            verified = await adopt_position(client, actual, position.leverage)
                            position.stop, position.target = verified.stop, verified.target
                            position.stop_armed, position.target_armed = True, verified.target_armed
                        except Exception:
                            position.stop_armed = False
                            position.target_armed = False
                            self.safe_mode = True
                            self._refresh_failed = True

                    for update in result.closed:
                        closing = bot.positions.pop(update.symbol, None)
                        if closing is not None:
                            bot.pending_closures[update.symbol] = closing
                            self.store.save(self.registry, self.history)

                for bot in self.registry.bots.values():
                    for symbol, closing in list(bot.pending_closures.items()):
                        # Cancel only recorded protective orders. A symbol-wide
                        # sweep could remove an operator's unrelated orders.
                        cleanup_ok = True
                        for order_id in (closing.stop_client_id, closing.target_client_id):
                            if not order_id:
                                continue
                            try:
                                await client.cancel_algo_order(order_id)
                            except Exception as exc:
                                if getattr(exc, "code", None) not in {-2011, -2013}:
                                    cleanup_ok = False
                                    log.warning("protective-order cleanup pending for %s", symbol)
                        try:
                            trade = await settle(client, bot, closing)
                        except Exception:
                            log.warning("fill settlement pending for %s", symbol, exc_info=True)
                            continue
                        if trade is None or not cleanup_ok:
                            continue
                        self.history.record(trade)
                        bot.realised += trade.realised
                        bot.tp_hits += int(trade.outcome == "tp")
                        bot.sl_hits += int(trade.outcome == "sl")
                        bot.pending_closures.pop(symbol)
                        self.store.save(self.registry, self.history)
                    # An aborted entry may settle while another position in this bot
                    # remains open. Clear only that verified exit's stale warning.
                    if bot.error and ": entry aborted after fill:" in bot.error:
                        aborted_symbol = bot.error.split(":", 1)[0]
                        if (
                            aborted_symbol not in bot.positions
                            and aborted_symbol not in bot.pending_closures
                            and not bot.entry_pending
                            and any(t.verified and t.symbol == aborted_symbol
                                    and t.bot == bot.name for t in self.history.recent())
                        ):
                            bot.error = None
                            bot.symbol_notes[aborted_symbol] = "Exit verified; entry abort settled"
                    if (
                        bot.resume_after_settlement and bot.continuous and bot.state == "error"
                        and all(p.stop_armed for p in bot.positions.values())
                        and not bot.pending_closures
                        and not bot.entry_pending and self.reconciliation.is_clean
                        and not self.reconciliation.resized
                        and not self.store.control.get("halted")
                    ):
                        bot.state = "running"
                        bot.error = None
                        bot.resume_after_settlement = False
                        recovered = True
                        self._refresh_failed = True
                if (
                    (recovered or self._refresh_failed)
                    and refreshed_balance.known
                    and self.reconciliation.is_clean
                    and not self.reconciliation.resized
                    and not self.store.control.get("halted")
                    and self.credentials.can_trade is True
                    and self.settings.env is not Environment.PAPER
                    and not any(
                        b.entry_pending or b.pending_closures for b in self.registry.bots.values()
                    )
                    and all(
                        p.stop_armed
                        for b in self.registry.bots.values()
                        for p in b.positions.values()
                    )
                    and self.store.save(self.registry, self.history)
                ):
                    self._refresh_failed = False
                    self.safe_mode = False
        except Exception:
            self.safe_mode = True
            self._refresh_failed = True
            # Logged rather than swallowed. A refresh that keeps failing means
            # the dashboard is drifting from the account, which is exactly the
            # condition this method exists to prevent.
            log.exception("could not refresh positions from the exchange")

    async def reconcile_account(self) -> None:
        """Compare our record against the exchange, then decide whether to trade.

        Nothing may open a position until this returns clean. The engine has no
        persisted positions yet, so every open position on the account shows up
        as an orphan, which is the correct and deliberately conservative
        answer: a position nobody can explain is not something to trade around.
        """
        self.safe_mode = True
        if not self.credentials.valid:
            log.warning("skipping reconciliation: credentials are not usable")
            return

        try:
            config = ExchangeConfig()
            self._check_account(config)
            async with BinanceFuturesClient(config) as client:
                await self._recover_entries(client)
                live = await open_positions(client)
                for bot in self.registry.bots.values():
                    for symbol, position in bot.positions.items():
                        actual = next((p for p in live if p.symbol == symbol), None)
                        position.stop_armed = False
                        if actual is not None:
                            verified = await adopt_position(client, actual, position.leverage)
                            position.stop_armed = True
                            position.target_armed = verified.target_armed
                            position.stop, position.target = verified.stop, verified.target
        except Exception:
            self._refresh_failed = True
            log.exception("reconciliation could not read the account")
            return

        # The restored record, so a position this engine opened before a
        # restart is adopted rather than reported as belonging to nobody.
        known = [
            KnownPosition(
                symbol=position.symbol,
                quantity=float(
                    position.quantity if position.side == "LONG" else -position.quantity
                ),
                bot_id=bot.id,
            )
            for bot in self.registry.bots.values()
            for position in bot.positions.values()
        ]
        self.reconciliation = reconcile(live, known)
        report = self.reconciliation.describe()

        if (
            self.reconciliation.is_clean
            and not self.reconciliation.resized
            and not any(b.entry_pending for b in self.registry.bots.values())
            and not self.store.control.get("halted")
            and self.credentials.can_trade is True
            and self.settings.env is not Environment.PAPER
        ):
            self.safe_mode = False
            log.info("reconciliation clean: %s; trading enabled", report)
        else:
            self._refresh_failed = True
            log.warning(
                "reconciliation found %d position(s) this engine cannot explain: "
                "%s. Trading stays disabled. Leave them alone and decide "
                "whether to adopt or close them.",
                report["orphaned"],
                report["blocked_symbols"],
            )

    def _alerts(self) -> list[dict[str, object]]:
        """What is actually stopping this engine from trading, in order."""
        alerts: list[dict[str, object]] = []
        if any(b.entry_pending for b in self.registry.bots.values()):
            alerts.append(
                {
                    "id": "entry-pending",
                    "severity": "critical",
                    "message": "An entry has an uncertain outcome. New trading is disabled; "
                    "reconcile exchange orders before clearing its saved entry intent.",
                }
            )
        if not self.credentials.valid:
            alerts.append(
                {
                    "id": "credentials",
                    "severity": "warning",
                    "message": self.credentials.message,
                }
            )
        if self.reconciliation is not None and not self.reconciliation.is_clean:
            symbols = ", ".join(sorted(self.reconciliation.blocked_symbols))
            alerts.append(
                {
                    "id": "orphans",
                    "severity": "critical",
                    "message": (
                        f"The exchange holds positions this engine has no record "
                        f"of: {symbols}. They have been left untouched. Adopt "
                        f"them to bring them under management, or close them on "
                        f"the exchange. Trading stays disabled until then."
                    ),
                    # Carried as data so the dashboard can offer the action on
                    # them. Parsing them back out of the sentence would break
                    # the first time the sentence was reworded.
                    "symbols": sorted(self.reconciliation.blocked_symbols),
                }
            )
        elif self.safe_mode:
            pause = self._rate_limit_pause()
            alerts.append(
                {
                    "id": "safe-mode",
                    "severity": "warning" if pause or self._refresh_failed else "info",
                    "message": (
                        f"Binance rate limit: account reads paused for {describe(pause)}. "
                        "Balance may be unavailable or stale. Automatic retry follows the cooldown."
                        if pause else
                        "Account refresh failed. Showing the last known account values; "
                        "trading remains disabled until a successful refresh."
                        if self._refresh_failed else
                        "Trading is disabled until reconciliation against the exchange completes."
                    ),
                }
            )
        pending = sorted({s for b in self.registry.bots.values() for s in b.pending_closures})
        if pending:
            alerts.append(
                {
                    "id": "settlement",
                    "severity": "warning",
                    "message": "Closed positions awaiting verified fills or order cleanup: "
                    + ", ".join(pending)
                    + ". They are excluded from P&L statistics.",
                }
            )
        unprotected = sorted(
            {
                p.symbol
                for b in self.registry.bots.values()
                for p in b.positions.values()
                if not p.stop_armed
            }
        )
        if unprotected:
            alerts.append(
                {
                    "id": "protection",
                    "severity": "critical",
                    "message": "Protective stop not verified: " + ", ".join(unprotected),
                }
            )
        return alerts

    async def run(self) -> None:
        await self.verify_credentials()
        await self.reconcile_account()
        self.running = True
        # The parenthetical used to say trading was disabled unconditionally,
        # which contradicted the safe_mode=False it printed beside it. Now that
        # a restored record can clear reconciliation on startup, that line was
        # wrong on exactly the run where it mattered most.
        log.info(
            "engine started mode=%s safe_mode=%s (%s)",
            self.settings.env.value,
            self.safe_mode,
            "trading disabled until reconciliation is clean"
            if self.safe_mode
            else "reconciliation clean, trading enabled",
        )
        if self.settings.env is Environment.LIVE:
            log.warning("LIVE MODE: orders placed by this process use real money")

        try:
            last_refresh = float("-inf")
            while not self._stop.is_set():
                self.ticks += 1

                if time.monotonic() - last_refresh >= POSITION_REFRESH_SECONDS:
                    await self.refresh_positions()
                    last_refresh = time.monotonic()

                minute = int(time.time() // 60)
                if minute != self._last_signal_cycle and time.time() % 60 >= 2:
                    self._last_signal_cycle = minute
                    await self._signal_cycle()

                with suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=HEARTBEAT_SECONDS)
        finally:
            self.running = False
            log.info("engine stopped after %d ticks", self.ticks)


async def _run(settings: AppSettings) -> None:
    engine = Engine(settings)
    loop = asyncio.get_running_loop()

    for signame in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, signame, None)
        if sig is None:
            continue
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, engine.request_stop)

    publisher = SnapshotPublisher(
        settings.engine_host,
        settings.engine_port,
        engine.snapshot,
        on_command=engine.handle_command,
    )
    # The publisher is a daemon of the engine, not the other way round: if it
    # fails, trading continues and only the dashboard goes dark.
    publishing = asyncio.create_task(publisher.serve_forever())

    try:
        await engine.run()
    finally:
        publishing.cancel()
        with suppress(asyncio.CancelledError):
            await publishing


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    settings = AppSettings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    try:
        with EngineLock(settings.lock_path) as lock:
            if lock.previous_owner is not None:
                log.warning(
                    "took over a lock file left by pid %s at %s; the previous "
                    "engine did not shut down cleanly, so reconciliation "
                    "matters this run",
                    lock.previous_owner,
                    settings.lock_path,
                )
            try:
                asyncio.run(_run(settings))
            except KeyboardInterrupt:
                log.info("interrupted")
    except EngineLockHeld as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
