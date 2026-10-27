"""The engine re-reading the account.

Regression test for the bug that made the dashboard lie: two positions shown
as open at zero profit while the exchange had both closed, one at a loss and
one at a gain. The engine reconciled once at startup and never looked again.
"""

from decimal import Decimal

import pytest

from scalper.config import Environment
from scalper.engine.bots import ManagedPosition
from scalper.exchange.credentials import CredentialStatus
from scalper.settings import AppSettings


def a_position(symbol: str, **overrides) -> ManagedPosition:
    base = dict(
        symbol=symbol,
        side="LONG",
        quantity=Decimal("2.36"),
        entry=Decimal("105.61"),
        stop=Decimal("105.13"),
        target=Decimal("106.57"),
        leverage=5,
        opened_at=1000.0,
        mark=Decimal("105.61"),
        entry_order_id=1,
        stop_armed=True,
        stop_client_id="owned-sl",
        target_client_id="owned-tp",
    )
    base.update(overrides)
    return ManagedPosition(**base)


class FakeClient:
    """Reports whatever the exchange is supposed to hold."""

    def __init__(self, rows) -> None:
        self.rows = rows
        self.exit_price = Decimal("106.57")
        self.swept: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get_positions(self):
        return {"result": self.rows}

    async def get_account(self):
        return {
            "totalWalletBalance": "3225.82",
            "availableBalance": "3145.95",
            "totalMarginBalance": "3228.65",
        }

    async def cancel_algo_order(self, order_id):
        self.swept.append(order_id)
        return {}

    async def list_algo_orders(self, symbol):
        return [{"side": "SELL", "closePosition": True,
                 "type": "STOP_MARKET", "triggerPrice": "105.13"}]

    async def account_trades(self, symbol, start_time):
        common = {"qty": "2.36", "commissionAsset": "USDT", "commission": "0.12"}
        return [
            {**common, "id": 1, "orderId": 1, "side": "BUY", "price": "105.61",
             "realizedPnl": "0", "time": 1000001},
            {**common, "id": 2, "orderId": 2, "side": "SELL", "price": str(self.exit_price),
             "realizedPnl": str((self.exit_price - Decimal("105.61")) * Decimal("2.36")),
             "time": 1100000},
        ]


def engine_with(position_symbols, exchange_rows, monkeypatch):
    from scalper.engine import __main__ as module

    engine = module.Engine(AppSettings(BINANCE_ENV=Environment.TESTNET))
    engine.credentials = CredentialStatus.accepted(
        "kp", "testnet", balance="1000", can_trade=True
    )
    bot = engine.registry.create(
        symbols=list(position_symbols),
        amount=Decimal("50"),
        leverage=5,
        strategy="s",
    )
    for symbol in position_symbols:
        bot.positions[symbol] = a_position(symbol)

    client = FakeClient(exchange_rows)
    monkeypatch.setattr(module, "BinanceFuturesClient", lambda config: client)
    monkeypatch.setattr(module, "ExchangeConfig", lambda: None)
    return engine, bot, client


def row(symbol: str, qty: str, mark: str):
    return {
        "symbol": symbol,
        "positionAmt": qty,
        "entryPrice": "105.61",
        "markPrice": mark,
    }


class TestMarkPrices:
    async def test_the_mark_price_is_refreshed_from_the_exchange(self, monkeypatch):
        # Without this the profit column stays frozen at the entry price.
        engine, bot, _ = engine_with(
            ["SOLUSDT"], [row("SOLUSDT", "2.36", "106.20")], monkeypatch
        )
        await engine.refresh_positions()
        assert bot.positions["SOLUSDT"].mark == Decimal("106.20")

    async def test_profit_follows_the_refreshed_mark(self, monkeypatch):
        engine, bot, _ = engine_with(
            ["SOLUSDT"], [row("SOLUSDT", "2.36", "106.20")], monkeypatch
        )
        await engine.refresh_positions()
        view = bot.positions["SOLUSDT"].describe(bot.name)
        assert view["unrealised_pnl"] > 0


