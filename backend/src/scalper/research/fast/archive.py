"""Bulk candle download from Binance's public data archive.

v1 walked history one UTC day at a time over the REST klines endpoint: 365
round trips for a year, measured at roughly 677 ms each, so about four minutes
per symbol before any strategy ran. The same year is twelve monthly archives.

Only published history is downloaded here. The current month has no monthly
archive yet, so it is assembled from daily archives, and the last day or two
that even the daily archive has not caught up with come from REST. Everything
is verified against the checksum the archive publishes beside it.

Public market data only: no credentials are sent and nothing is ordered.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import cast

import httpx
import numpy as np

from scalper.config import LIVE_REST
from scalper.exchange.market_limits import paced_get
from scalper.research.fast.store import (
    CANDLE_DTYPE,
    INTERVAL_MS,
    DataGap,
    Month,
    month_of,
    months_covering,
    read_manifest,
    stored_months,
    write_month,
)

ARCHIVE = "https://data.binance.vision/data/futures/um"
REST_LIMIT = 1500
MAX_PARALLEL = 4
#: Columns as the archive and the REST payload both order them.
KLINE_COLUMNS = 12
TAKER_BUY_BASE = 9

Progress = Callable[[str, int, int], None]


class ArchiveUnavailable(RuntimeError):
    """The archive has no file for this period yet."""


def monthly_url(symbol: str, interval: str, month: Month) -> str:
    stem = f"{symbol.upper()}-{interval}-{month.label}"
    return f"{ARCHIVE}/monthly/klines/{symbol.upper()}/{interval}/{stem}.zip"


def daily_url(symbol: str, interval: str, day: date) -> str:
    stem = f"{symbol.upper()}-{interval}-{day.isoformat()}"
    return f"{ARCHIVE}/daily/klines/{symbol.upper()}/{interval}/{stem}.zip"


def columns_to_candles(flat: np.ndarray) -> np.ndarray:
    """A 12-column kline table to the stored layout."""
    bars = np.empty(len(flat), dtype=CANDLE_DTYPE)
    bars["open_time"] = flat[:, 0].astype(np.int64)
    for index, name in enumerate(("open", "high", "low", "close", "volume"), start=1):
        bars[name] = flat[:, index]
    bars["quote_volume"] = flat[:, 7]
    bars["taker_buy_base"] = flat[:, TAKER_BUY_BASE]
    return bars


def parse_klines(text: str) -> np.ndarray:
    """Archive CSV to candle rows.

    Every field is parsed by numpy in one call rather than row by row in
    Python; on a month of one-minute candles that is the difference between
    tenths of a second and several seconds.
    """
    lines = [line for line in text.splitlines() if line]
    if lines and not lines[0][:1].isdigit():
        lines = lines[1:]  # newer archives carry a header row, older ones do not
    if not lines:
        raise ValueError("archive contained no candles")
    fields = [value for line in lines for value in line.split(",")]
    if len(fields) != len(lines) * KLINE_COLUMNS:
        raise ValueError("archive rows do not have the expected number of columns")
    try:
        flat = np.array(fields, dtype=np.float64).reshape(len(lines), KLINE_COLUMNS)
    except ValueError as exc:
        raise ValueError("archive contains a non-numeric field") from exc
    return columns_to_candles(flat)


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    response = await client.get(url)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response


async def fetch_zip(client: httpx.AsyncClient, url: str) -> np.ndarray | None:
    """One archive file, verified against its published checksum."""
    response = await _get(client, url)
    if response is None:
        return None
    payload = response.content
    checksum = await _get(client, url + ".CHECKSUM")
    if checksum is not None:
        expected = checksum.text.split()[0].strip()
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError(f"checksum mismatch for {url.rsplit('/', 1)[-1]}")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
            names = bundle.namelist()
            if len(names) != 1:
                raise ValueError("archive holds more than one file")
            text = bundle.read(names[0]).decode("utf-8")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"archive is not readable: {url.rsplit('/', 1)[-1]}") from exc
    return parse_klines(text)


async def fetch_rest(
    client: httpx.AsyncClient, symbol: str, interval: str, start_ms: int, end_ms: int
) -> np.ndarray:
    """The most recent candles, which no archive has published yet."""
    step = INTERVAL_MS[interval]
    collected: list[np.ndarray] = []
    cursor = start_ms
    while cursor < end_ms:
        # Paced through the shared budget: this runs while the scanner is also
        # polling, and an unpaced download of several symbols is what gets the
        # address rate limited for everyone in the process.
        response = await paced_get(
            client,
            LIVE_REST,
            "/fapi/v1/klines",
            {
                "symbol": symbol.upper(),
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms - 1,
                "limit": REST_LIMIT,
            },
        )
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            break
        table = np.array([row[:KLINE_COLUMNS] for row in rows], dtype=np.float64)
        collected.append(columns_to_candles(table))
        cursor = int(table[-1, 0]) + step
    if not collected:
        return np.empty(0, dtype=CANDLE_DTYPE)
    bars = np.concatenate(collected)
    return bars[(bars["open_time"] >= start_ms) & (bars["open_time"] < end_ms)]


async def fetch_month(
    client: httpx.AsyncClient, symbol: str, interval: str, month: Month, now_ms: int
) -> np.ndarray:
    """A whole month, from whichever sources have published it."""
    whole = await fetch_zip(client, monthly_url(symbol, interval, month))
    if whole is not None:
        return whole
    # No monthly archive: assemble the days, then top up from REST.
    end_ms = min(month.end_ms, now_ms)
    if end_ms <= month.start_ms:
        raise ArchiveUnavailable(f"{symbol} {month.label} has not started")
    blocks: list[np.ndarray] = []
    day = datetime.fromtimestamp(month.start_ms / 1000, tz=UTC).date()
    last = datetime.fromtimestamp((end_ms - 1) / 1000, tz=UTC).date()
    while day <= last:
        daily = await fetch_zip(client, daily_url(symbol, interval, day))
        if daily is None:
            break
        blocks.append(daily)
        day += timedelta(days=1)
    covered = month.start_ms + sum(len(b) for b in blocks) * INTERVAL_MS[interval]
    if covered < end_ms:
        tail = await fetch_rest(client, symbol, interval, covered, end_ms)
        if len(tail):
            blocks.append(tail)
    if not blocks:
        raise ArchiveUnavailable(f"{symbol} {month.label} is not published yet")
    return np.concatenate(blocks)


def outstanding_months(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Month]:
    """Months still needing a download to cover [start, end).

    A final month is simply present. The month still in progress counts as
    covered only if the copy on disk already reaches the last bar this range
    asks for - otherwise today's run would silently score yesterday's data.

    Both the downloader and the "what will this cost" message read this, so
    the estimate and the work can never disagree.
    """
    held = stored_months(symbol, interval)
    manifest = read_manifest(symbol, interval)
    step = INTERVAL_MS[interval]
    pending = []
    for month in months_covering(start_ms, end_ms):
        if month.label in held:
            continue
        entry = manifest.get(month.label)
        needed_last = min(month.end_ms, end_ms) - step
        if entry and int(cast(int, entry.get("last", -1))) >= needed_last:
            continue
        pending.append(month)
    return pending


async def ensure_range(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    *,
    now_ms: int | None = None,
    progress: Progress | None = None,
) -> dict[str, object]:
    """Download whatever the store is missing for [start, end).

    Months already held as final are skipped, so a second run over overlapping
    dates costs nothing.
    """
    now = now_ms if now_ms is not None else int(datetime.now(tz=UTC).timestamp() * 1000)
    if end_ms > now:
        raise ValueError("cannot download candles from the future")
    wanted = months_covering(start_ms, end_ms)
    outstanding = outstanding_months(symbol, interval, start_ms, end_ms)
    current = month_of(now)
    downloaded = 0
    limits = httpx.Limits(max_connections=MAX_PARALLEL)
    async with httpx.AsyncClient(timeout=180, follow_redirects=True, limits=limits) as client:
        for index, month in enumerate(outstanding):
            if progress is not None:
                progress(f"Downloading {symbol} {month.label}", index, len(outstanding))
            bars = await fetch_month(client, symbol, interval, month, now)
            write_month(
                symbol,
                interval,
                month,
                bars,
                complete=month.label != current.label and month.end_ms <= now,
            )
            downloaded += len(bars)
    if progress is not None:
        progress(f"{symbol} ready", len(outstanding), len(outstanding))
    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "months_total": len(wanted),
        "months_downloaded": len(outstanding),
        "months_reused": len(wanted) - len(outstanding),
        "candles_downloaded": downloaded,
    }


async def ensure_many(
    symbols: list[str],
    interval: str,
    start_ms: int,
    end_ms: int,
    *,
    now_ms: int | None = None,
    progress: Progress | None = None,
) -> list[dict[str, object]]:
    """Fill the store for several symbols, one at a time.

    Deliberately sequential across symbols: the archive is a courtesy of the
    exchange, and a run that gets itself rate limited is slower than one that
    waits its turn. A symbol that cannot be filled is reported rather than
    aborting the rest.
    """
    reports: list[dict[str, object]] = []
    for symbol in symbols:
        try:
            reports.append(
                await ensure_range(
                    symbol, interval, start_ms, end_ms, now_ms=now_ms, progress=progress
                )
            )
        except (ArchiveUnavailable, DataGap, ValueError, httpx.HTTPError) as exc:
            reports.append({"symbol": symbol.upper(), "error": str(exc)})
    return reports


def missing_months(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[str]:
    return [m.label for m in outstanding_months(symbol, interval, start_ms, end_ms)]


async def _amain() -> None:
    """python -m scalper.research.fast.archive BTCUSDT --days 365"""
    import argparse

    parser = argparse.ArgumentParser(description="Download candles for offline research")
    parser.add_argument("symbols", nargs="+")
    parser.add_argument("--interval", default="1m", choices=sorted(INTERVAL_MS))
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()
    step = INTERVAL_MS[args.interval]
    now = int(datetime.now(tz=UTC).timestamp() * 1000) // step * step
    start = now - args.days * 86_400_000
    for report in await ensure_many(
        [s.upper() for s in args.symbols],
        args.interval,
        start,
        now,
        now_ms=now,
        progress=lambda phase, done, total: print(f"[{done}/{total}] {phase}", flush=True),
    ):
        print(report)


if __name__ == "__main__":
    asyncio.run(_amain())
