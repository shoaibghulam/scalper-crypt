"""Start the whole application with one command.

The engine stays a separate operating system process. That is not ceremony: a
trading engine living inside the web application would be multiplied by the
uvicorn worker count, because workers are separate processes that each run
startup independently. Four workers would mean four engines, four times the
intended position size, and four risk governors each satisfied that they were
within limits.

So the engine is a sibling, not a child of the web app, and this supervisor is
their common parent. It is deliberately the same shape a systemd unit or a
compose file would give you, which means moving to either later changes nothing
about how the processes behave.

Restarts are bounded. Restarting forever against a permanent fault, such as a
rejected API key, buries the real error under a wall of log noise.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field

from scalper.childreaper import ChildReaper
from scalper.settings import AppSettings

MAX_RESTARTS = 5
RESTART_WINDOW_S = 60.0

#: A child that stayed up this long is treated as having started successfully,
#: so a later death gets a fresh restart allowance. Without this, a process
#: that runs for hours and then crashes is judged against failures from days ago.
HEALTHY_RUNTIME_S = 30.0
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 30.0

# How long a child gets to shut down cleanly before it is killed. The engine
# uses this window to release its lock and close its streams.
SHUTDOWN_GRACE_S = 10.0


@dataclass
class ProcessSpec:
    name: str
    args: list[str]
    critical: bool

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("process spec requires a name")
        if not self.args:
            raise ValueError("process spec requires args to execute")


def build_specs(settings: AppSettings) -> list[ProcessSpec]:
    """The two processes that make up the application.

    The engine is listed first so it is starting while the web server binds its
    port, which shortens the window in which the dashboard shows a disconnected
    engine on a cold start.
    """
    return [
        ProcessSpec(
            name="engine",
            args=[sys.executable, "-m", "scalper.engine"],
            # Losing the engine means positions are unmanaged. Losing the
            # dashboard means you cannot watch them, which is far less bad.
            critical=True,
        ),
        ProcessSpec(
            name="api",
            args=[sys.executable, "-m", "scalper.api"],
            critical=False,
        ),
    ]


@dataclass
class RestartPolicy:
    """Bounded restarts with exponential backoff, counted per process.

    Two counters, deliberately. A time window catches a fast crash loop. A
    consecutive counter catches a slow one, which the window alone cannot: by
    the fourth retry the backoff delays are long enough that earlier failures
    have aged out, so the window never fills and a permanently broken process
    restarts forever. That was observed as an engine started ten times against
    a lock it could never take, with no conclusion ever reached.
    """

    failures: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    consecutive: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def note_healthy(self, name: str) -> None:
        """Record that this process ran long enough to count as started."""
        self.consecutive[name] = 0
        self.failures[name] = []

    def should_restart(self, name: str, *, now: float) -> bool:
        if self.consecutive[name] >= MAX_RESTARTS:
            return False

        recent = [at for at in self.failures[name] if now - at < RESTART_WINDOW_S]
        if len(recent) >= MAX_RESTARTS:
            self.failures[name] = recent
            return False

        recent.append(now)
        self.failures[name] = recent
        self.consecutive[name] += 1
        return True

    def attempts(self, name: str) -> int:
        return self.consecutive[name]

    @staticmethod
    def delay_for(attempt: int) -> float:
        return float(min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** (attempt - 1))))


class Supervisor:
    """Runs the child processes and keeps the critical one alive."""

    def __init__(self, specs: list[ProcessSpec]) -> None:
        self.specs = specs
        self.policy = RestartPolicy()
        self.children: dict[str, subprocess.Popen[bytes]] = {}
        self.started_at: dict[str, float] = {}
        # Binds children to this process lifetime, so a hard kill of the
        # supervisor cannot leave an engine holding the lock and the port.
        self.reaper = ChildReaper()
        self._stopping = False

    def start(self, spec: ProcessSpec) -> None:
        print(f"[supervisor] starting {spec.name}", flush=True)
        child = subprocess.Popen(spec.args)
        self.children[spec.name] = child
        self.started_at[spec.name] = time.monotonic()
        self.reaper.adopt(child)

    def stop_all(self) -> None:
        """Ask every child to exit, then insist.

        The engine needs the polite signal: it releases its single-instance
        lock and closes its exchange streams on the way out. Killing it outright
        leaves the lock file behind, which is survivable but noisy.
        """
        self._stopping = True
        for name, child in self.children.items():
            if child.poll() is None:
                print(f"[supervisor] stopping {name}", flush=True)
                child.terminate()

        deadline = time.monotonic() + SHUTDOWN_GRACE_S
        for name, child in self.children.items():
            remaining = max(0.0, deadline - time.monotonic())
            try:
                child.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                print(f"[supervisor] {name} did not stop; killing", flush=True)
                child.kill()

    def run(self) -> int:
        for spec in self.specs:
            self.start(spec)

        signal.signal(signal.SIGINT, self._on_signal)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, self._on_signal)

        by_name = {spec.name: spec for spec in self.specs}

        try:
            while not self._stopping:
                time.sleep(0.4)
                for name, child in list(self.children.items()):
                    code = child.poll()
                    if code is None:
                        continue
                    if not self._handle_exit(by_name[name], code):
                        return 1
        except KeyboardInterrupt:
            pass
        finally:
            self.stop_all()
        return 0

    def _handle_exit(self, spec: ProcessSpec, code: int) -> bool:
        ran_for = time.monotonic() - self.started_at.get(spec.name, time.monotonic())
        print(
            f"[supervisor] {spec.name} exited with code {code} after {ran_for:.0f}s",
            flush=True,
        )

        # A process that stayed up and then died is a different situation from
        # one that never started. Give it a fresh allowance.
        if ran_for >= HEALTHY_RUNTIME_S:
            self.policy.note_healthy(spec.name)

        if not spec.critical:
            # The dashboard can come and go without touching a position.
            if self.policy.should_restart(spec.name, now=time.monotonic()):
                self._restart(spec)
            return True

        if not self.policy.should_restart(spec.name, now=time.monotonic()):
            print(
                f"[supervisor] {spec.name} failed {MAX_RESTARTS} times in "
                f"{RESTART_WINDOW_S:.0f}s; this is a persistent fault, not a "
                "transient one. Read its output above rather than restarting.",
                flush=True,
            )
            return False

        self._restart(spec)
        return True

    def _restart(self, spec: ProcessSpec) -> None:
        delay = RestartPolicy.delay_for(self.policy.attempts(spec.name))
        print(f"[supervisor] restarting {spec.name} in {delay:.0f}s", flush=True)
        time.sleep(delay)
        if not self._stopping:
            self.start(spec)

    def _on_signal(self, *_: object) -> None:
        self._stopping = True


def main() -> int:
    settings = AppSettings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"[supervisor] mode={settings.env.value} "
        f"dashboard=http://{settings.api_host}:{settings.api_port}",
        flush=True,
    )
    return Supervisor(build_specs(settings)).run()