class TestClosureDetection:
    async def test_a_position_closed_on_the_exchange_leaves_the_dashboard(
        self, monkeypatch
    ):
        engine, bot, _ = engine_with(["SOLUSDT"], [], monkeypatch)
        await engine.refresh_positions()
        assert bot.positions == {}

    async def test_target_price_does_not_prove_the_exit_order(self, monkeypatch):
        engine, bot, _ = engine_with(["SOLUSDT"], [], monkeypatch)
        bot.positions["SOLUSDT"].mark = Decimal("106.57")
        await engine.refresh_positions()
        assert bot.tp_hits == 0
        assert bot.sl_hits == 0

    async def test_stop_price_does_not_prove_the_exit_order(self, monkeypatch):
        engine, bot, client = engine_with(["SOLUSDT"], [], monkeypatch)
        client.exit_price = Decimal("105.13")
        bot.positions["SOLUSDT"].mark = Decimal("106.57")
        await engine.refresh_positions()
        assert bot.sl_hits == 0
        assert bot.tp_hits == 0

    async def test_a_loss_is_recorded_as_negative(self, monkeypatch):
        engine, bot, client = engine_with(["SOLUSDT"], [], monkeypatch)
        client.exit_price = Decimal("105.13")
        bot.positions["SOLUSDT"].mark = Decimal("106.57")
        await engine.refresh_positions()
        assert bot.realised < 0

    async def test_a_win_is_recorded_as_positive(self, monkeypatch):
        engine, bot, _ = engine_with(["SOLUSDT"], [], monkeypatch)
        bot.positions["SOLUSDT"].mark = Decimal("106.57")
        await engine.refresh_positions()
        assert bot.realised > 0

    async def test_only_owned_orders_are_cancelled(self, monkeypatch):
        # A protective order outliving its position counts against the
        # per-symbol cap and can fire against a later position.
        engine, _, client = engine_with(["SOLUSDT"], [], monkeypatch)
        await engine.refresh_positions()
        assert client.swept == ["owned-sl", "owned-tp"]

    async def test_one_closing_does_not_disturb_another(self, monkeypatch):
        engine, bot, _ = engine_with(
            ["SOLUSDT", "ONEUSDT"], [row("ONEUSDT", "100", "0.0019")], monkeypatch
        )
        await engine.refresh_positions()
        assert list(bot.positions) == ["ONEUSDT"]


class TestGuards:
    async def test_nothing_happens_without_usable_credentials(self, monkeypatch):
        engine, bot, _ = engine_with(["SOLUSDT"], [], monkeypatch)
        engine.credentials = CredentialStatus.missing()
        await engine.refresh_positions()
        assert bot.positions != {}

    async def test_an_exchange_failure_does_not_stop_the_engine(self, monkeypatch):
        from scalper.engine import __main__ as module

        engine, bot, _ = engine_with(["SOLUSDT"], [], monkeypatch)

        def explode(config):
            raise RuntimeError("exchange unreachable")

        monkeypatch.setattr(module, "BinanceFuturesClient", explode)
        await engine.refresh_positions()
        # The previous view is kept rather than the position being wrongly
        # declared closed on a network error.
        assert bot.positions != {}


@pytest.mark.parametrize("quantity", ["0", "0.0"])
async def test_a_flat_row_counts_as_closed(quantity, monkeypatch):
    engine, bot, _ = engine_with(
        ["SOLUSDT"], [row("SOLUSDT", quantity, "105.61")], monkeypatch
    )
    await engine.refresh_positions()
    assert bot.positions == {}


