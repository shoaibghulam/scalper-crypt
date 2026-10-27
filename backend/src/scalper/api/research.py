"""Bounded, offline research endpoint. Never queues trading commands."""

import asyncio
import csv
import hashlib
import io
import json
from collections.abc import Callable, Mapping
from itertools import pairwise
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from scalper.api.research_metrics import diagnostics
from scalper.api.research_store import save_run
from scalper.backtest import replay
from scalper.exchange.market_data import Candle
from scalper.strategies import STRATEGIES
from scalper.strategies.research_registry import RESEARCH_STRATEGIES

_gate = asyncio.Lock()
_source_root = Path(__file__).resolve().parents[1]
_model_files = [
    *sorted((_source_root / "strategies").glob("*.py")),
    _source_root / "backtest.py",
    _source_root / "indicators" / "core.py",
    Path(__file__),
    Path(__file__).with_name("research_metrics.py"),
]
MODEL_SHA256 = hashlib.sha256(
    b"".join(
        str(path.relative_to(_source_root)).encode() + path.read_bytes() for path in _model_files
    )
).hexdigest()


class ResearchRequest(BaseModel):
    csv: str = Field(min_length=1, max_length=2_000_000)
    strategy: str = Field(max_length=80)
    warmup: int = Field(default=500, ge=500, le=50_000)
    capital: float = Field(default=10, gt=0, le=100000, allow_inf_nan=False)
    leverage: int = Field(default=3, ge=1, le=10)
    fee_bps: float = Field(default=5, ge=0, le=100, allow_inf_nan=False)
    slippage_bps: float = Field(default=2, ge=0, le=100, allow_inf_nan=False)
    spread_bps: float = Field(default=2, ge=0, le=100, allow_inf_nan=False)
    minimum_notional: float = Field(default=5, gt=0, le=10000, allow_inf_nan=False)
    minimum_quantity: float = Field(default=0, ge=0, allow_inf_nan=False)
    quantity_step: float = Field(gt=0, le=100000, allow_inf_nan=False)
    risk_pct: float = Field(default=1, gt=0, le=5, allow_inf_nan=False)
    minimum_trades: int = Field(default=30, ge=10, le=500)
    dataset_label: str = Field(default="CSV", max_length=100)

    @field_validator("strategy")
    @classmethod
    def supported(cls, value: str) -> str:
        if value in RESEARCH_STRATEGIES:
            return value
        if value not in STRATEGIES.ids() or STRATEGIES.get(value).requires_order_flow:
            raise ValueError("Unsupported candle research strategy")
        return value


def catalog() -> list[dict[str, object]]:
    """Every strategy a backtest can run, and what it needs.

    `research_symbols` is published because a couple of these are frozen
    single-symbol hypotheses. Without it the page cannot tell an operator that
    five of their six coins will be refused, and they find out one coin at a
    time after the download has already finished.
    """
    return [
        {
            **s.describe(),
            "warmup_candles": max(500, s.history_5m * 5),
            "research_symbols": list(getattr(s, "research_symbols", ())),
        }
        for s in RESEARCH_STRATEGIES.values()
    ] + [
        {
            **STRATEGIES.get(key).describe(),
            "research_only": False,
            "timeframe": f"{STRATEGIES.get(key).signal_interval_s // 60}m",
            "warmup_candles": max(500, STRATEGIES.get(key).history_5m * 5),
            "research_symbols": list(getattr(STRATEGIES.get(key), "research_symbols", ())),
        }
        for key in STRATEGIES.ids()
        if key not in RESEARCH_STRATEGIES and not STRATEGIES.get(key).requires_order_flow
    ]


def evaluate(request: ResearchRequest) -> dict[str, object]:
    reader = csv.DictReader(io.StringIO(request.csv.lstrip("\ufeff")))
    required = {"open_time", "open", "high", "low", "close", "volume"}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError("CSV needs open_time, open, high, low, close, volume")
    candles: list[Candle] = []
    for row in reader:
        if len(candles) >= 10000:
            raise ValueError("Maximum 10,000 candles per run")
        timestamp = int(row["open_time"])
        candles.append(
            Candle(
                timestamp,
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["volume"]),
                timestamp + 59999,
                0,
                0,
            )
        )
    return evaluate_candles(request, candles)


