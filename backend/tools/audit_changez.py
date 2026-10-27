"""Replay saved Changez datasets and save every fixed-rule comparison to SQLite."""
import hashlib
import json
from pathlib import Path

from scalper.api.research import ResearchRequest, evaluate_candles
from scalper.api.research_store import load_run, save_run
from scalper.exchange.market_data import Candle

ROOT = Path(__file__).resolve().parents[2]


def cached_bars(report):
    quality = report["data_quality"]
    first, last = quality["first"], quality["last"]
    found = {}
    for path in sorted((ROOT / "var/research-candles").glob("BTCUSDT-*.json")):
        _, start, end = path.stem.split("-")
        if int(end) <= first or int(start) > last:
            continue
        for raw in json.loads(path.read_text(encoding="utf-8")):
            if first <= int(raw[0]) <= last:
                bar = Candle.from_raw(raw)
                if bar.open_time in found and found[bar.open_time] != bar:
                    raise ValueError("Conflicting cached candles")
                found[bar.open_time] = bar
    bars = [found[t] for t in range(first, last+1, 60_000)]
    assert len(bars) == report["candles"]
    return bars


def main():
    comparisons = []
    for source_id in ("be82310584f447b9a377897ee01393c3",
                      "fe95afb5635d4ad780e93761d72cea28"):
        source = load_run(source_id)
        bars = cached_bars(source)
        digest = hashlib.sha256(json.dumps([
            [b.open_time, b.open, b.high, b.low, b.close, b.volume] for b in bars
        ], separators=(",", ":")).encode()).hexdigest()
        strategies = ["changez-cost-filtered-1m"]
        if source_id == "be82310584f447b9a377897ee01393c3":
            strategies.insert(0, "changez-1m")
        for strategy in strategies:
            print(f"Testing {strategy}, {len(bars)} candles", flush=True)
            request = ResearchRequest(csv="cached-candles:"+digest,
                **{**source["config"], "strategy": strategy})
            report = evaluate_candles(request, bars)
            report["dataset_sha256"] = digest
            report["comparison_source_run"] = source_id
            report["research_note"] = (
                "Retrospective fixed-rule comparison. These periods overlap previously viewed "
                "results; none is a fresh holdout. Threshold fixed from existing platform floor. "
                "Keep original and failed trials. New forward data is required for validation.")
            if strategy == "changez-1m":
                assert all(a["trades"] == b["trades"]
                           for a, b in zip(report["results"], source["results"], strict=True))
            saved = save_run(report)
            summary = {"source": source_id, "strategy": strategy,
                       "days": source["data_quality"]["scored_candles"] // 1440,
                       "run_id": saved["run_id"], "periods": [
                           {k: r[k] for k in ("period", "stress", "net", "total_fees",
                            "trade_count", "tp_hits", "sl_hits", "profit_factor")}
                           for r in saved["results"]]}
            comparisons.append(summary)
            (ROOT / "var/changez-cost-audit.json").write_text(
                json.dumps(comparisons, indent=2), encoding="utf-8")
            print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