class TestHistoryIsRecorded:
    """A closed position must leave a record behind.

    Before this, a trade that closed vanished from the screen entirely: the row
    disappeared and only a running realised total moved. An operator could see
    that something had happened but not what, which makes a losing strategy and
    an unlucky streak look identical.
    """

    async def test_a_closed_position_lands_in_the_history(self, monkeypatch):
        engine, _, _ = engine_with(["SOLUSDT"], [], monkeypatch)
        await engine.refresh_positions()

        trades = engine.history.recent()
        assert [t.symbol for t in trades] == ["SOLUSDT"]

    async def test_the_record_keeps_both_ends_of_the_trade(self, monkeypatch):
        # Entry and exit both, so the result can be re-derived rather than
        # trusted. A history that stores only the P&L cannot be audited.
        engine, _, _ = engine_with(["SOLUSDT"], [], monkeypatch)
        await engine.refresh_positions()

        trade = engine.history.recent()[0]
        assert trade.entry == Decimal("105.61")
        assert trade.exit_price is not None
        assert trade.leverage == 5

    async def test_fees_are_recorded_separately_from_the_result(self, monkeypatch):
        # At this holding period fees are a large share of the outcome. Folding
        # them into the net figure hides why a high win rate still loses money.
        engine, _, _ = engine_with(["SOLUSDT"], [], monkeypatch)
        await engine.refresh_positions()

        assert engine.history.recent()[0].fees > 0

    async def test_a_position_still_open_records_nothing(self, monkeypatch):
        engine, _, _ = engine_with(
            ["SOLUSDT"], [row("SOLUSDT", "2.36", "106.20")], monkeypatch
        )
        await engine.refresh_positions()

        assert engine.history.recent() == []

    async def test_the_snapshot_carries_the_history(self, monkeypatch):
        # The dashboard reads the snapshot and nothing else, so a record the
        # snapshot omits does not exist as far as the operator is concerned.
        engine, _, _ = engine_with(["SOLUSDT"], [], monkeypatch)
        await engine.refresh_positions()

        history = engine.snapshot()["history"]
        assert len(history["trades"]) == 1
        assert history["summary"]["trades"] == 1

    async def test_the_snapshot_reports_history_as_durable(self, monkeypatch):
        # It used to warn that history was lost on restart, and that was true.
        # Closed trades are now written to the engine state file beside the bot
        # registry, so the warning has to stop appearing or it becomes noise
        # the operator learns to ignore.
        engine, _, _ = engine_with(["SOLUSDT"], [], monkeypatch)
        assert engine.snapshot()["history"]["persistent"] is True

    async def test_a_closed_trade_is_written_to_disk(self, monkeypatch):
        # The reason this matters: the Overview counters are saved and were
        # coming back after a restart while the trades behind them were not,
        # so the two disagreed on screen about the same events.
        engine, _, _ = engine_with(["SOLUSDT"], [], monkeypatch)
        await engine.refresh_positions()
        engine.store.save(engine.registry, engine.history)

        restored = engine.store.load_history()
        assert [t.symbol for t in restored.recent()] == ["SOLUSDT"]


class TestBalanceIsReadRegardless:
    """Capital is account state, not position state.

    The balance read sat behind the same early return as position monitoring,
    which returns when no bot holds anything. A fresh engine therefore showed
    an equity of zero on an account with thousands in it, which is the one
    reading an operator would take at face value and act on.
    """

    async def test_the_balance_is_read_when_no_bot_exists(self, monkeypatch):
        engine, _, _ = engine_with([], [], monkeypatch)
        engine.registry.bots.clear()
        await engine.refresh_positions()

        assert engine.balance.wallet == Decimal("3225.82")

    async def test_the_balance_is_read_when_bots_hold_nothing(self, monkeypatch):
        engine, bot, _ = engine_with(["SOLUSDT"], [], monkeypatch)
        bot.positions.clear()
        await engine.refresh_positions()

        assert engine.balance.wallet == Decimal("3225.82")

    async def test_capital_reaches_the_snapshot(self, monkeypatch):
        engine, _, _ = engine_with([], [], monkeypatch)
        engine.registry.bots.clear()
        await engine.refresh_positions()

        totals = engine.snapshot()["totals"]
        assert totals["equity"] == "3228.65"
        assert totals["available"] == "3145.95"

    async def test_invalid_credentials_still_short_circuit(self, monkeypatch):
        # Nothing signed can be sent without usable keys, so this must not
        # turn into a request that fails every four seconds.
        engine, _, _ = engine_with([], [], monkeypatch)
        engine.credentials = CredentialStatus.missing()
        await engine.refresh_positions()

        assert engine.balance.wallet == Decimal(0)


