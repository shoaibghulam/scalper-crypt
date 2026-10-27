"""Forward-only public-data paper sessions; never submit exchange orders."""

import argparse
import asyncio
import os
import time

from scalper.research.backtest import Backtester
from scalper.research.data.loader import digest
from scalper.research.data.models import INTERVALS, DataRequest, Dataset
from scalper.research.data.store import load
from scalper.research.experiments import DATA, atomic_json, get_dataset, read_json
from scalper.research.features import Features
from scalper.research.integration import validated_export
from scalper.research.locking import acquire, release
from scalper.research.ml import predict
from scalper.research.schema import SearchConfig
from scalper.research.validation import summarize


async def execute(directory):
    state = read_json(directory / "state.json")
    artifact = read_json(directory / "strategy.json")
    definition = validated_export(artifact)
    config = SearchConfig.model_validate(artifact["search_config"])
    initial = load(DATA / state["dataset_file"])
    interval = INTERVALS[definition.timeframe]
    start = state["start"]
    # Use the original immutable dataset for indicator warm-up, then append every
    # closed bar through the forward period. No backward use of paper observations.
    dataset = initial

    def update(**values):
        state.update(values, heartbeat=time.time())
        atomic_json(directory / "state.json", state)

    lock = None
    try:
        lock = acquire(directory.parent / "paper.lock")
        update(
            status="running",
            pid=os.getpid(),
            phase="Waiting for new closed candles",
            dataset_segments=[state["dataset_file"]],
        )
        while True:
            stop = (directory / "cancel").exists()
            end = int(time.time() * 1000) // interval * interval
            if end > dataset.request.end:
                request = DataRequest(
                    symbol=artifact["symbol"],
                    timeframe=definition.timeframe,
                    start=dataset.request.end,
                    end=end,
                    funding=config.costs.require_funding,
                )
                appended, appended_path = await get_dataset(request, lambda *_: None)
                # Prevent running beyond data contract limits; session must be reviewed/restarted.
                dataset = Dataset(
                    request=DataRequest(
                        symbol=artifact["symbol"],
                        timeframe=definition.timeframe,
                        start=initial.request.start,
                        end=end,
                        funding=config.costs.require_funding,
                    ),
                    source="paper-public",
                    source_sha256=digest([dataset.source_sha256, appended.source_sha256]),
                    bars=(*dataset.bars, *appended.bars),
                    funding_events=(*dataset.funding_events, *appended.funding_events),
                    funding_status=initial.funding_status,
                )
                state["dataset_segments"].append(appended_path.name)
            first = (start - dataset.request.start) // interval
            if len(dataset.bars) > first:
                features = Features(dataset)
                model = artifact.get("model")
                mask = predict(model, features.ml_matrix()) >= model["threshold"] if model else None
                result = Backtester(features).run(
                    definition, config, first, mask=mask, finalize=stop
                )
                update(
                    result=summarize(result),
                    observed_through=end,
                    phase="Forward paper simulation; evaluated on closed candles",
                )
                if result["metrics"]["halted"]:
                    update(status="halted", phase="Safety limit reached; no further paper entries")
                    return
            if stop:
                update(
                    status="stopped",
                    phase="Stopped by operator; paper position closed at last observed close",
                )
                return
            update()
            await asyncio.sleep(15)
    except Exception as exc:
        update(status="failed", phase=f"{type(exc).__name__}: {exc}; paper session stopped")
    finally:
        release(lock)


if __name__ == "__main__":
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    asyncio.run(execute(parser.parse_args().job))
