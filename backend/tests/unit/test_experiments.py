import sqlite3
from decimal import Decimal

from fastapi.testclient import TestClient

from scalper.api.app import create_app
from scalper.engine.auto_trade import start_fleet
from scalper.engine.bots import BotRegistry
from scalper.engine.commands import AutoTradeRequest
from scalper.engine.experiments import configure, enforce, report
from scalper.engine.store import StateStore
from scalper.engine.trades import TradeHistory
from tests.unit.test_trade_history import trade


def row(history, control):
    return next(r for r in report(history, control)["rows"] if r["id"] == "lab-01")


def test_full_history_net_results_and_recent_window():
    history = TradeHistory()
    for i in range(100):
        history.record(trade("1", strategy="lab-01", closed_at=i))
    for i in range(20):
        history.record(trade("-1" if i < 15 else "0", strategy="lab-01", closed_at=100 + i))
    history.record(trade("-1000", strategy="lab-01", verified=False, closed_at=200))
    result = row(history, {})
    assert result["trades"] == 120 and result["wins"] == 100
    assert result["losses"] == 15 and result["breakeven"] == 5
    assert result["net"] == 85 and result["unverified"] == 1
    assert result["flagged"] and result["recent_losses"] == 15
    assert not result["disabled"]


def test_auto_disable_threshold_and_restart_persistence(tmp_path):
    history, registry, control = TradeHistory(), BotRegistry(), {}
    bot = registry.create(symbols=[], amount=Decimal(10), leverage=3, strategy="lab-01")
    for i in range(19):
        history.record(trade("-1", strategy="lab-01", closed_at=i))
    configure({"auto_disable": True}, control)
    enforce(history, control, registry)
    assert bot.state == "running"
    history.record(trade("-1", strategy="lab-01", closed_at=20))
    enforce(history, control, registry)
    assert bot.state == "stopped" and row(history, control)["disabled"]
    store = StateStore(tmp_path / "state.json")
    store.control = control
    assert store.save(registry, history)
    restored = StateStore(store.path)
    restored_history = restored.load_history()
    assert row(restored_history, restored.control)["disabled"]
    with sqlite3.connect(store.path.with_suffix(".sqlite")) as db:
        assert db.execute("SELECT COUNT(*) FROM closed_trades").fetchone()[0] == 20
    assert store.save(registry, history)
    with sqlite3.connect(store.path.with_suffix(".sqlite")) as db:
        assert db.execute("SELECT COUNT(*) FROM closed_trades").fetchone()[0] == 20
    fleet = start_fleet(registry, {}, AutoTradeRequest(amount=Decimal(10), leverage=3), {"lab-01"})
    assert all(registry.bots[i].strategy != "lab-01" for i in fleet["bot_ids"])


def test_manual_disable_preserves_positions_and_enable_does_not_start():
    registry, control, history = BotRegistry(), {}, TradeHistory()
    bot = registry.create(symbols=[], amount=Decimal(10), leverage=3, strategy="lab-01")
    position = object()
    bot.positions["SOLUSDT"] = position
    configure({"strategy": "lab-01", "disabled": True}, control)
    enforce(history, control, registry)
    assert bot.state == "stopped" and bot.positions["SOLUSDT"] is position
    configure({"strategy": "lab-01", "disabled": False}, control)
    assert not row(history, control)["disabled"] and bot.state == "stopped"


def test_experiment_request_validation():
    client = TestClient(create_app())
    assert (
        client.post("/api/strategy-monitor/settings", json={"strategy": "lab-01"}).status_code
        == 422
    )
    assert client.post("/api/strategy-monitor/settings", json={}).status_code == 422
    assert (
        client.post("/api/strategy-monitor/settings", json={"auto_disable": "true"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/strategy-monitor/settings",
            json={"auto_disable": True},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