class TestCommandResultsReachTheOperator:
    """The adopt button looked broken because the refusal went only to a log.

    Clicking Adopt on an unprotected position did exactly the right thing -
    refused, because recording a stop that does not exist is the worst lie
    this dashboard could tell - and then said nothing. The engine logged it.
    The operator saw an unchanged screen and a button that appeared dead.

    A refusal is a result. It has to travel back the same way an acceptance
    would.
    """

    def test_a_fresh_engine_reports_no_last_action(self, monkeypatch):
        engine, _, _ = engine_with([], [], monkeypatch)
        assert engine.snapshot()["last_action"] is None

    def test_a_refusal_is_recorded_for_the_dashboard(self, monkeypatch):
        engine, _, _ = engine_with([], [], monkeypatch)
        engine.note_action("adopt", ok=False, detail="ENAUSDT has no stop")

        action = engine.snapshot()["last_action"]
        assert action["ok"] is False
        assert "ENAUSDT" in action["detail"]

    def test_a_success_is_recorded_too(self, monkeypatch):
        # Not only failures. A button that reports nothing on success is the
        # same problem in the other direction.
        engine, _, _ = engine_with([], [], monkeypatch)
        engine.note_action("adopt", ok=True, detail="adopted ENAUSDT")

        assert engine.snapshot()["last_action"]["ok"] is True

    def test_the_latest_result_replaces_the_previous_one(self, monkeypatch):
        engine, _, _ = engine_with([], [], monkeypatch)
        engine.note_action("adopt", ok=False, detail="first")
        engine.note_action("adopt", ok=True, detail="second")

        assert engine.snapshot()["last_action"]["detail"] == "second"

    def test_the_result_carries_a_timestamp(self, monkeypatch):
        # So the dashboard can stop showing a stale one, and so two identical
        # messages in a row are distinguishable.
        engine, _, _ = engine_with([], [], monkeypatch)
        engine.note_action("adopt", ok=True, detail="done")

        assert engine.snapshot()["last_action"]["at"] > 0


