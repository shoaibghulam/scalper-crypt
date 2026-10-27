"""Cached signal series - the reason v2 is fast.

Scoring a strategy over a year spends almost all of its time in assess(),
recomputing indicators from scratch on a fresh window for every bar. Measured
on this machine that is 444-800 microseconds per candle, against 2.6 seconds to
load a whole symbol-year. Downloading was never the expensive part.

A run scores three periods twice each, and the six passes differ only in
capital and cost assumptions. Those change entries, brackets and fills - they
do not change which bars signal. So the signal pass runs once, is stored, and
every later pass over the same candles reads it instead of recomputing it.

The key covers the strategy, its source code and the exact candles. If any of
those change the key changes, so a stale entry is never served: a silently
wrong backtest is far worse than a slow one.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from scalper.backtest import signal_series
from scalper.exchange.market_data import Candle
from scalper.research.fast.store import ROOT, digest

CACHE = ROOT / "_signals"
#: Above this the workers contend for memory bandwidth and gain nothing.
MAX_WORKERS = 8
LONG, SHORT = 1, -1
_SOURCE = Path(__file__).resolve().parents[2]


def code_digest() -> str:
    """A fingerprint of every source file that can change a signal.

    Separate from the report's model_sha256, which records what produced a
    saved result. This one exists to invalidate a cache, so it is deliberately
    over-broad: hashing a file that turns out not to matter costs one
    recomputation, while missing one serves a wrong answer.
    """
    files = [
        *sorted((_SOURCE / "strategies").glob("*.py")),
        *sorted((_SOURCE / "indicators").glob("*.py")),
        _SOURCE / "backtest.py",
    ]
    fingerprint = hashlib.sha256()
    for path in files:
        fingerprint.update(path.name.encode())
        fingerprint.update(path.read_bytes())
    return fingerprint.hexdigest()


def cache_key(strategy_id: str, symbol: str, interval: str, bars: np.ndarray, code: str) -> str:
    parts = "|".join((strategy_id, symbol.upper(), interval, digest(bars), code))
    return hashlib.sha256(parts.encode()).hexdigest()


def path_for(key: str) -> Path:
    return CACHE / f"{key}.npz"


def read(key: str) -> dict[int, str] | None:
    path = path_for(key)
    if not path.exists():
        return None
    try:
        with np.load(path, allow_pickle=False) as stored:
            at, side = stored["at"], stored["side"]
    except (ValueError, OSError, KeyError):
        # A truncated cache file is a performance loss, not a correctness one:
        # drop it and recompute rather than failing the run.
        path.unlink(missing_ok=True)
        return None
    if len(at) != len(side):
        path.unlink(missing_ok=True)
        return None
    return {int(t): ("LONG" if int(s) == LONG else "SHORT") for t, s in zip(at, side, strict=True)}


def write(key: str, signals: dict[int, str]) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    times = sorted(signals)
    path = path_for(key)
    tmp = path.with_suffix(".tmp.npz")
    np.savez_compressed(
        tmp,
        at=np.array(times, dtype=np.int64),
        side=np.array([LONG if signals[t] == "LONG" else SHORT for t in times], dtype=np.int8),
    )
    tmp.replace(path)
    return path


def signals_for(
    bars: np.ndarray,
    candles: list[Candle],
    *,
    strategy_id: str,
    symbol: str,
    interval: str = "1m",
    start_index: int = 0,
    progress: Callable[[int, int], None] | None = None,
    code: str | None = None,
) -> tuple[dict[int, str], bool]:
    """The strategy's signals over these candles, computed once and kept.

    Returns the series and whether it came from the cache, so a caller can
    report honestly how much work it actually skipped.
    """
    key = cache_key(strategy_id, symbol, interval, bars, code or code_digest())
    cached = read(key)
    if cached is not None:
        return cached, True
    computed = signal_series(
        candles, strategy_id=strategy_id, start_index=start_index, progress=progress
    )
    # Losing the cache write costs speed on the next run, nothing more.
    with contextlib.suppress(OSError):
        write(key, computed)
    return computed, False


#: symbol, interval, start, end, strategy id, start index, code digest,
#: store root, cache directory.
WarmJob = tuple[str, str, int, int, str, int, str, str, str]


def _warm_one(job: WarmJob) -> tuple[str, int, bool, str]:
    """Compute and cache one strategy's signals. Runs in a worker process.

    Takes the candle RANGE rather than the candles. Shipping half a million
    bars to each worker costs more than the work itself; re-reading them from
    the stored month files takes about a second.

    The store and cache directories travel with the job. A worker is a fresh
    process that re-imports this module, so anything configured in the parent
    at runtime - a different root, a test using a temporary one - would
    otherwise be silently ignored and the worker would read the wrong data.
    """
    symbol, interval, start_ms, end_ms, strategy_id, start_index, code, root, cache = job
    import scalper.research.fast.signals as this
    from scalper.research.fast import store

    store.ROOT = Path(root)
    this.CACHE = Path(cache)

    try:
        bars = store.load_range(symbol, interval, start_ms, end_ms)
        series, cached = signals_for(
            bars,
            store.to_candles(bars),
            strategy_id=strategy_id,
            symbol=symbol,
            interval=interval,
            start_index=start_index,
            code=code,
        )
        return strategy_id, len(series), cached, ""
    except Exception as exc:  # one strategy failing must not take the rest down
        return strategy_id, 0, False, f"{type(exc).__name__}: {exc}"


def warm_many(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    strategy_ids: Sequence[str],
    *,
    start_index: int,
    workers: int | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, str]:
    """Cache every strategy's signals at once, across processes.

    The signal pass is pure CPU over a fixed candle block and each strategy is
    independent of the others, so running them one at a time leaves most of the
    machine idle. Threads would not help - this is Python bytecode under a GIL -
    so these are processes.

    Nothing about the result changes: each worker writes the same cache entry
    the serial path would have written, under the same key. Returns any
    per-strategy errors, keyed by id.
    """
    from scalper.research.fast import store

    code = code_digest()
    # Load and digest the candles ONCE. Deriving each key from its own load
    # would read the whole year back per strategy, which costs more than the
    # cache lookup saves.
    bars = store.load_range(symbol, interval, start_ms, end_ms)
    pending = [
        s
        for s in strategy_ids
        if read(cache_key(s, symbol, interval, bars, code)) is None
    ]
    del bars
    errors: dict[str, str] = {}
    if not pending:
        if progress is not None:
            progress("every signal series already cached", len(strategy_ids), len(strategy_ids))
        return errors
    # Leave a couple of cores for the engine and the dashboard.
    count = workers or max(1, min(len(pending), (os.cpu_count() or 4) - 2, MAX_WORKERS))
    jobs = [
        (symbol, interval, start_ms, end_ms, s, start_index, code, str(store.ROOT), str(CACHE))
        for s in pending
    ]
    done = 0
    if progress is not None:
        progress(f"computing {len(pending)} signal series across {count} cores", 0, len(pending))
    with ProcessPoolExecutor(max_workers=count) as pool:
        # as_completed, not map: map yields in submission order, so a slow
        # first strategy holds back the progress of every one that finished
        # before it and the run looks frozen for minutes at a time.
        futures = [pool.submit(_warm_one, job) for job in jobs]
        for future in as_completed(futures):
            strategy_id, found, cached, error = future.result()
            done += 1
            if error:
                errors[strategy_id] = error
            if progress is not None:
                note = error or f"{found} signals{' (cached)' if cached else ''}"
                progress(f"{strategy_id}: {note}", done, len(pending))
    return errors


def clear() -> int:
    """Drop every cached signal series. Returns how many files were removed."""
    if not CACHE.is_dir():
        return 0
    removed = 0
    for path in CACHE.glob("*.npz"):
        path.unlink(missing_ok=True)
        removed += 1
    return removed


def cached_count() -> int:
    return len(list(CACHE.glob("*.npz"))) if CACHE.is_dir() else 0
