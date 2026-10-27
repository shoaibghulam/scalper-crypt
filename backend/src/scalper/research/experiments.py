"""Reproducible disk-backed experiments; runnable independently of the web app."""

import argparse
import asyncio
import errno
import hashlib
import importlib.metadata
import json
import os
import platform
import tempfile
import time
from contextlib import suppress
from pathlib import Path

from pydantic import Field, field_validator

from scalper.config import LIVE_REST
from scalper.exchange.market_data import MarketDataClient
from scalper.research.data.loader import digest, download
from scalper.research.data.models import INTERVALS, DataRequest, Record, Timeframe
from scalper.research.data.store import load, save
from scalper.research.locking import acquire, release
from scalper.research.schema import SearchConfig

ROOT = Path(__file__).resolve().parents[4] / "var" / "quant-lab"
DATA = ROOT.parent / "research-datasets"
VERSION = "quant-research-1.0.0"


def code_fingerprint():
    root = Path(__file__).parent
    digest_ = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest_.update(path.relative_to(root).as_posix().encode())
        digest_.update(path.read_bytes())
    return digest_.hexdigest()


class ExperimentRequest(Record):
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "XRPUSDT")
    timeframe: Timeframe = "5m"
    days: int = Field(default=60, ge=7, le=366)
    end: int | None = Field(default=None, gt=0)
    search: SearchConfig = SearchConfig()

    @field_validator("symbols")
    @classmethod
    def symbols_valid(cls, value):
        if not 1 <= len(value) <= 20:
            raise ValueError("Choose 1 to 20 coins per experiment")
        normalized = tuple(DataRequest(symbol=s, start=0, end=300000).symbol for s in value)
        if len(set(normalized)) != len(normalized):
            raise ValueError("Duplicate coins")
        return normalized


def _retry_file_access(action):
    for attempt in range(9):
        try:
            return action()
        except PermissionError as exc:
            windows_lock = getattr(exc, "winerror", None) in {5, 32, 33} or (
                os.name == "nt" and exc.errno == errno.EACCES
            )
            if not windows_lock or attempt == 8:
                raise
            time.sleep(min(0.01 * 2**attempt, 0.5))


def atomic_json(path, value):
    """Publish a complete snapshot, tolerating temporary Windows reader locks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(filename)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        # Readers and virus scanners can briefly deny replacement. Never remove
        # or truncate the committed snapshot to work around a sharing violation.
        _retry_file_access(lambda: os.replace(temp, path))
    finally:
        with suppress(OSError):
            temp.unlink(missing_ok=True)


def read_snapshot(path):
    """Close the handle before JSON parsing or sending the snapshot to a client."""
    return _retry_file_access(path.read_bytes)


def read_json(path):
    return json.loads(read_snapshot(path))


class Cancelled(Exception):
    pass


def check_cancel(directory):
    if (directory / "cancel").exists():
        raise Cancelled("Cancelled by operator")


async def get_dataset(request, progress):
    index = DATA / "requests" / f"{digest(request.model_dump(mode='json'))}.json"
    if index.exists():
        reference = read_json(index)["dataset"]
        if len(reference) != 64 or any(c not in "0123456789abcdef" for c in reference):
            raise ValueError("Invalid cached dataset reference")
        dataset = load(DATA / f"{reference}.json")
        if dataset.request != request:
            raise ValueError("Cached dataset request mismatch")
        return dataset, DATA / f"{reference}.json"
    async with MarketDataClient(LIVE_REST) as market:
        dataset = await download(request, market, progress=progress)
    path = save(dataset, DATA)
    atomic_json(index, {"dataset": path.stem})
    return dataset, path


def run_dataset(dataset, config, directory, update, check):
    from scalper.research.backtest import Backtester
    from scalper.research.features import Features
    from scalper.research.search import evolve
    from scalper.research.validation import split_periods, validate

    periods = split_periods(len(dataset.bars), config)
    backtester = Backtester(Features(dataset))
    history = []
    with (directory / f"{dataset.request.symbol}-candidates.jsonl").open(
        "w", encoding="utf-8"
    ) as archive:

        def append(row):
            archive.write(json.dumps(row, allow_nan=False) + "\n")

        def emit(row):
            history.append(row)
            update(phase=f"Generation {row['generation']}/{config.generations}", progress=row)
            archive.flush()

        ranked, tested = evolve(backtester, config, *periods["train"], emit, append, check)
    update(phase="Walk-forward selection and validation")
    validated = validate(
        backtester, ranked, config, periods, check, lambda phase: update(phase=phase)
    )
    return {
        "symbol": dataset.request.symbol,
        "timeframe": dataset.request.timeframe,
        "dataset_sha256": digest(dataset.model_dump(mode="json")),
        "source_sha256": dataset.source_sha256,
        "periods": {
            name: {
                "indices": bounds,
                "start": dataset.bars[bounds[0]].timestamp,
                "end": dataset.bars[bounds[1] - 1].available_at,
            }
            for name, bounds in periods.items()
        },
        "total_tested": tested,
        "history": history,
        **validated,
    }


async def execute(directory):
    state_path = directory / "state.json"
    state = read_json(state_path)
    request = ExperimentRequest.model_validate(state["request"])

    def update(**values):
        state.update(values, heartbeat=time.time())
        atomic_json(state_path, state)

    def check():
        check_cancel(directory)
        if time.time() - state.get("heartbeat", 0) > 10:
            update()

    lock = None
    try:
        lock = acquire(directory.parent / "research.lock")
        update(
            status="running",
            phase="Preparing datasets",
            pid=os.getpid(),
            version=VERSION,
            code_sha256=code_fingerprint(),
            python=platform.python_version(),
            packages={
                p: importlib.metadata.version(p)
                for p in ["numpy", "numba", "optuna", "scikit-learn"]
            },
            results=[],
            errors=[],
        )
        interval = INTERVALS[request.timeframe]
        end = request.end or int(time.time() * 1000) // interval * interval
        if end % interval:
            raise ValueError("End must align to the selected timeframe")
        update(frozen_end=end)
        for index, symbol in enumerate(request.symbols):
            check()
            update(coin=symbol, coin_number=index + 1, phase=f"Loading {symbol}", progress=None)
            data_request = DataRequest(
                symbol=symbol,
                timeframe=request.timeframe,
                start=end - request.days * 86400000,
                end=end,
                funding=request.search.costs.require_funding,
            )
            try:

                def progress(done, total, coin=symbol):
                    check()
                    update(phase=f"Loading {coin}: {done:,}/{total:,} minutes")

                dataset, path = await get_dataset(data_request, progress)
                update(phase=f"Researching {symbol}", dataset=path.stem)
                result = await asyncio.to_thread(
                    run_dataset, dataset, request.search, directory, update, check
                )
                result["dataset_file"] = path.name
                state["results"].append(result)
                update()
            except Cancelled:
                raise
            except Exception as exc:
                state["errors"].append(
                    {"symbol": symbol, "error": str(exc), "type": type(exc).__name__}
                )
                update()
        update(
            status="completed" if not state["errors"] else "completed_with_errors",
            phase="Research complete; review every stage",
        )
    except Cancelled:
        update(status="cancelled", phase="Cancelled; completed results retained")
    except Exception as exc:
        update(status="failed", phase=f"{type(exc).__name__}: {exc}")
    finally:
        release(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(execute(args.job))


if __name__ == "__main__":
    main()