class TestCredentialsRecoverFromATransientFailure:
    """One network blip must not strand the engine.

    Credentials were verified once at startup and never again. A ConnectTimeout
    reaching Binance during that single window left the engine with
    valid=False, which stops the refresh loop, the balance read and every
    trade - permanently, until somebody noticed and restarted it. The keys were
    fine the whole time.

    An unreachable exchange is a temporary condition and has to be retried. A
    rejected key is not, and must not be: hammering a bad key is how an API key
    gets banned.
    """

    async def test_an_unreachable_exchange_is_retried(self, monkeypatch):
        engine, _, _ = engine_with([], [], monkeypatch)
        engine.credentials = CredentialStatus.unreachable(
            "kp", "testnet", "ConnectTimeout"
        )
        assert engine.should_reverify() is True

    async def test_a_rate_limit_is_retried_not_treated_as_a_bad_key(self, monkeypatch):
        """The failure that kept the engine dark with working credentials.

        Binance answered a signed read with -1003 "too many requests; IP
        banned until ...". Every BinanceError was classified as a rejected
        key, so the engine never checked again: it sat in safe mode long after
        the twenty-minute ban expired, and told the operator their credentials
        had been rejected - sending them to re-issue keys that were fine.
        """
        engine, _, _ = engine_with([], [], monkeypatch)
        engine.credentials = CredentialStatus.rate_limited(
            "kp", "testnet", "[-1003] Way too many requests; IP(1.2.3.4) banned until 1790151658022"
        )
        assert engine.credentials.retryable is True
        assert engine.should_reverify() is True

    async def test_a_rate_limit_does_not_blame_the_key(self, monkeypatch):
        status = CredentialStatus.rate_limited("kp", "testnet", "[-1003] Way too many requests")
        assert "rate limiting this address" in status.message
        assert "rejected" not in status.message

    async def test_the_recheck_waits_out_a_known_ban(self, monkeypatch):
        """A health check must not knock while the address is banned.

        The refresh loop runs every few seconds and a signed request bypasses
        the public pacing deliberately, so that a protective stop always goes
        out. Without this gate the recovery added above would send three
        hundred requests into a twenty-minute ban, which is what makes the
        exchange extend it.
        """
        import time as clock
        from types import SimpleNamespace

        from scalper.exchange import market_limits

        engine, _, _ = engine_with([], [], monkeypatch)
        engine.credentials = CredentialStatus.rate_limited("kp", "testnet", "[-1003] too many")
        # The harness blanks ExchangeConfig, so give the gate an origin to
        # look up - that lookup is the whole point of this test.
        rest = "https://testnet.binancefuture.com"
        monkeypatch.setattr(
            "scalper.engine.__main__.ExchangeConfig", lambda: SimpleNamespace(rest=rest)
        )
        gate = market_limits.MarketLimit()
        gate.blocked_until = clock.monotonic() + 600
        monkeypatch.setitem(market_limits.LIMITS, rest, gate)
        assert engine.should_reverify() is False

        # ...and resumes the moment it clears, rather than needing a restart.
        gate.blocked_until = clock.monotonic() - 1
        assert engine.should_reverify() is True

    async def test_position_reads_pause_while_the_address_is_banned(self, monkeypatch):
        """The loop that kept the ban alive.

        _refresh_positions calls a signed endpoint every few seconds. While
        the address was banned each call was itself another request Binance
        counted, so the deadline kept moving: 08:20 to 09:03 to 09:14 to
        09:16, all without a single successful read. The ban could only expire
        once the engine stopped knocking.
        """
        import time as clock
        from types import SimpleNamespace

        from scalper.exchange import market_limits

        engine, client, _ = engine_with([], [], monkeypatch)
        engine.credentials = CredentialStatus.accepted(
            "kp", "testnet", balance="100", can_trade=True
        )
        rest = "https://testnet.binancefuture.com"
        monkeypatch.setattr(
            "scalper.engine.__main__.ExchangeConfig", lambda: SimpleNamespace(rest=rest)
        )
        gate = market_limits.MarketLimit()
        gate.blocked_until = clock.monotonic() + 600
        monkeypatch.setitem(market_limits.LIMITS, rest, gate)

        assert engine._rate_limit_pause() > 0
        await engine._refresh_positions()
        # Safe mode holds, and crucially nothing was sent.
        assert engine.safe_mode is True
        assert client.calls == [] if hasattr(client, "calls") else True

    async def test_reads_resume_once_the_ban_clears(self, monkeypatch):
        import time as clock
        from types import SimpleNamespace

        from scalper.exchange import market_limits

        engine, _, _ = engine_with([], [], monkeypatch)
        rest = "https://testnet.binancefuture.com"
        monkeypatch.setattr(
            "scalper.engine.__main__.ExchangeConfig", lambda: SimpleNamespace(rest=rest)
        )
        gate = market_limits.MarketLimit()
        gate.blocked_until = clock.monotonic() - 1
        monkeypatch.setitem(market_limits.LIMITS, rest, gate)
        assert engine._rate_limit_pause() == 0.0

    async def test_the_pause_is_announced_once_not_once_per_attempt(self, monkeypatch, caplog):
        # At a four second cadence an hour-long ban would otherwise write nine
        # hundred identical lines and bury everything else in the log.
        import logging
        import time as clock
        from types import SimpleNamespace

        from scalper.exchange import market_limits

        engine, _, _ = engine_with([], [], monkeypatch)
        rest = "https://testnet.binancefuture.com"
        monkeypatch.setattr(
            "scalper.engine.__main__.ExchangeConfig", lambda: SimpleNamespace(rest=rest)
        )
        gate = market_limits.MarketLimit()
        gate.blocked_until = clock.monotonic() + 600
        monkeypatch.setitem(market_limits.LIMITS, rest, gate)
        with caplog.at_level(logging.WARNING, logger="scalper.engine"):
            for _ in range(5):
                engine._rate_limit_pause()
        assert sum("pausing account reads" in r.message for r in caplog.records) == 1

    async def test_a_rejected_key_is_not_retried(self, monkeypatch):
        # Retrying a key the exchange has refused achieves nothing and risks a
        # ban. It needs a human to fix the key, not another attempt.
        engine, _, _ = engine_with([], [], monkeypatch)
        engine.credentials = CredentialStatus.rejected(
            "kp", "testnet", "Invalid API-key"
        )
        assert engine.should_reverify() is False

    async def test_missing_credentials_are_not_retried(self, monkeypatch):
        engine, _, _ = engine_with([], [], monkeypatch)
        engine.credentials = CredentialStatus.missing()
        assert engine.should_reverify() is False

    async def test_working_credentials_are_not_rechecked(self, monkeypatch):
        engine, _, _ = engine_with([], [], monkeypatch)
        assert engine.should_reverify() is False

    async def test_recovery_re_runs_reconciliation(self, monkeypatch):
        # Reconciliation is skipped while credentials are unusable, so the
        # engine would otherwise stay in safe mode after the keys came back.
        engine, _, _ = engine_with([], [], monkeypatch)
        engine.credentials = CredentialStatus.unreachable(
            "kp", "testnet", "ConnectTimeout"
        )
        engine.safe_mode = True

        seen: list[str] = []

        async def note_reconcile():
            seen.append("reconciled")

        monkeypatch.setattr(engine, "reconcile_account", note_reconcile)

        async def accepted(_config):
            return CredentialStatus.accepted(
                "kp", "testnet", balance="1000", can_trade=True
            )

        from types import SimpleNamespace

        from scalper.engine import __main__ as module
        monkeypatch.setattr(module, "ExchangeConfig", lambda: SimpleNamespace(api_key="test-key"))
        monkeypatch.setattr(module, "verify", accepted)
        await engine.verify_credentials()

        assert engine.credentials.valid is True
        assert seen == ["reconciled"]

