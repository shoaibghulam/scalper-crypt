"""Run, reproduce or benchmark research without the dashboard."""

import argparse
import asyncio
import random
import time
import uuid
from pathlib import Path

from scalper.research.data.store import load
from scalper.research.experiments import (
    DATA,
    ROOT,
    VERSION,
    ExperimentRequest,
    atomic_json,
    code_fingerprint,
    execute,
    read_json,
    run_dataset,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path, help="ExperimentRequest JSON; downloads public data")
    source.add_argument(
        "--replay", type=Path, help="Reproduce a saved report using immutable datasets"
    )
    source.add_argument(
        "--benchmark", type=Path, help="Benchmark a local dataset; does not select strategies"
    )
    parser.add_argument("--evaluations", type=int, default=1000)
    args = parser.parse_args()
    if args.benchmark:
        from scalper.research.backtest import Backtester
        from scalper.research.features import Features
        from scalper.research.schema import SearchConfig
        from scalper.research.search import random_strategy

        if not 1 <= args.evaluations <= 100000:
            parser.error("Benchmark evaluations must be between 1 and 100000")
        dataset = load(args.benchmark)
        tester = Backtester(Features(dataset))
        config = SearchConfig()
        rng = random.Random(config.seed)
        tester.run(random_strategy(rng, dataset.request.timeframe, config), config)
        begin = time.perf_counter()
        for _ in range(args.evaluations):
            tester.run(random_strategy(rng, dataset.request.timeframe, config), config)
        seconds = time.perf_counter() - begin
        print(
            f"{args.evaluations} evaluations / {len(dataset.bars)} bars in {seconds:.3f}s; "
            f"{args.evaluations / seconds:.1f}/s, warmed compiled CPU, "
            "includes generation/features/metrics"
        )
        return
    job_id = uuid.uuid4().hex
    directory = ROOT / job_id
    if args.config:
        request = ExperimentRequest.model_validate(read_json(args.config))
        atomic_json(
            directory / "state.json",
            {
                "id": job_id,
                "kind": "research",
                "status": "queued",
                "created_at": time.time(),
                "request": request.model_dump(mode="json"),
                "phase": "Queued",
            },
        )
        asyncio.run(execute(directory))
    else:
        original = read_json(args.replay)
        if original.get("code_sha256") and original["code_sha256"] != code_fingerprint():
            raise ValueError(
                "Research code differs from the saved experiment; "
                "restore its code version before reproducing"
            )
        request = ExperimentRequest.model_validate(original["request"])
        directory.mkdir(parents=True)
        results = []
        for coin in original["results"]:
            # Resolve only content-addressed files in the dataset directory.
            name = coin["dataset_file"]
            if Path(name).name != name or len(name) != 69:
                raise ValueError("Invalid dataset filename")
            results.append(
                run_dataset(
                    load(DATA / name),
                    request.search,
                    directory,
                    lambda **value: print(value.get("phase", ""), flush=True),
                    lambda: None,
                )
            )
        atomic_json(
            directory / "reproduction.json",
            {
                "version": VERSION,
                "source_experiment": original["id"],
                "request": original["request"],
                "results": results,
                "note": "Same observations reused; this is reproduction, not fresh evidence",
            },
        )
    print(f"Report directory: {directory}")


if __name__ == "__main__":
    main()
