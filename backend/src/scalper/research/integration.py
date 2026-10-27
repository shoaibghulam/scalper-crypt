"""Validated export and paper-only bridge to the existing strategy interface."""

from decimal import Decimal

from scalper.research.data.loader import digest
from scalper.research.data.models import INTERVALS, Bar, DataRequest, Dataset
from scalper.research.features import Features
from scalper.research.ml import predict
from scalper.research.schema import SearchConfig, StrategyDefinition
from scalper.strategies.base import Levels, Setup, Strategy


def export_strategy(report, coin, result):
    return {
        "schema_version": 1,
        "strategy_id": result["strategy_id"],
        "status": "research_passed" if result["status"] == "research_passed" else "rejected",
        "symbol": coin["symbol"],
        "strategy_definition": result["definition"],
        "experiment_id": report["id"],
        "dataset_sha256": coin["dataset_sha256"],
        "search_config": report["request"]["search"],
        "model": result["model"],
        "backtest_metrics": result["training"]["metrics"],
        "validation_metrics": result["validation"]["metrics"],
        "unseen_test_metrics": result["test"]["metrics"],
        "stress_test_metrics": result.get("stress_test", {}).get("metrics"),
        "walk_forward_metrics": result["walk_forward"],
        "robustness_metrics": result["robustness"],
        "reasons": result["reasons"],
        "live_enabled": False,
        "execution_contract": "paper-only candle simulation; not registered with live execution",
    }


def validated_export(artifact):
    if artifact.get("schema_version") != 1 or artifact.get("live_enabled") is not False:
        raise ValueError("Unsupported export or forbidden live activation")
    definition = StrategyDefinition.model_validate(artifact["strategy_definition"])
    if artifact["strategy_id"] != definition.strategy_id:
        raise ValueError("Strategy identity mismatch")
    if artifact["status"] not in {"research_passed", "paper_approved"}:
        raise ValueError("Rejected research cannot be approved for paper trading")
    SearchConfig.model_validate(artifact["search_config"])
    return definition


class ResearchStrategy(Strategy):
    """Bot-interface adapter for inspection/paper tooling, intentionally not live.

    A separate human-reviewed execution adapter is necessary before live use:
    the existing engine does not implement this rule DSL's exits and trailing.
    """

    backtest_only = True
    manual_only = True

    def __init__(self, artifact):
        self.definition = validated_export(artifact)
        self.artifact = artifact
        self.id = f"quant_{self.definition.strategy_id}"
        self.name = f"Researched {artifact['symbol']} {self.definition.timeframe}"
        self.signal_interval_s = INTERVALS[self.definition.timeframe] // 1000
        self.history_5m = 0
        self.history_1m = 400 * self.signal_interval_s // 60
        self.candle_limit_1m = self.history_1m

    def assess(self, ctx):
        from scalper.research.data.loader import aggregate

        if ctx.symbol != self.artifact["symbol"] or len(ctx.candles_1m) < self.history_1m:
            return Setup.waiting("Wrong symbol or insufficient research warm-up")
        interval = INTERVALS[self.definition.timeframe]
        source = ctx.candles_1m
        start = ((source[0].open_time + interval - 1) // interval) * interval
        end = (source[-1].open_time + 60000) // interval * interval
        try:
            bars = [
                Bar(
                    timestamp=c.open_time,
                    available_at=c.open_time + 60000,
                    open=c.open,
                    high=c.high,
                    low=c.low,
                    close=c.close,
                    volume=c.volume,
                )
                for c in source
                if start <= c.open_time < end
            ]
            dataset = Dataset(
                request=DataRequest(
                    symbol=ctx.symbol, timeframe=self.definition.timeframe, start=start, end=end
                ),
                source="bot-context",
                source_sha256=digest([b.model_dump() for b in bars]),
                bars=tuple(aggregate(bars, self.definition.timeframe)),
            )
            features = Features(dataset)
            ready = bool(features.signals(self.definition)[0][-1])
            if self.artifact.get("model"):
                model = self.artifact["model"]
                ready &= bool(predict(model, features.ml_matrix())[-1] >= model["threshold"])
        except ValueError as exc:
            return Setup.waiting(f"Invalid research data: {exc}")
        return (
            Setup.entry(self.definition.direction, "Research rule matched; paper only")
            if ready
            else Setup.waiting("Research conditions not met")
        )

    def levels(self, ctx, entry, direction):
        sign = Decimal(1 if direction == "long" else -1)
        risk = self.definition.risk
        return Levels(
            stop=entry * (1 - sign * Decimal(str(risk.stop_loss))),
            target=entry * (1 + sign * Decimal(str(risk.take_profit))),
        )
