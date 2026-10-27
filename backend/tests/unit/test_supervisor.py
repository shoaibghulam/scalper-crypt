"""Process supervision.

One command must start the whole application, but the engine must remain its
own operating system process. Those two requirements are not in tension: a
parent that spawns two children satisfies both, and it is what systemd or a
compose file would do anyway.

What must never happen is the web application spawning the engine itself.
Uvicorn workers are separate processes that each run startup independently, so
an engine started from inside the app would be multiplied by the worker count.
"""

import pytest

from scalper.settings import AppSettings
from scalper.supervisor import (
    MAX_RESTARTS,
    RESTART_WINDOW_S,
    ProcessSpec,
    RestartPolicy,
    build_specs,
)


class TestProcessSpecs:
    def test_both_processes_are_launched(self):
        names = {spec.name for spec in build_specs(AppSettings())}
        assert names == {"api", "engine"}

    def test_each_runs_as_its_own_python_module(self):
        for spec in build_specs(AppSettings()):
            assert "-m" in spec.args
            assert any(arg.startswith("scalper.") for arg in spec.args)

    def test_the_engine_is_not_a_child_of_the_web_app(self):
        # Both are siblings under the supervisor. If the engine were spawned by
        # the API, running the API with multiple workers would start one engine
        # per worker, and each would pass its own risk checks in isolation.
        specs = {spec.name: spec for spec in build_specs(AppSettings())}
        assert "scalper.api" not in " ".join(specs["engine"].args)

    def test_the_engine_is_marked_critical(self):
        specs = {spec.name: spec for spec in build_specs(AppSettings())}
        assert specs["engine"].critical is True

    def test_the_api_is_not_critical(self):
        # Losing the dashboard is an inconvenience. Losing the engine means
        # positions are unmanaged.
        specs = {spec.name: spec for spec in build_specs(AppSettings())}
        assert specs["api"].critical is False


class TestRestartPolicy:
    def test_a_first_crash_is_restarted(self):
        policy = RestartPolicy()
        assert policy.should_restart("engine", now=0.0) is True

    def test_backoff_grows_with_each_attempt(self):
        policy = RestartPolicy()
        first = policy.delay_for(1)
        second = policy.delay_for(2)
        third = policy.delay_for(3)
        assert first < second < third

    def test_backoff_is_capped(self):
        policy = RestartPolicy()
        assert policy.delay_for(50) == policy.delay_for(51)

    def test_a_crash_loop_gives_up_rather_than_thrashing(self):
        # Restarting forever against a permanent fault, such as bad
        # credentials, hides the real problem behind a wall of log noise.
        policy = RestartPolicy()
        for _ in range(MAX_RESTARTS):
            assert policy.should_restart("engine", now=0.0) is True
        assert policy.should_restart("engine", now=0.0) is False

    def test_the_window_alone_does_not_forgive_a_slow_crash_loop(self):
        # This previously asserted that letting the window elapse restored the
        # allowance. That was the bug: exponential backoff pushes retries past
        # the window, so a process that never starts restarts forever. Only
        # actually staying up earns a fresh allowance now.
        policy = RestartPolicy()
        for _ in range(MAX_RESTARTS):
            policy.should_restart("engine", now=0.0)
        assert policy.should_restart("engine", now=RESTART_WINDOW_S + 1) is False

    def test_staying_up_restores_the_allowance(self):
        policy = RestartPolicy()
        for _ in range(MAX_RESTARTS):
            policy.should_restart("engine", now=0.0)
        policy.note_healthy("engine")
        assert policy.should_restart("engine", now=0.0) is True

    def test_failures_are_counted_per_process(self):
        policy = RestartPolicy()
        for _ in range(MAX_RESTARTS):
            policy.should_restart("engine", now=0.0)
        assert policy.should_restart("api", now=0.0) is True


class TestSpecValidation:
    def test_a_spec_needs_a_command(self):
        with pytest.raises(ValueError, match="args"):
            ProcessSpec(name="engine", args=[], critical=True)

    def test_a_spec_needs_a_name(self):
        with pytest.raises(ValueError, match="name"):
            ProcessSpec(name="", args=["python"], critical=True)


class FakeChild:
    """Stands in for a Popen handle so the policy can be tested without spawning."""

    def __init__(self, exit_code: int | None = None) -> None:
        self._code = exit_code
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self._code

    def terminate(self) -> None:
        self.terminated = True
        self._code = 0

    def kill(self) -> None:
        self.killed = True
        self._code = -9

    def wait(self, timeout: float | None = None) -> int:
        return self._code or 0


class TestSupervisorDecisions:
    def _supervisor(self):
        from scalper.supervisor import Supervisor

        return Supervisor(build_specs(AppSettings()))

    def test_a_crashed_engine_is_restarted(self, monkeypatch):
        sup = self._supervisor()
        started: list[str] = []
        monkeypatch.setattr(sup, "start", lambda spec: started.append(spec.name))
        monkeypatch.setattr("scalper.supervisor.time.sleep", lambda _: None)

        spec = next(s for s in sup.specs if s.name == "engine")
        assert sup._handle_exit(spec, 1) is True
        assert started == ["engine"]

    def test_a_persistently_crashing_engine_stops_the_supervisor(self, monkeypatch):
        # Restarting forever against a rejected API key would bury the real
        # error. Giving up surfaces it.
        sup = self._supervisor()
        monkeypatch.setattr(sup, "start", lambda spec: None)
        monkeypatch.setattr("scalper.supervisor.time.sleep", lambda _: None)

        spec = next(s for s in sup.specs if s.name == "engine")
        for _ in range(MAX_RESTARTS):
            assert sup._handle_exit(spec, 1) is True
        assert sup._handle_exit(spec, 1) is False

    def test_a_crashing_dashboard_never_stops_the_supervisor(self, monkeypatch):
        # The engine must keep managing positions even if the web app is broken.
        sup = self._supervisor()
        monkeypatch.setattr(sup, "start", lambda spec: None)
        monkeypatch.setattr("scalper.supervisor.time.sleep", lambda _: None)

        spec = next(s for s in sup.specs if s.name == "api")
        for _ in range(MAX_RESTARTS + 3):
            assert sup._handle_exit(spec, 1) is True


class TestShutdown:
    def test_running_children_are_asked_to_exit_politely_first(self):
        # The engine releases its single-instance lock and closes its exchange
        # streams on the polite signal.
        from scalper.supervisor import Supervisor

        sup = Supervisor(build_specs(AppSettings()))
        child = FakeChild(exit_code=None)
        sup.children = {"engine": child}
        sup.stop_all()
        assert child.terminated is True
        assert child.killed is False

    def test_already_exited_children_are_left_alone(self):
        from scalper.supervisor import Supervisor

        sup = Supervisor(build_specs(AppSettings()))
        child = FakeChild(exit_code=0)
        sup.children = {"api": child}
        sup.stop_all()
        assert child.terminated is False

    def test_stopping_prevents_a_restart_race(self, monkeypatch):
        # A child dying during shutdown must not be brought back up.
        from scalper.supervisor import Supervisor

        sup = Supervisor(build_specs(AppSettings()))
        started: list[str] = []
        monkeypatch.setattr(sup, "start", lambda spec: started.append(spec.name))
        monkeypatch.setattr("scalper.supervisor.time.sleep", lambda _: None)
        sup._stopping = True
        sup._restart(next(s for s in sup.specs if s.name == "engine"))
        assert started == []
