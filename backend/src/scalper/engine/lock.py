"""A single-instance lease for the trading engine.

The engine owns all trading state for one exchange account: the rate-limit
budget, the user data stream, the reconciler and every open position. Running
two of them is not degraded operation, it is duplicate order placement, because
each process evaluates risk limits against its own memory only.

This is the third of three independent guards. The first is structural, keeping
the engine out of the web process so worker count cannot multiply it. The
second is the deterministic client order id, which lets Binance itself reject a
duplicate. This one closes the remaining gap: an operator starting the engine
twice by hand.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import TracebackType
from typing import IO

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class EngineLockHeld(Exception):
    """Another engine process owns this account. Do not start."""


# Windows byte-range locks are mandatory, not advisory: a locked byte cannot
# even be read by another process. The lease is therefore taken on a byte well
# past any content, so the recorded pid stays readable for diagnostics while
# the lock is held. Locking beyond end-of-file is permitted.
LOCK_BYTE_OFFSET = 4096
OWNER_FIELD_WIDTH = 64


def _try_lock(handle: IO[str]) -> bool:
    """Take an exclusive, non-blocking lock. False if someone else holds it."""
    try:
        if sys.platform == "win32":
            handle.seek(LOCK_BYTE_OFFSET)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: IO[str]) -> None:
    try:
        if sys.platform == "win32":
            handle.seek(LOCK_BYTE_OFFSET)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        # The lock dies with the process anyway.
        pass


class EngineLock:
    """Context manager holding an exclusive lease on one engine account.

    The lock file also records the owning process id, purely so an operator
    reading the directory can tell who holds it. Nothing depends on that value
    being accurate; the kernel lock is the authority.

    A lock left behind by a hard kill needs no special handling. The operating
    system drops it when the process table entry goes away, so the file may
    persist but the lease does not.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.held = False
        self.previous_owner: int | None = None
        self._handle: IO[str] | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.previous_owner = self._read_owner()

        existed = self.path.exists()
        # Not a context manager on purpose: the handle must outlive this call.
        # The lease lasts as long as the file is open, so closing it here would
        # release the lock immediately and defeat the entire mechanism.
        handle = open(  # noqa: SIM115
            self.path, "r+" if existed else "w+", encoding="utf-8"
        )

        if not _try_lock(handle):
            handle.close()
            owner = self.previous_owner
            detail = f" as pid {owner}" if owner is not None else ""
            raise EngineLockHeld(
                f"engine already running{detail} (lock file {self.path}); "
                "refusing to start a second instance against the same account"
            )

        self._handle = handle
        self.held = True
        self._write_owner()

    def release(self) -> None:
        if not self.held or self._handle is None:
            return
        try:
            _unlock(self._handle)
        finally:
            self._handle.close()
            self._handle = None
            self.held = False

    def _write_owner(self) -> None:
        assert self._handle is not None
        # Fixed-width so the record can be overwritten in place. Truncating a
        # file that carries a byte-range lock past its end is not portable.
        self._handle.seek(0)
        self._handle.write(f"{os.getpid()}\n".ljust(OWNER_FIELD_WIDTH))
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def _read_owner(self) -> int | None:
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.isdigit():
                    return int(stripped)
        except OSError:
            return None
        return None

    @property
    def owner_pid(self) -> int | None:
        return os.getpid() if self.held else self.previous_owner

    def __enter__(self) -> EngineLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
