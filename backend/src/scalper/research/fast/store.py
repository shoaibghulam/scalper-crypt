"""Columnar candle storage for offline research.

One file per symbol, interval and UTC month. Months are the unit because they
match what Binance publishes as a single archive, so a month is either fully
present and verified or absent - there is no half-written month to reason about.

The format is a numpy structured array rather than JSON. The v1 day-cache
spends most of its load time turning text into floats; a stored array skips
that entirely, which is what makes a rerun feel instant.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from scalper.exchange.market_data import Candle

MINUTE_MS = 60_000
INTERVAL_MS = {"1m": MINUTE_MS}
ROOT = Path(__file__).resolve().parents[5] / "var" / "research-v2"

#: Field order matches the exchange kline payload so a row maps straight onto a
#: Candle without a lookup table drifting out of step with it.
CANDLE_DTYPE = np.dtype(
    [
        ("open_time", "<i8"),
        ("open", "<f8"),
        ("high", "<f8"),
        ("low", "<f8"),
        ("close", "<f8"),
        ("volume", "<f8"),
        ("quote_volume", "<f8"),
        ("taker_buy_base", "<f8"),
    ]
)


class DataGap(ValueError):
    """Requested history is not fully covered. Never silently shortened."""


@dataclass(frozen=True)
class Month:
    year: int
    month: int

    @property
    def label(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def start_ms(self) -> int:
        return int(datetime(self.year, self.month, 1, tzinfo=UTC).timestamp() * 1000)

    @property
    def next(self) -> Month:
        return Month(self.year + self.month // 12, self.month % 12 + 1)

    @property
    def end_ms(self) -> int:
        return self.next.start_ms


def month_of(timestamp_ms: int) -> Month:
    moment = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
    return Month(moment.year, moment.month)


def months_covering(start_ms: int, end_ms: int) -> list[Month]:
    """Every UTC month touched by the half-open range [start, end)."""
    if end_ms <= start_ms:
        raise ValueError("end must be after start")
    months, cursor = [], month_of(start_ms)
    while cursor.start_ms < end_ms:
        months.append(cursor)
        cursor = cursor.next
    return months


def path_for(symbol: str, interval: str, month: Month) -> Path:
    return ROOT / symbol.upper() / interval / f"{month.label}.npy"


def digest(bars: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(bars).tobytes()).hexdigest()


def validate(bars: np.ndarray, interval: str) -> None:
    """Refuse anything a backtest must not silently treat as continuous."""
    if bars.dtype != CANDLE_DTYPE:
        raise ValueError("unexpected candle layout")
    if not len(bars):
        raise DataGap("empty candle block")
    step = INTERVAL_MS[interval]
    times = bars["open_time"]
    if int(times[0]) % step:
        raise ValueError("candles must align to the interval")
    if len(times) > 1 and not np.all(np.diff(times) == step):
        raise DataGap("candles are not consecutive; gaps are never filled or skipped")
    opens, closes = bars["open"], bars["close"]
    highs, lows = bars["high"], bars["low"]
    for name in ("open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base"):
        if not np.all(np.isfinite(bars[name])):
            raise ValueError("candles contain non-finite values")
    if np.any(lows <= 0) or np.any(bars["volume"] < 0):
        raise ValueError("candles contain impossible prices or volume")
    if np.any(lows > np.minimum(opens, closes)) or np.any(highs < np.maximum(opens, closes)):
        raise ValueError("candles contain an invalid OHLC range")


def manifest_path(symbol: str, interval: str) -> Path:
    return ROOT / symbol.upper() / interval / "manifest.json"


def read_manifest(symbol: str, interval: str) -> dict[str, dict[str, object]]:
    path = manifest_path(symbol, interval)
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        # A damaged manifest is an index, not the data: rebuilding it costs one
        # re-verification, so it must never take the stored months down with it.
        return {}
    return loaded if isinstance(loaded, dict) else {}


def record_month(
    symbol: str, interval: str, month: Month, bars: np.ndarray, *, complete: bool
) -> None:
    path = manifest_path(symbol, interval)
    entries = read_manifest(symbol, interval)
    entries[month.label] = {
        "candles": len(bars),
        "sha256": digest(bars),
        "first": int(bars["open_time"][0]),
        "last": int(bars["open_time"][-1]),
        "complete": complete,
        "written_at": int(datetime.now(tz=UTC).timestamp() * 1000),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, sort_keys=True, indent=1), encoding="utf-8")
    tmp.replace(path)


def write_month(
    symbol: str, interval: str, month: Month, bars: np.ndarray, *, complete: bool = True
) -> Path:
    """Persist a month atomically.

    `complete=False` is for the month currently in progress, which no archive
    publishes yet. Such a month is still a prefix starting at the first of the
    month with no interior gaps - it just ends early, and is rewritten as more
    days are published. The manifest records which kind it is so a later run
    knows to extend it rather than trust it as final.
    """
    validate(bars, interval)
    expected = (month.end_ms - month.start_ms) // INTERVAL_MS[interval]
    if int(bars["open_time"][0]) != month.start_ms:
        raise DataGap(f"{symbol} {month.label} does not start at the first of the month")
    if complete and len(bars) != expected:
        raise DataGap(f"{symbol} {month.label} is incomplete: {len(bars)} of {expected} candles")
    if len(bars) > expected:
        raise DataGap(f"{symbol} {month.label} has more candles than the month holds")
    path = path_for(symbol, interval, month)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=f"{month.label}-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            np.save(stream, bars, allow_pickle=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
    record_month(symbol, interval, month, bars, complete=complete)
    return path


def stored_months(symbol: str, interval: str) -> set[str]:
    """Months held as final. A partial month is deliberately excluded.

    A caller asking what it can skip downloading must not skip a month that is
    still filling up, so the in-progress month is reported as absent until the
    archive for it is published.
    """
    directory = ROOT / symbol.upper() / interval
    if not directory.is_dir():
        return set()
    manifest = read_manifest(symbol, interval)
    return {
        p.stem
        for p in directory.glob("*.npy")
        if manifest.get(p.stem, {}).get("complete", True)
    }


def load_range(symbol: str, interval: str, start_ms: int, end_ms: int) -> np.ndarray:
    """Every stored candle in [start, end). Raises DataGap rather than truncating."""
    needed = months_covering(start_ms, end_ms)
    missing = [m.label for m in needed if not path_for(symbol, interval, m).exists()]
    if missing:
        raise DataGap(
            f"{symbol} {interval} is missing {len(missing)} month(s): {', '.join(missing[:6])}"
            + ("..." if len(missing) > 6 else "")
        )
    bars = np.concatenate(
        [np.load(path_for(symbol, interval, m), allow_pickle=False) for m in needed]
    )
    times = bars["open_time"]
    window = np.ascontiguousarray(bars[(times >= start_ms) & (times < end_ms)])
    validate(window, interval)
    expected = (end_ms - start_ms) // INTERVAL_MS[interval]
    if len(window) != expected:
        raise DataGap(
            f"{symbol} covers {len(window)} of {expected} candles; "
            "a shorter period is not reported as a full one"
        )
    return window


def to_candles(bars: np.ndarray) -> list[Candle]:
    """Rows to the Candle tuples replay expects.

    Reads each column once into a Python list. Indexing numpy scalars row by
    row costs several times more, and this sits on the hot path of every run.
    """
    close_times = (bars["open_time"] + MINUTE_MS - 1).tolist()
    return list(
        map(
            Candle,
            bars["open_time"].tolist(),
            bars["open"].tolist(),
            bars["high"].tolist(),
            bars["low"].tolist(),
            bars["close"].tolist(),
            bars["volume"].tolist(),
            close_times,
            bars["quote_volume"].tolist(),
            bars["taker_buy_base"].tolist(),
        )
    )
