"""Single-instance enforcement for the trading engine.

Two engines against one account is the most expensive bug this system can
have: both read the same signal, both pass their own per-process risk checks,
and both send an order. The position is then double the intended size and the
risk limits that were supposed to prevent it were never consulted jointly.

Process separation makes this unlikely. The lock makes it impossible.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scalper.engine.lock import EngineLock, EngineLockHeld


class TestEngineLock:
    def test_acquires_when_free(self, tmp_path):
        with EngineLock(tmp_path / "engine.lock") as lock:
            assert lock.held

    def test_second_holder_is_refused(self, tmp_path):
        path = tmp_path / "engine.lock"
        with EngineLock(path):
            with pytest.raises(EngineLockHeld):
                with EngineLock(path):
                    pass

    def test_lock_is_released_on_exit(self, tmp_path):
        path = tmp_path / "engine.lock"
        with EngineLock(path):
            pass
        with EngineLock(path) as second:
            assert second.held

    def test_lock_is_released_even_when_the_body_raises(self, tmp_path):
        path = tmp_path / "engine.lock"
        with pytest.raises(RuntimeError):
            with EngineLock(path):
                raise RuntimeError("engine crashed")
        with EngineLock(path) as second:
            assert second.held

    def test_lock_file_records_the_owning_pid(self, tmp_path):
        # Diagnostic only. The kernel lock is the authority; this is so an
        # operator reading the directory can see who holds it.
        path = tmp_path / "engine.lock"
        with EngineLock(path):
            assert str(os.getpid()) in path.read_text()

    def test_leftover_file_from_a_crash_does_not_block_startup(self, tmp_path):
        # A hard kill leaves the file behind but not the lease: the OS drops
        # the lock when the process table entry goes. Refusing to start after
        # every crash would leave open positions unmanaged, which is worse than
        # the race being guarded against.
        path = tmp_path / "engine.lock"
        path.write_text("999999\n")
        with EngineLock(path) as lock:
            assert lock.held

    def test_unreadable_lock_contents_do_not_block_startup(self, tmp_path):
        path = tmp_path / "engine.lock"
        path.write_text("not-a-pid")
        with EngineLock(path) as lock:
            assert lock.held

    def test_error_message_names_the_holder(self, tmp_path):
        path = tmp_path / "engine.lock"
        with EngineLock(path), pytest.raises(EngineLockHeld, match=str(os.getpid())):
            with EngineLock(path):
                pass


class TestAcrossRealProcesses:
    """The case the single-process tests cannot cover.

    An earlier implementation compared recorded pids and passed every test in
    this file while still letting two real engines run at once. Checking a file
    is not the same as locking it. These tests spawn actual processes, which is
    the only way to observe the difference.
    """

    HOLDER = (
        "import sys, time\n"
        "sys.path.insert(0, sys.argv[2])\n"
        "from scalper.engine.lock import EngineLock\n"
        "with EngineLock(sys.argv[1]):\n"
        "    print('ACQUIRED', flush=True)\n"
        "    time.sleep(30)\n"
    )

    CONTENDER = (
        "import sys\n"
        "sys.path.insert(0, sys.argv[2])\n"
        "from scalper.engine.lock import EngineLock, EngineLockHeld\n"
        "try:\n"
        "    with EngineLock(sys.argv[1]):\n"
        "        print('ACQUIRED', flush=True)\n"
        "except EngineLockHeld:\n"
        "    print('REFUSED', flush=True)\n"
    )

    @staticmethod
    def _src_root() -> str:
        return str(Path(__file__).resolve().parents[2] / "src")

    def test_a_second_process_is_refused_while_the_first_holds(self, tmp_path):
        path = tmp_path / "engine.lock"
        holder = subprocess.Popen(
            [sys.executable, "-c", self.HOLDER, str(path), self._src_root()],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "ACQUIRED"

            contender = subprocess.run(
                [sys.executable, "-c", self.CONTENDER, str(path), self._src_root()],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert contender.stdout.strip() == "REFUSED", (
                "a second engine acquired the lock while the first held it; "
                "this is duplicate order placement"
            )
        finally:
            holder.kill()
            holder.wait(timeout=10)

    def test_lock_is_available_once_the_holder_is_hard_killed(self, tmp_path):
        # kill -9 equivalent: no cleanup runs, yet the lease must not survive.
        path = tmp_path / "engine.lock"
        holder = subprocess.Popen(
            [sys.executable, "-c", self.HOLDER, str(path), self._src_root()],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ACQUIRED"
        holder.kill()
        holder.wait(timeout=10)

        contender = subprocess.run(
            [sys.executable, "-c", self.CONTENDER, str(path), self._src_root()],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert contender.stdout.strip() == "ACQUIRED"
