"""Local formula discovery. Original implementation; no generated Python execution.

The neural sampler uses REINFORCE, not AlphaGen's PPO. Both neural search and
genetic search receive only training rewards. Validation selects one finalist
per method before either sees holdout. All fills use the platform's replay.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
import threading
from collections import deque
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from scalper.backtest import replay
from scalper.exchange.market_data import Candle
from scalper.strategies.base import Levels, Setup, Strategy, StrategyContext
from scalper.symbols import SYMBOL_PATTERN, is_symbol

FEATURES = (
    "return_1",
    "ema_9_20",
    "ema_20_50",
    "rolling_vwap_50",
    "rsi_14",
    "macd_histogram",
    "bollinger_20",
    "stoch_rsi",
    "relative_volume",
    "atr_relative",
    "adx_direction",
    "trend_5m",
    "trend_15m",
    "trend_30m",
    "trend_1h",
    "zero",
    "one",
)
OPS = ("add", "subtract", "multiply", "minimum", "maximum")
CARDINALITIES = (5, 5, len(FEATURES), len(FEATURES), 5, len(FEATURES), len(FEATURES), 3)
THRESHOLDS = (0.5, 1.0, 1.5)
WARMUP = 3600  # 60 completed hourly bars, plus shorter indicators.
EMBARGO = 60


class DiscoveryRequest(BaseModel):
    symbol: str = Field(default="BTCUSDT", pattern=SYMBOL_PATTERN)
    symbols: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator("symbols")
    @classmethod
    def valid_symbols(cls, values: list[str]) -> list[str]:
        if any(not is_symbol(value) for value in values):
            raise ValueError("Choose valid USDT perpetual symbols")
        return list(dict.fromkeys(values))

    method: Literal["compare", "rl", "gp"] = "compare"
    device: Literal["cuda", "cpu"] = "cuda"
    days: int = Field(default=30, ge=7, le=90)
    end_time: int | None = Field(default=None, ge=0)
    population: int = Field(default=16, ge=8, le=64)
    generations: int = Field(default=6, ge=2, le=30)
    seed: int = Field(default=42, ge=0, le=2147483647)
    capital: float = Field(default=100, gt=0, le=100000, allow_inf_nan=False)
    leverage: int = Field(default=3, ge=1, le=10)
    fee_bps: float = Field(default=5, ge=0, le=100, allow_inf_nan=False)
    slippage_bps: float = Field(default=2, ge=0, le=100, allow_inf_nan=False)
    spread_bps: float = Field(default=2, ge=0, le=100, allow_inf_nan=False)
    minimum_trades: int = Field(default=30, ge=10, le=500)


def check(event: threading.Event) -> None:
    if event.is_set():
        raise InterruptedError("Discovery cancelled; no candidate was activated for trading")


def hardware() -> dict[str, Any]:
    try:
        import torch
    except ImportError:
        return {
            "torch": None,
            "cuda": False,
            "gpu": None,
            "note": "Install the optional CUDA PyTorch dependency to train neural formulas.",
        }
    available = torch.cuda.is_available()
    return {
        "torch": torch.__version__,
        "cuda": available,
        "gpu": torch.cuda.get_device_name(0) if available else None,
        "memory_gb": round(torch.cuda.get_device_properties(0).total_memory / 2**30, 1)
        if available
        else None,
    }


def bounded(value: float) -> float:
    return max(-3.0, min(3.0, value))


def feature_rows(
    bars: list[Candle], event: threading.Event
) -> tuple[list[list[float]], list[float]]:
    """Streaming causal features, using only complete higher-timeframe bars.

    EMA first-value seeds are discarded by the 3,600-minute warmup. VWAP is a
    rolling 50-bar VWAP, not tied to New York or a UTC session reset.
    """
    rows: list[list[float]] = []
    atrs: list[float] = []
    windows: deque[Candle] = deque(maxlen=50)
    rsi_window: deque[float] = deque(maxlen=14)
    stoch_window: deque[float] = deque(maxlen=3)
    averages = {n: bars[0].close for n in (9, 12, 20, 26, 50)}
    higher = {n: {"count": 0, "bucket": -1, "fast": None, "slow": None} for n in (5, 15, 30, 60)}
    tr_smooth = gain = loss = plus = minus = adx = macd_signal = 0.0
    for i, bar in enumerate(bars):
        if i % 1000 == 0:
            check(event)
        prev = bars[max(0, i - 1)]
        change = bar.close - prev.close
        tr = max(bar.high - bar.low, abs(bar.high - prev.close), abs(bar.low - prev.close))
        up, down = bar.high - prev.high, prev.low - bar.low
        tr_smooth += (tr - tr_smooth) / 14
        gain += (max(change, 0) - gain) / 14
        loss += (max(-change, 0) - loss) / 14
        plus += ((up if up > down and up > 0 else 0) - plus) / 14
        minus += ((down if down > up and down > 0 else 0) - minus) / 14
        dx = abs(plus - minus) / max(plus + minus, 1e-12)
        adx += (dx - adx) / 14
        atr = max(tr_smooth, bar.close * 1e-8)
        for n in averages:
            averages[n] += 2 / (n + 1) * (bar.close - averages[n])
        macd = averages[12] - averages[26]
        macd_signal += 0.2 * (macd - macd_signal)
        rsi = gain / (gain + loss) if gain + loss > 0 else 0.5
        rsi_window.append(rsi)
        lo, hi = min(rsi_window), max(rsi_window)
        stoch_window.append((rsi - lo) / (hi - lo) if hi > lo else 0.5)
        windows.append(bar)
        recent = list(windows)
        bb = recent[-20:]
        mean = sum(b.close for b in bb) / len(bb)
        std = math.sqrt(sum((b.close - mean) ** 2 for b in bb) / len(bb))
        volume = sum(b.volume for b in recent)
        vwap = (
            sum((b.high + b.low + b.close) / 3 * b.volume for b in recent) / volume
            if volume
            else bar.close
        )
        avg_volume = sum(b.volume for b in bb) / len(bb)
        trends = []
        for minutes, state in higher.items():
            bucket = bar.open_time // (minutes * 60000)
            if bucket != state["bucket"]:
                state.update(bucket=bucket, count=0)
            state["count"] += 1
            if (bar.close_time + 1) % (minutes * 60000) == 0 and state["count"] == minutes:
                state["fast"] = (
                    bar.close
                    if state["fast"] is None
                    else state["fast"] + 2 / 21 * (bar.close - state["fast"])
                )
                state["slow"] = (
                    bar.close
                    if state["slow"] is None
                    else state["slow"] + 2 / 51 * (bar.close - state["slow"])
                )
            trends.append(0 if state["fast"] is None else (state["fast"] - state["slow"]) / atr)
        rows.append(
            [
                bounded(v)
                for v in (
                    change / atr,
                    (averages[9] - averages[20]) / atr,
                    (averages[20] - averages[50]) / atr,
                    (bar.close - vwap) / atr,
                    (rsi - 0.5) * 2,
                    (macd - macd_signal) / atr,
                    (bar.close - mean) / max(2 * std, 1e-12),
                    2 * (sum(stoch_window) / len(stoch_window) - 0.5),
                    bar.volume / avg_volume - 1 if avg_volume else 0,
                    atr / bar.close * 100,
                    adx * (1 if plus > minus else -1),
                    *trends,
                    0,
                    1,
                )
            ]
        )
        atrs.append(atr)
    return rows, atrs


def validate_genome(tokens: tuple[int, ...]) -> None:
    if len(tokens) != len(CARDINALITIES) or any(
        type(t) is not int or not 0 <= t < n for t, n in zip(tokens, CARDINALITIES, strict=True)
    ):
        raise ValueError("Invalid formula tokens")


def operation(op: int, a: float, b: float) -> float:
    if op == 0:
        return bounded(a + b)
    if op == 1:
        return bounded(a - b)
    if op == 2:
        return bounded(a * b)
    return min(a, b) if op == 3 else max(a, b)


def formula(tokens: tuple[int, ...]) -> str:
    validate_genome(tokens)
    root, left, a, b, right, c, d, threshold = tokens
    return (
        f"{OPS[root]}({OPS[left]}({FEATURES[a]}, {FEATURES[b]}), "
        f"{OPS[right]}({FEATURES[c]}, {FEATURES[d]})); threshold={THRESHOLDS[threshold]}"
    )


def scores(tokens: tuple[int, ...], rows: list[list[float]]) -> list[float]:
    validate_genome(tokens)
    root, left, a, b, right, c, d, _ = tokens
    return [
        operation(root, operation(left, row[a], row[b]), operation(right, row[c], row[d]))
        for row in rows
    ]


class FormulaStrategy(Strategy):
    id = "alpha-lab-formula"
    name = "Alpha Lab research formula"
    backtest_only = True
    history_5m = 1
    history_1m = 2
    candle_limit_1m = 2

    def __init__(
        self, bars: list[Candle], values: list[float], atrs: list[float], threshold: float
    ):
        self.signals = {}
        for i in range(1, len(bars)):
            direction = (
                "LONG"
                if values[i] > threshold and values[i - 1] <= threshold
                else "SHORT"
                if values[i] < -threshold and values[i - 1] >= -threshold
                else None
            )
            self.signals[bars[i].close_time] = (direction, atrs[i])

    def assess(self, ctx: StrategyContext) -> Setup:
        direction, _ = self.signals.get(ctx.candles_1m[-1].close_time, (None, 0))
        return (
            Setup.entry(direction, "Formula crossed threshold")
            if direction
            else Setup.waiting("No crossing")
        )

    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        _, atr = self.signals[ctx.candles_1m[-1].close_time]
        distance = max(Decimal(str(atr * 1.5)), entry * Decimal("0.0045"))
        sign = 1 if direction == "LONG" else -1
        return Levels(entry - sign * distance, entry + sign * distance * 2)

    def scheduled_exit(self, opened_at_ms: int) -> int:
        return opened_at_ms + 60 * 60000


def partitions(size: int, warmup: int) -> dict[str, tuple[int, int]]:
    usable = size - warmup
    a, b = warmup + int(usable * 0.6), warmup + int(usable * 0.8)
    if usable < 1000 or b + EMBARGO >= size:
        raise ValueError("Not enough candles for independent evaluation windows")
    return {"train": (warmup, a), "validation": (a + EMBARGO, b), "holdout": (b + EMBARGO, size)}


def reward(report: dict[str, Any], minimum: int) -> float:
    if report["trade_count"] < minimum:
        return -100.0 - (minimum - report["trade_count"]) / minimum
    return max(-100, min(100, report["return_pct"] - report["closed_equity_drawdown_pct"]))


class NeuralSampler:
    """Small autoregressive GRU policy, trained with a batch reward baseline."""

    def __init__(self, device: str, seed: int):
        import torch
        from torch import nn

        self.torch = torch
        if device == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA unavailable; install CUDA PyTorch or choose CPU")
        self.device = device
        torch.manual_seed(seed)
        self.embedding = nn.Embedding(32, 32).to(device)
        self.cell = nn.GRUCell(32, 64).to(device)
        self.head = nn.Linear(64, 32).to(device)
        self.parameters = [
            *self.embedding.parameters(),
            *self.cell.parameters(),
            *self.head.parameters(),
        ]
        self.optimizer = torch.optim.Adam(self.parameters, lr=0.003)

    def sample(self, count: int) -> list[tuple[int, ...]]:
        torch = self.torch
        h = torch.zeros(count, 64, device=self.device)
        prev = torch.full((count,), 31, dtype=torch.long, device=self.device)
        tokens, logs, entropies = [], [], []
        for cardinality in CARDINALITIES:
            h = self.cell(self.embedding(prev), h)
            distribution = torch.distributions.Categorical(logits=self.head(h)[:, :cardinality])
            prev = distribution.sample()
            logs.append(distribution.log_prob(prev))
            entropies.append(distribution.entropy())
            tokens.append(prev)
        self.log_probs = torch.stack(logs).sum(0)
        self.entropy = torch.stack(entropies).mean()
        return [tuple(row) for row in torch.stack(tokens, 1).cpu().tolist()]

    def learn(self, rewards: list[float]) -> float:
        torch = self.torch
        values = torch.tensor(rewards, device=self.device)
        advantage = (values - values.mean()) / values.std(unbiased=False).clamp_min(1e-6)
        loss = -(self.log_probs * advantage).mean() - 0.01 * self.entropy
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.parameters, 1)
        self.optimizer.step()
        return float(loss.detach().cpu())


def discover(
    request: DiscoveryRequest,
    bars: list[Candle],
    metadata: dict[str, Any],
    event: threading.Event,
    update: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    check(event)
    rows, atrs = feature_rows(bars, event)
    warmup = max(WARMUP, metadata["warmup"])
    windows = partitions(len(bars), warmup)
    methods = ["rl", "gp"] if request.method == "compare" else [request.method]
    cache: dict[tuple[tuple[int, ...], str, bool], dict[str, Any]] = {}
    histories: list[dict[str, Any]] = []
    finalists = []
    tested = 0

    def evaluate(tokens: tuple[int, ...], period: str, stress: bool = False) -> dict[str, Any]:
        check(event)
        key = tokens, period, stress
        if key in cache:
            return cache[key]
        start, end = windows[period]
        # Feature construction is causal; slicing prevents any future score
        # or candle from being visible to this replay or formula instance.
        prefix = max(0, start - 10)
        segment = bars[prefix:end]
        values = scores(tokens, rows[prefix:end])
        strategy = FormulaStrategy(segment, values, atrs[prefix:end], THRESHOLDS[tokens[-1]])
        result = replay(
            segment,
            strategy_id=strategy.id,
            research_strategy=strategy,
            start_index=start - prefix,
            initial=request.capital,
            risk_fraction=0.01,
            leverage=request.leverage,
            fee_rate=request.fee_bps / 10000,
            slippage_bps=request.slippage_bps * (2 if stress else 1),
            spread_bps=request.spread_bps * (2 if stress else 1),
            minimum_notional=metadata["minimum_notional"],
            minimum_quantity=metadata["minimum_quantity"],
            quantity_step=metadata["quantity_step"],
            progress=lambda *_: check(event),
        )
        # Keep bounded summaries while searching; full trades for final reports.
        summary = {k: v for k, v in result.items() if k not in {"trades", "equity_curve"}}
        if period == "holdout":
            summary["trades"] = result["trades"]
            summary["equity_curve"] = result["equity_curve"]
        cache[key] = summary
        return summary

    for method in methods:
        rng = random.Random(request.seed)
        sampler = NeuralSampler(request.device, request.seed) if method == "rl" else None
        population = [
            tuple(rng.randrange(n) for n in CARDINALITIES) for _ in range(request.population)
        ]
        archive: dict[tuple[int, ...], float] = {}
        for generation in range(request.generations):
            check(event)
            if sampler:
                population = sampler.sample(request.population)
            rewards = []
            for index, tokens in enumerate(population):
                result = evaluate(tokens, "train")
                score = reward(result, request.minimum_trades)
                archive[tokens] = score
                rewards.append(score)
                tested += 1
                update(
                    {
                        "phase": (
                            f"{method.upper()} generation {generation + 1}/{request.generations}, "
                            f"candidate {index + 1}/{request.population}"
                        ),
                        "progress": round(
                            tested / (len(methods) * request.generations * request.population) * 80,
                            1,
                        ),
                        "evaluations": tested,
                        "unique_training_formulas": len(
                            {key[0] for key in cache if key[1] == "train"}
                        ),
                    }
                )
            loss = sampler.learn(rewards) if sampler else None
            histories.append(
                {
                    "method": method,
                    "generation": generation + 1,
                    "best_training_score": max(rewards),
                    "policy_loss": loss,
                }
            )
            update({"history": list(histories)})
            if not sampler:
                ranked = sorted(archive, key=lambda t: (-archive[t], t))
                elites = ranked[: max(2, request.population // 4)]
                population = elites[:2]
                while len(population) < request.population:
                    a, b = rng.choice(elites), rng.choice(elites)
                    child = [a[j] if rng.random() < 0.5 else b[j] for j in range(len(a))]
                    for j, n in enumerate(CARDINALITIES):
                        if rng.random() < 0.25:
                            child[j] = rng.randrange(n)
                    population.append(tuple(child))
        shortlist = sorted(archive, key=lambda t: (-archive[t], t))[:5]
        update(
            {"phase": f"{method.upper()}: validation of five training finalists", "progress": 85}
        )
        selected = max(
            shortlist, key=lambda t: (reward(evaluate(t, "validation"), request.minimum_trades), t)
        )
        finalists.append(
            {
                "method": method,
                "tokens": list(selected),
                "formula": formula(selected),
                "training": evaluate(selected, "train"),
                "validation": evaluate(selected, "validation"),
                "unique_formulas": len(archive),
            }
        )
    # Selection is now frozen for ALL methods. Never sort/select on holdout.
    for finalist in finalists:
        check(event)
        update({"phase": f"Final untouched holdout: {finalist['method'].upper()}", "progress": 95})
        tokens = tuple(finalist["tokens"])
        finalist["holdout"] = evaluate(tokens, "holdout")
        finalist["stress_holdout"] = evaluate(tokens, "holdout", stress=True)
        evaluated = [finalist[k] for k in ("training", "validation", "holdout", "stress_holdout")]
        finalist["passed"] = all(
            r["net"] > 0 and r["trade_count"] >= request.minimum_trades for r in evaluated
        )
        finalist["status"] = (
            "Passed initial research checks; needs forward testing"
            if finalist["passed"]
            else "Rejected: loss or insufficient trades"
        )
    digest = hashlib.sha256(
        Path(__file__).read_bytes() + Path(__file__).with_name("backtest.py").read_bytes()
    ).hexdigest()
    return {
        "finalists": finalists,
        "history": histories,
        "config": request.model_dump(),
        "model_sha256": digest,
        "coverage": metadata,
        "windows": {
            k: {"start": bars[a].open_time, "end": bars[b - 1].close_time + 1, "candles": b - a}
            for k, (a, b) in windows.items()
        },
        "protocol": (
            "60% train / 20% validation / 20% holdout; 60-minute gaps; "
            "top five per method validated, one frozen finalist per method"
        ),
        "limitations": [
            "Research only; no generated formula is added to live trading.",
            "Repeated runs on the same holdout turn it into development data. "
            "Use fresh dates for confirmation.",
            "Funding, order-book liquidity, latency and liquidation are not simulated.",
            "Drawdown uses closed-trade equity, not intratrade mark-to-market equity.",
            "1-minute threshold crossings, 1% planned risk, minimum 0.45%/1.5 ATR stop, "
            "2R target, 60-minute maximum hold.",
            "Stress doubles spread and slippage. Fees are charged on both sides.",
        ],
    }
