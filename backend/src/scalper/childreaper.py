"""Make child processes die with their parent.

Killing a parent process does not kill its children on Windows, and on POSIX it
only does so for terminal-attached signals. So a supervisor that is killed
abruptly, rather than asked to stop, leaves the trading engine and the web app
running. They keep the single-instance lock and the HTTP port, and every
subsequent start then fails against resources held by a process nobody can see.

Observed in practice as an engine restarted ten times against a lock held by an
orphan from a previous run.

The graceful shutdown path still exists and is still preferred, because it lets
the engine release its lock and close its exchange streams. This is the
backstop for when that path does not run.
"""

from __future__ import annotations

import logging
import subprocess
import sys

log = logging.getLogger("scalper.supervisor")

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    # A Job Object with this limit kills every assigned process when the last
    # handle to the job closes, which happens when the parent dies for any
    # reason, including a hard kill.
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


class ChildReaper:
    """Binds children to the parent lifetime, as far as the platform allows."""

    def __init__(self) -> None:
        self._job = None
        if sys.platform == "win32":
            self._job = self._create_job()

    @staticmethod
    def _create_job() -> object | None:
        try:
            job = ctypes.windll.kernel32.CreateJobObjectW(None, None)
            if not job:
                return None
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = (
                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            ok = ctypes.windll.kernel32.SetInformationJobObject(
                job,
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            )
            return job if ok else None
        except (OSError, AttributeError):
            # Job objects are unavailable in some sandboxes. Losing the
            # backstop is survivable; failing to start is not.
            return None

    @property
    def active(self) -> bool:
        return self._job is not None

    def adopt(self, process: subprocess.Popen[bytes]) -> None:
        """Bind one child to the parent lifetime."""
        if self._job is None:
            return
        try:
            handle = ctypes.windll.kernel32.OpenProcess(
                0x001F0FFF, False, process.pid
            )
            if handle:
                ctypes.windll.kernel32.AssignProcessToJobObject(self._job, handle)
                ctypes.windll.kernel32.CloseHandle(handle)
        except (OSError, AttributeError) as exc:
            log.debug("could not bind pid %s to the job object: %s", process.pid, exc)
