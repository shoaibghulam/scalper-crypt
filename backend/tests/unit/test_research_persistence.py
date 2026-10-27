import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from scalper.api import quant_lab
from scalper.research import experiments


def denied():
    error = PermissionError("File is temporarily held open")
    error.winerror = 5
    return error


def test_transient_windows_lock_retries_without_losing_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    experiments.atomic_json(path, {"generation": 1})
    original_replace = os.replace
    attempts, sleeps = [], []

    def replace(source, target):
        attempts.append(source)
        if len(attempts) <= 3:
            assert experiments.read_json(path) == {"generation": 1}
            raise denied()
        original_replace(source, target)

    monkeypatch.setattr(experiments.os, "replace", replace)
    monkeypatch.setattr(experiments.time, "sleep", sleeps.append)
    experiments.atomic_json(path, {"generation": 2})
    assert experiments.read_json(path) == {"generation": 2}
    assert sleeps == [0.01, 0.02, 0.04]
    assert len(attempts) == 4
    assert not list(tmp_path.glob("*.tmp"))


def test_permanent_lock_fails_boundedly_and_preserves_old_file(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    experiments.atomic_json(path, {"generation": 1})
    sleeps = []

    def replace(*_):
        raise denied()

    monkeypatch.setattr(experiments.os, "replace", replace)
    monkeypatch.setattr(experiments.time, "sleep", sleeps.append)
    with pytest.raises(PermissionError):
        experiments.atomic_json(path, {"generation": 2})
    assert len(sleeps) == 8
    assert experiments.read_json(path) == {"generation": 1}
    assert not list(tmp_path.glob("*.tmp"))


def test_serialization_failure_preserves_old_file_and_cleans_temp(tmp_path):
    path = tmp_path / "state.json"
    experiments.atomic_json(path, {"generation": 1})
    with pytest.raises(ValueError):
        experiments.atomic_json(path, {"bad": float("nan")})
    assert experiments.read_json(path) == {"generation": 1}
    assert not list(tmp_path.glob("*.tmp"))


def test_unrelated_permission_error_is_not_retried(tmp_path, monkeypatch):
    def replace(*_):
        raise PermissionError("Real permissions problem")

    sleeps = []
    monkeypatch.setattr(experiments.os, "replace", replace)
    monkeypatch.setattr(experiments.time, "sleep", sleeps.append)
    with pytest.raises(PermissionError):
        experiments.atomic_json(tmp_path / "state.json", {})
    assert not sleeps


def test_concurrent_writers_have_unique_temps_and_publish_whole_snapshots(tmp_path):
    path = tmp_path / "state.json"

    def write(i):
        experiments.atomic_json(path, {"generation": i, "payload": [i] * 1000})

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(40)))
    final = experiments.read_json(path)
    assert final["payload"] == [final["generation"]] * 1000
    assert not list(tmp_path.glob("*.tmp"))


def test_download_is_frozen_bytes_not_open_mutable_file(tmp_path, monkeypatch):
    monkeypatch.setattr(quant_lab, "ROOT", tmp_path)
    job_id = "c" * 32
    path = tmp_path / job_id / "state.json"
    experiments.atomic_json(path, {"generation": 1})
    response = quant_lab.download_report(job_id)
    experiments.atomic_json(path, {"generation": 2})
    assert json.loads(response.body) == {"generation": 1}
    assert experiments.read_json(path) == {"generation": 2}
    assert "attachment" in response.headers["content-disposition"]


@pytest.mark.skipif(os.name != "nt", reason="Real Windows sharing violation")
def test_actual_windows_handle_lock_can_be_retried(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes

    path = tmp_path / "state.json"
    experiments.atomic_json(path, {"generation": 1})
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    # GENERIC_READ, FILE_SHARE_READ (no FILE_SHARE_DELETE), OPEN_EXISTING.
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80, None)
    assert handle != wintypes.HANDLE(-1).value
    sleeps = []

    def unlock(delay):
        nonlocal handle
        sleeps.append(delay)
        kernel.CloseHandle(handle)
        handle = None

    monkeypatch.setattr(experiments.time, "sleep", unlock)
    try:
        experiments.atomic_json(path, {"generation": 2})
    finally:
        if handle:
            kernel.CloseHandle(handle)
    assert sleeps == [0.01]
    assert experiments.read_json(path) == {"generation": 2}


@pytest.mark.skipif(os.name != "nt", reason="Windows CRT read errors use errno EACCES")
def test_read_retries_crt_access_denied_without_winerror(tmp_path, monkeypatch):
    import errno
    from pathlib import Path

    path = tmp_path / "state.json"
    experiments.atomic_json(path, {"generation": 2})
    original = Path.read_bytes
    attempts = []

    def read(self):
        attempts.append(self)
        if len(attempts) == 1:
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        return original(self)

    sleeps = []
    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(experiments.time, "sleep", sleeps.append)
    assert experiments.read_json(path) == {"generation": 2}
    assert sleeps == [0.01]
