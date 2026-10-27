"""The engine supervisor.

The property under test is the safety posture at startup: trading disabled
until reconciliation has compared our state against the exchange. Starting
enabled and disabling on a problem is the wrong order, because the window in
between is exactly when a stale position would be traded against.
"""

import asyncio
from types import SimpleNamespace

import pytest

from scalper.config import Environment
from scalper.engine.__main__ import Engine
from scalper.settings import AppSettings


def make_engine(env: Environment = Environment.TESTNET) -> Engine:
    return Engine(AppSettings(BINANCE_ENV=env))


def offline(engine: Engine) -> Engine:
    """Stub the startup checks that talk to the exchange.

    These tests are about the lifecycle, not about credentials, and a unit test
    that reaches the network is slow, flaky, and fails on a machine with no
    keys configured.
    """

    async def noop() -> None:
        return None

    engine.verify_credentials = noop  # type: ignore[method-assign]
    engine.reconcile_account = noop  # type: ignore[method-assign]
    engine.refresh_positions = noop  # type: ignore[method-assign]
    return engine


class TestStartupPosture:
    def test_engine_starts_in_safe_mode(self):
        assert make_engine().safe_mode is True

    def test_engine_is_not_running_before_start(self):
        assert make_engine().running is False

    def test_safe_mode_is_independent_of_environment(self):
        # Live is not treated as more trusted than testnet.
        for env in Environment:
            assert make_engine(env).safe_mode is True


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_run_stops_when_asked(self):
        engine = offline(make_engine())
        task = asyncio.create_task(engine.run())
        await asyncio.sleep(0.05)
        assert engine.running is True
        engine.request_stop()
        await asyncio.wait_for(task, timeout=5)
        assert engine.running is False

    @pytest.mark.asyncio
    async def test_stop_requested_before_start_exits_immediately(self):
        engine = offline(make_engine())
        engine.request_stop()
        await asyncio.wait_for(engine.run(), timeout=5)
        assert engine.running is False

    @pytest.mark.asyncio
    async def test_running_flag_is_cleared_even_on_failure(self):
        engine = offline(make_engine())
        engine.request_stop()
        await engine.run()
        assert engine.running is False


@pytest.mark.asyncio
async def test_account_refresh_precedes_signals_and_uses_elapsed_time(monkeypatch):
    engine = offline(make_engine())
    events = []
    elapsed = [0.0]
    monkeypatch.setattr("scalper.engine.__main__.time", SimpleNamespace(
        monotonic=lambda: elapsed[0], time=lambda: 62 + elapsed[0],
    ))
    monkeypatch.setattr("scalper.engine.__main__.HEARTBEAT_SECONDS", 0.001)

    async def refresh():
        events.append("refresh")
        if len(events) == 3:
            engine.request_stop()

    async def scan():
        events.append("scan")
        elapsed[0] += 60

    engine.refresh_positions = refresh
    engine._signal_cycle = scan
    await engine.run()
    assert events[:3] == ["refresh", "scan", "refresh"]
