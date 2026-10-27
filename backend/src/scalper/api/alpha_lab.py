"""Bounded discovery jobs. Public candles only; no engine commands or credentials."""

import asyncio
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from scalper.alpha_lab import WARMUP, DiscoveryRequest, check, discover, hardware
from scalper.api.research import _gate
from scalper.api.research_jobs import AnnualRequest, download

STORE = Path(__file__).resolve().parents[4] / "var" / "alpha-lab"
_states: dict[str, dict[str, Any]] = {}
_events: dict[str, threading.Event] = {}
_tasks: set[asyncio.Task[None]] = set()


def persist(state: dict[str, Any]) -> None:
    STORE.mkdir(parents=True, exist_ok=True)
    path = STORE / f"{state['id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


async def capabilities() -> dict[str, Any]:
    return {**await asyncio.to_thread(hardware), "multi_coin_limit": 1000}


def get(job_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(404, "Unknown discovery job")
    if job_id in _states:
        return _states[job_id].copy()
    path = STORE / f"{job_id}.json"
    if not path.exists():
        raise HTTPException(404, "Unknown discovery job")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Saved discovery report could not be read") from exc
    if state["status"] in {"running", "queued"}:
        state.update(
            status="interrupted", phase="API restarted. Start a new run to reuse cached candles."
        )
    return state


def history() -> list[dict[str, Any]]:
    paths = sorted(STORE.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
    rows = []
    for path in paths:
        state = get(path.stem)
        rows.append({k: state[k] for k in ("id", "status", "created_at", "config", "phase")})
    return rows


def cancel(job_id: str) -> dict[str, Any]:
    state = get(job_id)
    if job_id in _events:
        _events[job_id].set()
        _states[job_id]["phase"] = "Cancellation requested"
    return state


async def run(job_id: str, request: DiscoveryRequest) -> None:
    state, event = _states[job_id], _events[job_id]
    try:
        async with _gate:
            check(event)
            state.update(status="running", phase="Checking compute device")
            if request.method != "gp":
                device = await capabilities()
                if not device["torch"]:
                    raise ValueError(
                        "PyTorch is not installed. Install the Alpha Lab optional dependencies."
                    )
                if request.device == "cuda" and not device["cuda"]:
                    raise ValueError("CUDA is unavailable. Choose CPU or install CUDA PyTorch.")
                state["hardware"] = device
            symbols = request.symbols or [request.symbol]
            state.update(coin_results=[], errors=[], completed_coins=0, coin_count=len(symbols))
            frozen_end = request.end_time
            for index, symbol in enumerate(symbols):
                check(event)
                state.update(
                    current_coin=symbol,
                    coin_index=index + 1,
                    history=[],
                    phase=f"Preparing {symbol}",
                    progress=100 * index / len(symbols),
                )
                coin_state: dict[str, Any] = {}

                def update(
                    values: dict[str, Any], symbol: str = symbol, index: int = index
                ) -> None:
                    state.update(values)
                    if "phase" in values:
                        state["phase"] = f"{symbol} ({index + 1}/{len(symbols)}): {values['phase']}"
                    if "progress" in values:
                        state["progress"] = round(
                            100 * (index + values["progress"] / 100) / len(symbols), 1
                        )

                coin_request = request.model_copy(
                    update={"symbol": symbol, "symbols": [], "end_time": frozen_end}
                )
                try:
                    data_request = AnnualRequest(
                        symbol=symbol,
                        strategies=["ema-trend-pullback"],
                        days=request.days,
                        end_time=frozen_end,
                    )
                    state["phase"] = f"Downloading {symbol} ({index + 1}/{len(symbols)})"
                    bars, metadata = await download(
                        data_request, coin_state, event, minimum_warmup=WARMUP
                    )
                    frozen_end = metadata["end"]
                    state["end_time"] = frozen_end
                    coin_request = coin_request.model_copy(update={"end_time": frozen_end})
                    update({"phase": "Building closed-candle features", "progress": 0})
                    result = await asyncio.to_thread(
                        discover, coin_request, bars, metadata, event, update
                    )
                    check(event)
                    state["coin_results"] = [
                        *state["coin_results"],
                        {"symbol": symbol, "result": result},
                    ]
                    if len(symbols) == 1:
                        state["result"] = result
                    del bars
                except Exception as exc:
                    if isinstance(exc, InterruptedError):
                        raise
                    if len(symbols) == 1:
                        raise
                    message = (
                        str(exc)
                        if isinstance(exc, ValueError)
                        else f"{type(exc).__name__}: coin research failed"
                    )
                    state["errors"] = [*state["errors"], {"symbol": symbol, "error": message}]
                state["completed_coins"] = index + 1
                persist(state)
            passes = sum(
                f["passed"] for row in state["coin_results"] for f in row["result"]["finalists"]
            )
            state.update(
                status="completed_with_errors" if state["errors"] else "completed",
                phase=(
                    f"Search finished: {passes} passing candidates across "
                    f"{len(state['coin_results'])} tested coins"
                ),
                progress=100,
                passed_count=passes,
            )
    except InterruptedError as exc:
        state.update(status="cancelled", phase=str(exc))
    except Exception as exc:
        state.update(
            status="failed",
            phase=str(exc)
            if isinstance(exc, ValueError)
            else f"{type(exc).__name__}: discovery failed. Check memory and public data access.",
        )
    finally:
        _events.pop(job_id, None)
        state["finished_at"] = time.time()
        try:
            persist(state)
        except OSError:
            state.update(
                status="failed",
                phase="Could not save report. Check disk space; copy the result before restarting.",
            )


async def start(request: DiscoveryRequest) -> dict[str, Any]:
    if _gate.locked() or _events:
        raise HTTPException(409, "A research job is already running; wait or cancel it first")
    while len(_states) >= 20:
        _states.pop(next(iter(_states)))
    key = uuid.uuid4().hex
    state = {
        "id": key,
        "status": "queued",
        "phase": "Queued",
        "progress": 0,
        "created_at": time.time(),
        "config": request.model_dump(),
        "history": [],
    }
    try:
        persist(state)
    except OSError as exc:
        raise HTTPException(503, "Cannot save discovery job; check disk availability") from exc
    _states[key] = state
    _events[key] = threading.Event()
    task = asyncio.create_task(run(key, request))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return state.copy()