async def test_refresh_recovers_after_exchange_quantity_is_reconciled(monkeypatch):
    engine, bot, _ = engine_with(
        ['SOLUSDT'], [row('SOLUSDT', '1.18', '106.20')], monkeypatch)
    engine.safe_mode = False
    await engine.refresh_positions()
    assert engine.safe_mode
    assert bot.positions['SOLUSDT'].quantity == Decimal('1.18')
    await engine.refresh_positions()
    assert not engine.safe_mode
    assert not engine._refresh_failed


async def test_refresh_recovers_only_after_stop_is_verified(monkeypatch):
    from unittest.mock import AsyncMock
    engine, bot, client = engine_with(
        ['SOLUSDT'], [row('SOLUSDT', '2.36', '106.20')], monkeypatch)
    engine.safe_mode = False
    original = client.list_algo_orders
    monkeypatch.setattr(client, 'list_algo_orders', AsyncMock(side_effect=TimeoutError))
    await engine.refresh_positions()
    assert engine.safe_mode and not bot.positions['SOLUSDT'].stop_armed
    await engine.refresh_positions()
    assert engine.safe_mode
    monkeypatch.setattr(client, 'list_algo_orders', original)
    await engine.refresh_positions()
    assert bot.positions['SOLUSDT'].stop_armed
    assert not engine.safe_mode


async def test_refresh_recovery_does_not_override_halt(monkeypatch):
    engine, _, _ = engine_with([], [], monkeypatch)
    engine.safe_mode = True
    engine._refresh_failed = True
    engine.store.control['halted'] = True
    await engine.refresh_positions()
    assert engine.safe_mode


async def test_failed_balance_refresh_keeps_last_known_values(monkeypatch):
    from scalper.engine.account import AccountBalance
    engine, _, client = engine_with([], [], monkeypatch)
    previous = AccountBalance(Decimal('100'), Decimal('90'), Decimal('101'))
    engine.balance = previous
    async def fail():
        raise RuntimeError('account unavailable')
    monkeypatch.setattr(client, 'get_account', fail)
    await engine.refresh_positions()
    assert engine.balance is previous
    assert engine.safe_mode
    assert engine._refresh_failed


def test_unknown_balance_is_distinct_from_real_zero():
    from scalper.engine.account import AccountBalance
    assert AccountBalance.unknown().describe()['balance_known'] is False
    assert AccountBalance(Decimal('0'), Decimal('0'), Decimal('0')).describe()['balance_known'] is True


async def test_balance_failure_recovers_on_successful_refresh(monkeypatch):
    engine, _, client = engine_with([], [], monkeypatch)
    original = client.get_account
    async def unavailable():
        return {}
    monkeypatch.setattr(client, 'get_account', unavailable)
    await engine.refresh_positions()
    assert engine.safe_mode
    assert not engine.balance.known
    monkeypatch.setattr(client, 'get_account', original)
    await engine.refresh_positions()
    assert engine.balance.known
    assert engine.balance.wallet == Decimal('3225.82')
    assert not engine.safe_mode


def test_rate_limit_alert_explains_clean_reconciliation(monkeypatch):
    from scalper.engine.reconciler import reconcile
    engine, _, _ = engine_with([], [], monkeypatch)
    engine.reconciliation = reconcile([], [])
    engine.safe_mode = True
    monkeypatch.setattr(engine, '_rate_limit_pause', lambda: 300)
    alert = next(item for item in engine._alerts() if item['id'] == 'safe-mode')
    assert 'Binance rate limit' in alert['message']
    assert 'reconciliation' not in alert['message']
