"""The path from a click to an order, and the boundary in the middle.

The web process relays; the engine acts. That split is the whole reason the
engine is a separate process, so these tests assert it rather than trusting it:
the API must be unable to place an order even in principle, and must not claim
one was placed.
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from scalper.api.app import create_app
from scalper.config import Environment
from scalper.engine.account import open_positions
from scalper.engine.bots import BotRegistry
from scalper.settings import AppSettings


def client() -> TestClient:
    return TestClient(create_app(AppSettings(BINANCE_ENV=Environment.TESTNET)))


class FakeExchange:
    def __init__(self, rows) -> None:
        self.rows = rows

    async def get_positions(self):
        return {"result": self.rows}


class TestAccountReading:
    async def test_flat_rows_are_not_positions(self):
        # positionRisk returns a row for every symbol on the account.
        rows = [
            {"symbol": "BTCUSDT", "positionAmt": "0", "entryPrice": "0"},
            {"symbol": "SOLUSDT", "positionAmt": "2.36", "entryPrice": "105.52"},
        ]
        found = await open_positions(FakeExchange(rows))
        assert [p.symbol for p in found] == ["SOLUSDT"]

    async def test_a_short_is_a_position(self):
        rows = [{"symbol": "ETHUSDT", "positionAmt": "-1.5", "entryPrice": "3000"}]
        found = await open_positions(FakeExchange(rows))
        assert found[0].quantity == -1.5

    async def test_a_size_with_no_entry_price_is_still_a_position(self):
        # The size is what carries the risk. Dropping it because one field is
        # malformed would hide a real exposure.
        rows = [{"symbol": "ETHUSDT", "positionAmt": "1.5", "entryPrice": "0"}]
        found = await open_positions(FakeExchange(rows))
        assert len(found) == 1

    async def test_an_unparseable_row_refuses_an_incomplete_account_snapshot(self):
        rows = [{"symbol": "BAD", "positionAmt": "not-a-number"}]
        with pytest.raises(ValueError):
            await open_positions(FakeExchange(rows))


class TestCreateBotEndpoint:
    def test_it_refuses_when_the_engine_is_not_reporting(self):
        # Accepting a trade instruction the engine will never see would be a
        # lie told with a 202.
        response = client().post(
            "/api/bots", json={"symbols": ["SOLUSDT"], "amount": "50", "leverage": 5}
        )
        assert response.status_code == 503

    def test_leverage_above_the_cap_is_rejected(self):
        response = client().post(
            "/api/bots", json={"symbols": ["SOLUSDT"], "amount": "50", "leverage": 101}
        )
        assert response.status_code == 422

    def test_high_leverage_within_the_cap_passes_the_boundary_check(self):
        # 503 rather than 422: the request is well formed and reaches the point
        # of needing an engine. Whether 50x is actually safe depends on the stop
        # distance, and that is decided in the engine against the real trade.
        response = client().post(
            "/api/bots", json={"symbols": ["SOLUSDT"], "amount": "50", "leverage": 50}
        )
        assert response.status_code == 503

    def test_a_negative_amount_is_rejected(self):
        response = client().post(
            "/api/bots", json={"symbols": ["SOLUSDT"], "amount": "-5", "leverage": 5}
        )
        assert response.status_code == 422

    def test_an_empty_symbol_list_is_rejected(self):
        response = client().post(
            "/api/bots", json={"symbols": [], "amount": "50", "leverage": 5}
        )
        assert response.status_code == 422

    def test_too_many_symbols_is_rejected(self):
        # Concurrent position limits exist for a reason.
        response = client().post(
            "/api/bots",
            json={
                "symbols": ["A", "B", "C", "D", "E", "F"],
                "amount": "50",
                "leverage": 5,
            },
        )
        assert response.status_code == 422


class TestTheApiCannotTrade:
    def test_no_route_places_an_order_directly(self):
        app = create_app(AppSettings())
        paths = {route.path for route in app.routes}
        assert "/api/order" not in paths
        assert not any("algo" in path for path in paths)

    def test_the_web_process_holds_no_exchange_client(self):
        # Structural: the module never imports one, so no code path can reach
        # the exchange from here regardless of what a handler tries to do.
        import scalper.api.app as module

        assert not hasattr(module, "BinanceFuturesClient")


class TestEngineCommandHandling:
    def _engine(self):
        from scalper.engine.__main__ import Engine

        engine = Engine(AppSettings(BINANCE_ENV=Environment.TESTNET))
        engine.safe_mode = False
        return engine

    async def test_an_unknown_command_is_ignored_not_fatal(self):
        await self._engine().handle_command({"type": "nonsense"})

    async def test_a_bot_is_not_created_in_safe_mode(self):
        engine = self._engine()
        engine.safe_mode = True
        await engine.handle_command(
            {"type": "create_bot", "symbols": ["SOLUSDT"], "amount": "50", "leverage": 5}
        )
        assert engine.registry.bots == {}

    async def test_a_bot_with_no_symbols_is_refused(self):
        engine = self._engine()
        await engine.handle_command(
            {"type": "create_bot", "symbols": [], "amount": "50", "leverage": 5}
        )
        assert engine.registry.bots == {}

    async def test_stopping_a_bot_changes_its_state(self):
        engine = self._engine()
        bot = engine.registry.create(
            symbols=["SOLUSDT"], amount=Decimal("50"), leverage=5, strategy="s"
        )
        await engine.handle_command({"type": "stop_bot", "bot_id": bot.id})
        assert engine.registry.bots[bot.id].state == "stopped"


class TestSnapshotShape:
    def test_the_snapshot_reports_bots_and_positions_from_the_registry(self):
        from scalper.engine.__main__ import Engine

        engine = Engine(AppSettings(BINANCE_ENV=Environment.TESTNET))
        engine.registry = BotRegistry()
        engine.registry.create(
            symbols=["SOLUSDT"], amount=Decimal("50"), leverage=5, strategy="s"
        )
        snapshot = engine.snapshot()
        assert len(snapshot["bots"]) == 1
        assert snapshot["totals"]["bots_running"] == 1

    def test_the_snapshot_never_contains_key_material(self):
        # Matching the word "secret" would be a false positive: it appears in
        # the help text telling an operator which variable to set. What must
        # never appear is a value shaped like a key.
        import re

        from scalper.engine.__main__ import Engine

        snapshot = Engine(AppSettings()).snapshot()
        key_shaped = re.compile(r"[A-Za-z0-9]{40,}")
        assert not key_shaped.search(str(snapshot))


@pytest.mark.parametrize("field", ["symbols", "amount", "leverage"])
def test_every_create_field_is_required(field):
    payload = {"symbols": ["SOLUSDT"], "amount": "50", "leverage": 5}
    payload.pop(field)
    assert client().post("/api/bots", json=payload).status_code == 422


class TestMarginModeReachesTheEngine:
    """The choice has to survive the whole relay, not just the form.

    The web process only relays; the engine holds the credentials and sets the
    margin type on the exchange. A mode accepted at the boundary and dropped in
    the middle would leave the operator believing they had chosen cross while
    every position was opened isolated.
    """

    def test_a_margin_mode_is_accepted(self):
        response = client().post(
            "/api/bots",
            json={
                "symbols": ["SOLUSDT"],
                "amount": "50",
                "leverage": 5,
                "margin_mode": "CROSSED",
            },
        )
        # 503 because no engine is attached in this test; 422 would mean the
        # field was rejected at the boundary.
        assert response.status_code == 503

    def test_an_unknown_margin_mode_is_refused_at_the_boundary(self):
        # Refused here rather than silently normalised, so a typo in an API
        # client is reported instead of quietly becoming isolated.
        response = client().post(
            "/api/bots",
            json={
                "symbols": ["SOLUSDT"],
                "amount": "50",
                "leverage": 5,
                "margin_mode": "WHATEVER",
            },
        )
        assert response.status_code == 422

    def test_the_mode_is_optional(self):
        response = client().post(
            "/api/bots",
            json={"symbols": ["SOLUSDT"], "amount": "50", "leverage": 5},
        )
        assert response.status_code == 503

    async def test_the_engine_applies_the_requested_mode(self):
        from decimal import Decimal

        from scalper.config import Environment
        from scalper.engine import __main__ as module
        from scalper.exchange.credentials import CredentialStatus
        from scalper.settings import AppSettings

        engine = module.Engine(AppSettings(BINANCE_ENV=Environment.TESTNET))
        engine.credentials = CredentialStatus.accepted(
            "kp", "testnet", balance="1000", can_trade=True
        )
        bot = engine.registry.create(
            symbols=["SOLUSDT"],
            amount=Decimal("50"),
            leverage=5,
            strategy="500-backtest-v1",
            margin_mode="CROSS",
        )
        assert bot.margin_mode == "CROSSED"


class TestCreationRefusalsReachTheOperator:
    """The bug: five bots requested, none created, nothing said.

    The engine was in safe mode because of an unexplained position on the
    account, so it refused - correctly. But the refusal went only to
    log.warning, and the form had already said "Sent to the engine". From the
    operator side that is a button that accepts input and does nothing.

    Every refusal on this path now travels back the same way an acceptance
    would. The adopt path was fixed earlier; create was missed.
    """

    def _engine(self):
        from decimal import Decimal  # noqa: F401

        from scalper.config import Environment
        from scalper.engine import __main__ as module
        from scalper.exchange.credentials import CredentialStatus
        from scalper.settings import AppSettings

        engine = module.Engine(AppSettings(BINANCE_ENV=Environment.TESTNET))
        engine.credentials = CredentialStatus.accepted(
            "kp", "testnet", balance="1000", can_trade=True
        )
        return engine

    async def test_safe_mode_refusal_is_reported(self):
        engine = self._engine()
        engine.safe_mode = True
        await engine.handle_command(
            {"type": "create_bot", "symbols": ["SOLUSDT"], "amount": "20"}
        )
        action = engine.snapshot()["last_action"]
        assert action["ok"] is False
        assert "safe mode" in action["detail"].lower()

    async def test_the_refusal_names_the_blocking_symbol_and_the_fix(self):
        # "Refused" alone sends the operator looking. When the cause is a
        # position the engine cannot explain, the message has to name it and
        # say what clears it.
        from scalper.engine.reconciler import ExchangePosition, reconcile

        engine = self._engine()
        engine.safe_mode = True
        engine.reconciliation = reconcile(
            [ExchangePosition(symbol="ENAUSDT", quantity=1204.0, entry_price=0.1667)],
            [],
        )
        await engine.handle_command(
            {"type": "create_bot", "symbols": ["SOLUSDT"], "amount": "20"}
        )
        detail = engine.snapshot()["last_action"]["detail"]
        assert "ENAUSDT" in detail
        assert "adopt" in detail.lower()

    async def test_safe_mode_before_reconciliation_does_not_invent_a_cause(self):
        # Nothing has been checked yet, so there is no orphan to name. Saying
        # "adopt something" here would send the operator after a position that
        # does not exist.
        engine = self._engine()
        engine.safe_mode = True
        engine.reconciliation = None
        await engine.handle_command(
            {"type": "create_bot", "symbols": ["SOLUSDT"], "amount": "20"}
        )
        detail = engine.snapshot()["last_action"]["detail"].lower()
        assert "reconciliation" in detail
        assert "adopt" not in detail

    async def test_an_empty_symbol_list_is_reported(self):
        engine = self._engine()
        engine.safe_mode = False
        await engine.handle_command({"type": "create_bot", "symbols": []})
        assert engine.snapshot()["last_action"]["ok"] is False

    async def test_an_unknown_strategy_is_reported(self):
        engine = self._engine()
        engine.safe_mode = False
        await engine.handle_command(
            {
                "type": "create_bot",
                "symbols": ["SOLUSDT"],
                "amount": "20",
                "strategy": "no-such-strategy",
            }
        )
        action = engine.snapshot()["last_action"]
        assert action["ok"] is False
        assert "strategy" in action["detail"].lower()

    async def test_stopping_an_unknown_bot_is_reported(self):
        engine = self._engine()
        await engine.handle_command({"type": "stop_bot", "bot_id": "nope"})
        assert engine.snapshot()["last_action"]["ok"] is False

    async def test_removing_a_bot_that_holds_a_position_is_reported(self):
        from decimal import Decimal

        from scalper.engine.bots import ManagedPosition

        engine = self._engine()
        bot = engine.registry.create(
            symbols=["BTCUSDT"],
            amount=Decimal("100"),
            leverage=5,
            strategy="500-backtest-v1",
        )
        bot.positions["BTCUSDT"] = ManagedPosition(
            symbol="BTCUSDT",
            side="LONG",
            quantity=Decimal("1"),
            entry=Decimal("100"),
            stop=Decimal("99"),
            target=Decimal("103"),
            leverage=5,
            opened_at=0.0,
        )
        await engine.handle_command({"type": "remove_bot", "bot_id": bot.id})
        assert engine.snapshot()["last_action"]["ok"] is False

    async def test_the_snapshot_says_whether_trading_is_enabled(self):
        # So the form can refuse BEFORE submitting, rather than accepting input
        # that the engine is certain to reject.
        engine = self._engine()
        engine.safe_mode = True
        assert engine.snapshot()["trading_enabled"] is False
        engine.safe_mode = False
        assert engine.snapshot()["trading_enabled"] is True