def evaluate_candles(request: ResearchRequest, candles: list[Candle],
                     progress: Callable[[int, int], None] | None = None,
                     signals: Mapping[int, str] | None = None) -> dict[str, object]:
    """Score a strategy over three periods, each at stated and stressed costs.

    `signals` is the v2 fast path: a signal series computed once for these
    candles and reused by all six passes. It decides only which bars trade, so
    the six passes still simulate their own entries, sizes and exits and the
    report is identical to one produced without it.
    """
    selected = (
        RESEARCH_STRATEGIES[request.strategy]
        if request.strategy in RESEARCH_STRATEGIES
        else STRATEGIES.get(request.strategy)
    )
    allowed_symbols = getattr(selected, "research_symbols", ())
    if allowed_symbols and request.dataset_label not in allowed_symbols:
        raise ValueError(f"This research strategy requires dataset: {', '.join(allowed_symbols)}")
    warmup = max(request.warmup, selected.history_5m * 5)
    if len(candles) < 1200:
        raise ValueError("Provide at least 1,200 consecutive one-minute candles")
    if len(candles) < warmup + 700:
        raise ValueError(f"This comparison needs at least {warmup + 700} candles including warm-up")
    # Warm-up bars are history only. Fixed parameters, no fitting or selection.
    boundaries = [warmup + (len(candles) - warmup) * n // 3 for n in range(4)]
    results = []
    for n, (start, end) in enumerate(pairwise(boundaries)):
        for stress in (False, True):
            result = replay(
                candles[max(0, start - warmup) : end],
                start_index=warmup,
                progress=progress,
                signals=signals,
                strategy_id=request.strategy,
                initial=request.capital,
                risk_fraction=request.risk_pct / 100,
                leverage=request.leverage,
                fee_rate=request.fee_bps / 10000,
                slippage_bps=request.slippage_bps * (2 if stress else 1),
                spread_bps=request.spread_bps * (2 if stress else 1),
                minimum_notional=request.minimum_notional,
                quantity_step=request.quantity_step,
                minimum_quantity=request.minimum_quantity,
            )
            results.append(
                {
                    "period": n + 1,
                    "stress": stress,
                    "start": candles[start].open_time,
                    "end": candles[end - 1].close_time,
                    **result,
                }
            )
    config = request.model_dump(exclude={"csv"})
    return {
        "version": 3,
        "diagnostics": diagnostics(results, request.minimum_trades),
        "data_quality": {"consecutive": True, "interval": "1m", "first": candles[0].open_time,
                         "last": candles[-1].close_time, "scored_candles": len(candles) - warmup},
        "warmup_candles": warmup,
        "model_sha256": MODEL_SHA256,
        "config": config,
        "candles": len(candles),
        "strategy_specification": next(s for s in catalog() if s["id"] == request.strategy),
        "dataset_sha256": hashlib.sha256(request.csv.encode()).hexdigest(),
        "experiment_sha256": hashlib.sha256(
            (MODEL_SHA256 + json.dumps(request.model_dump(), sort_keys=True)).encode()
        ).hexdigest(),
        "results": results,
        "limitations": [
            "Fixed-rule chronological evaluation; no model training or parameter optimization.",
            "Each period starts flat with the same capital; final open trades close at period end.",
            "Drawdown uses closed trades only; intratrade losses can be larger.",
            "No funding, liquidation, historical liquidity, partial fills or full scanner gates.",
            "EMA IOC fills approximate open-price execution, not historical order fills.",
            "Costs and order minimums are supplied assumptions, not verified exchange history.",
            "Cost stress doubles spread and slippage. Results do not establish live profitability.",
            "Multiple trials create selection bias. Save all trials and reserve new data.",
        ],
    }


async def backtest(request: ResearchRequest) -> dict[str, object]:
    if _gate.locked():
        raise HTTPException(429, "A research run is already active; retry after it finishes")
    async with _gate:
        # Shield work so a disconnected browser cannot start overlapping runs.
        task = asyncio.create_task(asyncio.to_thread(lambda: save_run(evaluate(request))))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            raise HTTPException(422, str(exc)) from exc
