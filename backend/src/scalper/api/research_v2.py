"""Research v2: download once, then score offline.

The split from v1 is deliberate. v1 fetched a year of candles one UTC day at a
time and recomputed every strategy signal on every pass; v2 pulls whole months
from the public archive into a local store and computes each signal series
once. What it does NOT change is the simulation: both run the same replay over
the same candles, so a v2 report is comparable to a v1 report line for line.

Public market data only. No credentials, no orders, no trading commands.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from scalper.api.research import ResearchRequest, catalog, evaluate_candles
from scalper.api.research_store import save_run
from scalper.config import LIVE_REST
from scalper.exchange.market_limits import paced_get
from scalper.research.fast import signals as signal_cache
from scalper.research.fast import store
from scalper.research.fast.archive import ensure_range, missing_months
from scalper.research.fast.store import DataGap
from scalper.symbols import is_symbol

JOBS = Path(__file__).resolve().parents[4] / "var" / "research-v2-jobs"
FILTERS = store.ROOT / "_filters.json"
MAX_JOBS = 20

_jobs: dict[str, dict[str, Any]] = {}
_events: dict[str, threading.Event] = {}
_tasks: set[asyncio.Task[None]] = set()
_gate = asyncio.Lock()


#: Binance lists ~530 USDT perpetuals. An offline run reads the local store, so
#: the whole universe in one job is the point of the tab rather than an abuse of
#: it; the bound stays only to keep a typo from queueing unbounded work.
MAX_SYMBOLS = 600

#: The picker offers "select all", and the catalog is ~154 strategies, so a cap
#: below that turns a documented one-click action into a rejected request.
MAX_STRATEGIES = 200


class V2Request(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    strategies: list[str] = Field(min_length=1, max_length=MAX_STRATEGIES)
    days: int = Field(default=365, ge=2, le=365)
    end_time: int | None = Field(default=None, ge=0)
    capital: float = Field(default=100, gt=0, le=100000, allow_inf_nan=False)
    leverage: int = Field(default=3, ge=1, le=10)
    risk_pct: float = Field(default=1, gt=0, le=5, allow_inf_nan=False)
    fee_bps: float = Field(default=5, ge=0, le=100, allow_inf_nan=False)
    slippage_bps: float = Field(default=2, ge=0, le=100, allow_inf_nan=False)
    spread_bps: float = Field(default=2, ge=0, le=100, allow_inf_nan=False)
    minimum_trades: int = Field(default=30, ge=10, le=500)

    @field_validator("symbols")
    @classmethod
    def valid_symbols(cls, symbols: list[str]) -> list[str]:
        cleaned = [s.strip().upper() for s in symbols]
        if any(not is_symbol(s) for s in cleaned):
            raise ValueError("Choose valid USDT perpetual symbols")
        return list(dict.fromkeys(cleaned))

    @field_validator("strategies")
    @classmethod
    def supported(cls, ids: list[str]) -> list[str]:
        allowed = {row["id"] for row in catalog()}
        if any(key not in allowed for key in ids):
            raise ValueError("Choose supported candle strategies; order-flow needs event data")
        return list(dict.fromkeys(ids))


class V2DownloadRequest(BaseModel):
    """Fill the local store for these coins. No strategy is scored."""

    symbols: list[str] = Field(min_length=1, max_length=60)
    days: int = Field(default=365, ge=2, le=365)

    @field_validator("symbols")
    @classmethod
    def valid_symbols(cls, symbols: list[str]) -> list[str]:
        cleaned = [s.strip().upper() for s in symbols]
        if any(not is_symbol(s) for s in cleaned):
            raise ValueError("Choose valid USDT perpetual symbols")
        return list(dict.fromkeys(cleaned))


def persist(state: dict[str, Any]) -> None:
    JOBS.mkdir(parents=True, exist_ok=True)
    path = JOBS / f"{state['id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def refuse_if_busy(v1_gate: asyncio.Lock) -> None:
    """Refuse a second job, saying which one is in the way.

    "A research job is already running" with no way to find or stop it is a
    dead end when the job was started somewhere else.
    """
    running = active_job()
    if not (_gate.locked() or v1_gate.locked() or running):
        return
    if running:
        raise HTTPException(
            409,
            f"{running['symbol']} is already running ({running['phase']}). "
            "Wait for it or cancel it from the panel above.",
        )
    raise HTTPException(409, "A research job is already running; retry when it finishes")


def check_cancel(event: threading.Event) -> None:
    if event.is_set():
        raise InterruptedError("Cancelled; completed experiments remain saved")


async def symbol_rules(symbols: list[str]) -> dict[str, dict[str, float]]:
    """Order minimums per symbol, cached so a rerun needs no network.

    Sizing has to respect the exchange's step and notional floors or the trade
    count is fiction. These change rarely, so they are fetched once and kept;
    if the fetch fails the stored copy is used and the run still completes.
    """
    stored: dict[str, dict[str, float]] = {}
    if FILTERS.exists():
        try:
            stored = json.loads(FILTERS.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            stored = {}
    if all(symbol in stored for symbol in symbols):
        return stored
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await paced_get(client, LIVE_REST, "/fapi/v1/exchangeInfo")
            response.raise_for_status()
            for entry in response.json().get("symbols", []):
                filters = {f["filterType"]: f for f in entry.get("filters", [])}
                lot = filters.get("LOT_SIZE", {})
                notional = filters.get("MIN_NOTIONAL", {})
                stored[entry["symbol"]] = {
                    "quantity_step": float(lot.get("stepSize", 0.001)),
                    "minimum_quantity": float(lot.get("minQty", 0.0)),
                    "minimum_notional": float(notional.get("notional", 5.0)),
                }
        FILTERS.parent.mkdir(parents=True, exist_ok=True)
        FILTERS.write_text(json.dumps(stored, sort_keys=True), encoding="utf-8")
    except (httpx.HTTPError, ValueError, OSError, KeyError):
        pass
    missing = [s for s in symbols if s not in stored]
    if missing:
        raise ValueError(f"No exchange order rules for {', '.join(missing[:5])}")
    return stored


DAY_MS = 86_400_000


def runnable_strategies(symbol: str, strategy_ids: list[str]) -> tuple[list[str], list[str]]:
    """Split the chosen strategies into those this coin can run, and the rest.

    A couple of research entries are frozen single-symbol hypotheses - fitted
    on BTCUSDT and meaningless anywhere else, which is why they refuse. Asking
    before the run turns five identical mid-run failures into one sentence,
    and keeps a deliberate restriction from reading like a fault.
    """
    pinned = {
        row["id"]: {str(s).upper() for s in cast(list[str], row.get("research_symbols") or [])}
        for row in catalog()
    }
    allowed, skipped = [], []
    for strategy_id in strategy_ids:
        symbols = pinned.get(strategy_id) or set()
        (allowed if not symbols or symbol.upper() in symbols else skipped).append(strategy_id)
    return allowed, skipped


def window(request: V2Request, now_ms: int) -> tuple[int, int, int]:
    """Aligned [start, end) plus the warm-up the chosen strategies need.

    The end is floored to the last complete UTC day, not to the current
    minute. A window that ends "now" moves every sixty seconds, so two runs an
    hour apart score different candles, cannot be compared, and can never hit
    the signal cache. Anchoring to midnight makes a run reproducible for the
    rest of the day, which is both better research and what makes a rerun
    instant.
    """
    warmup = max(
        int(cast(int, row["warmup_candles"]))
        for row in catalog()
        if row["id"] in request.strategies
    )
    requested = request.end_time if request.end_time is not None else now_ms
    end = requested // DAY_MS * DAY_MS
    if end > now_ms:
        raise ValueError("End date must not be in the future")
    if end <= 0:
        raise ValueError("Choose a date after the first complete UTC day")
    start = end - (request.days * 1440 + warmup) * 60000
    if start < 0:
        raise ValueError("Requested range starts before the Unix epoch")
    return start, end, warmup


def score_symbol(
    request: V2Request,
    symbol: str,
    state: dict[str, Any],
    event: threading.Event,
    start: int,
    end: int,
    warmup: int,
    rules: dict[str, float],
) -> None:
    """Load this symbol from the store and score every chosen strategy."""
    bars = store.load_range(symbol, "1m", start, end)
    candles = store.to_candles(bars)
    runnable, skipped = runnable_strategies(symbol, list(request.strategies))
    for strategy_id in skipped:
        state["skipped"] = [
            *state.get("skipped", []),
            {
                "symbol": symbol,
                "strategy": strategy_id,
                "reason": "fitted to a single dataset; not comparable on this coin",
            },
        ]
    if not runnable:
        persist(state)
        return
    state["coverage"] = {
        "symbol": symbol,
        "candles": len(candles),
        "days": request.days,
        "warmup": warmup,
        "start": start,
        "end": end,
        "dataset_sha256": store.digest(bars),
    }
    for index, strategy in enumerate(runnable):
        check_cancel(event)
        label = f"{symbol} - {strategy} ({index + 1}/{len(runnable)})"

        def progress(done: int, total: int, label: str = label) -> None:
            state.update(phase=f"Signals: {label}", progress=round(done / total * 100, 1))
            check_cancel(event)

        state.update(phase=f"Signals: {label}", progress=0)
        series, was_cached = signal_cache.signals_for(
            bars,
            candles,
            strategy_id=strategy,
            symbol=symbol,
            start_index=warmup,
            progress=progress,
        )
        check_cancel(event)
        state.update(phase=f"Scoring: {label}", progress=0)
        config = ResearchRequest(
            csv=state["coverage"]["dataset_sha256"],
            strategy=strategy,
            warmup=warmup,
            dataset_label=symbol,
            capital=request.capital,
            leverage=request.leverage,
            risk_pct=request.risk_pct,
            fee_bps=request.fee_bps,
            slippage_bps=request.slippage_bps,
            spread_bps=request.spread_bps,
            minimum_trades=request.minimum_trades,
            **rules,
        )
        report = evaluate_candles(config, candles, None, series)
        report["dataset_sha256"] = state["coverage"]["dataset_sha256"]
        report["coverage"] = state["coverage"]
        cast(list[str], report["limitations"]).extend(
            [
                "Candles come from Binance's published archive, verified by its own checksum.",
                "Signals were computed once and reused by all six passes; costs and sizing are "
                "still simulated per pass, so results match a run without the cache.",
                "Current symbol filters introduce survivorship bias.",
            ]
        )
        saved = save_run(report)
        base = [row for row in saved["results"] if not row["stress"]]
        # Split the result into money won and money lost rather than reporting
        # only the difference. "Net -164" says nothing about whether the
        # strategy won a little often and lost a lot rarely, or the reverse,
        # and that is usually the first thing worth knowing.
        closed = [trade for row in base for trade in row.get("trades", [])]
        won = sum(trade["net"] for trade in closed if trade["net"] > 0)
        lost = -sum(trade["net"] for trade in closed if trade["net"] < 0)
        state["results"] = [
            *state["results"],
            {
                "symbol": symbol,
                "strategy": strategy,
                "run_id": saved["run_id"],
                "timeframe": saved["strategy_specification"].get("timeframe"),
                "signals": len(series),
                "signals_cached": was_cached,
                "gross_profit": sum(r["net"] + r["total_fees"] for r in base),
                "net": sum(r["net"] for r in base),
                # Won and lost are after each trade's own fees, so they differ
                # by exactly the net. Fees below is the same bill seen whole.
                "won": won,
                "lost": lost,
                # Each period starts flat with the full capital, so the money
                # actually put to work is capital x periods - without which a
                # net figure cannot be read as a return.
                "capital": request.capital,
                "periods": len(base),
                "capital_deployed": request.capital * len(base),
                "trades": sum(r["trade_count"] for r in base),
                "tp_hits": sum(r["tp_hits"] for r in base),
                "sl_hits": sum(r["sl_hits"] for r in base),
                "forced_exits": sum(r["forced_exits"] for r in base),
                "session_exits": sum(r.get("session_exits", 0) for r in base),
                "fees": sum(r["total_fees"] for r in base),
                "diagnostics": saved["diagnostics"],
            },
        ]
        persist(state)


async def run_job(key: str, request: V2Request) -> None:
    state = _jobs[key]
    event = _events[key]
    async with _gate:
        try:
            now = int(time.time() * 1000) // 60000 * 60000
            start, end, warmup = window(request, now)
            rules = await symbol_rules(request.symbols)
            state.update(start=start, end=end, warmup=warmup, errors=[], completed_symbols=0)
            persist(state)
            for index, symbol in enumerate(request.symbols):
                check_cancel(event)
                state.update(
                    current_symbol=symbol,
                    symbol_index=index + 1,
                    symbol_count=len(request.symbols),
                    phase=f"Preparing {symbol}",
                    progress=0,
                )
                try:
                    outstanding = missing_months(symbol, "1m", start, end)
                    state["phase"] = (
                        f"{symbol}: {len(outstanding)} month(s) to download"
                        if outstanding
                        else f"{symbol}: already stored"
                    )
                    persist(state)
                    report = await ensure_range(
                        symbol,
                        "1m",
                        start,
                        end,
                        now_ms=now,
                        progress=lambda phase, done, total: state.update(
                            phase=phase, progress=round(100 * done / max(total, 1), 1)
                        ),
                    )
                    state["downloads"] = [*state.get("downloads", []), report]
                    check_cancel(event)
                    # Compute every strategy's signals at once before scoring
                    # any of them. This is the slow half of a run and each
                    # strategy is independent, so doing them one at a time
                    # left most of the machine idle. The scoring loop below
                    # then finds them all cached.
                    state.update(phase=f"{symbol}: computing signals in parallel", progress=0)
                    warm_errors = await asyncio.to_thread(
                        signal_cache.warm_many,
                        symbol,
                        "1m",
                        start,
                        end,
                        runnable_strategies(symbol, list(request.strategies))[0],
                        start_index=warmup,
                        progress=lambda note, done, total: state.update(
                            phase=f"Signals ({done}/{total}) {note}",
                            progress=round(100 * done / max(total, 1), 1),
                        ),
                    )
                    for failed, message in warm_errors.items():
                        state["errors"] = [
                            *state["errors"],
                            {"symbol": f"{symbol} / {failed}", "error": message},
                        ]
                    check_cancel(event)
                    await asyncio.to_thread(
                        score_symbol,
                        request,
                        symbol,
                        state,
                        event,
                        start,
                        end,
                        warmup,
                        {
                            k: rules[symbol][k]
                            for k in ("quantity_step", "minimum_quantity", "minimum_notional")
                        },
                    )
                except (ValueError, DataGap, httpx.HTTPError) as exc:
                    state["errors"] = [*state["errors"], {"symbol": symbol, "error": str(exc)}]
                state["completed_symbols"] = index + 1
                persist(state)
            state.update(
                status="completed_with_errors" if state["errors"] else "completed",
                phase="Completed; review unavailable symbols" if state["errors"] else "Completed",
                progress=100,
            )
        except InterruptedError as exc:
            state.update(status="cancelled", phase=str(exc))
        except Exception as exc:
            # No authenticated calls happen here. Never surface raw URLs or headers.
            state.update(
                status="failed",
                phase=str(exc)
                if isinstance(exc, ValueError | DataGap)
                else f"{type(exc).__name__}: research stopped; saved results remain available.",
            )
        finally:
            _events.pop(key, None)
            try:
                persist(state)
            except OSError:
                state["phase"] += "; job summary could not be saved"


async def run_download(key: str, request: V2DownloadRequest) -> None:
    """Fill the store, one coin at a time, reporting as it goes."""
    state = _jobs[key]
    event = _events[key]
    async with _gate:
        try:
            now = int(time.time() * 1000) // 60000 * 60000
            end = now // DAY_MS * DAY_MS
            start = end - request.days * DAY_MS
            if start < 0:
                raise ValueError("Requested range starts before the Unix epoch")
            state.update(start=start, end=end, errors=[], completed_symbols=0)
            persist(state)
            for index, symbol in enumerate(request.symbols):
                check_cancel(event)
                state.update(
                    current_symbol=symbol,
                    symbol_index=index + 1,
                    symbol_count=len(request.symbols),
                    phase=f"Preparing {symbol}",
                    progress=0,
                )
                try:
                    report = await ensure_range(
                        symbol,
                        "1m",
                        start,
                        end,
                        now_ms=now,
                        progress=lambda phase, done, total: state.update(
                            phase=phase, progress=round(100 * done / max(total, 1), 1)
                        ),
                    )
                    # Prove the range really is continuous rather than trusting
                    # that the download said so; a gap here is silent later.
                    bars = store.load_range(symbol, "1m", start, end)
                    report["candles_stored"] = len(bars)
                    state["downloads"] = [*state.get("downloads", []), report]
                except (ValueError, DataGap, httpx.HTTPError) as exc:
                    state["errors"] = [*state["errors"], {"symbol": symbol, "error": str(exc)}]
                state["completed_symbols"] = index + 1
                persist(state)
            state.update(
                status="completed_with_errors" if state["errors"] else "completed",
                phase="Download complete; review unavailable coins"
                if state["errors"]
                else "Download complete",
                progress=100,
            )
        except InterruptedError as exc:
            state.update(status="cancelled", phase=str(exc))
        except Exception as exc:
            state.update(
                status="failed",
                phase=str(exc)
                if isinstance(exc, ValueError | DataGap)
                else f"{type(exc).__name__}: download stopped; stored coins remain available.",
            )
        finally:
            _events.pop(key, None)
            with contextlib.suppress(OSError):
                persist(state)


async def start_download_job(request: V2DownloadRequest) -> dict[str, Any]:
    """Download history for the chosen coins without scoring anything."""
    from scalper.api.research_jobs import _gate as v1_gate

    refuse_if_busy(v1_gate)
    while len(_jobs) >= MAX_JOBS:
        _jobs.pop(next(iter(_jobs)))
    key = uuid.uuid4().hex
    _jobs[key] = {
        "id": key,
        "version": 2,
        "kind": "download",
        "status": "running",
        "phase": "Starting",
        "progress": 0,
        "symbol": f"{len(request.symbols)} coin(s)",
        "config": request.model_dump(),
        "created_at": time.time(),
        "results": [],
        "errors": [],
        "downloads": [],
    }
    try:
        persist(_jobs[key])
    except OSError as exc:
        _jobs.pop(key, None)
        raise HTTPException(503, "Cannot save the download job; check disk availability") from exc
    _events[key] = threading.Event()
    task = asyncio.create_task(run_download(key, request))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return _jobs[key].copy()


async def start_v2_job(request: V2Request) -> dict[str, Any]:
    from scalper.api.research_jobs import _gate as v1_gate

    refuse_if_busy(v1_gate)
    while len(_jobs) >= MAX_JOBS:
        _jobs.pop(next(iter(_jobs)))
    key = uuid.uuid4().hex
    _jobs[key] = {
        "id": key,
        "version": 2,
        "kind": "backtest",
        "status": "running",
        "phase": "Starting",
        "progress": 0,
        "symbol": f"{len(request.symbols)} symbol(s)",
        "config": request.model_dump(),
        "created_at": time.time(),
        "results": [],
        "errors": [],
        "skipped": [],
        "downloads": [],
    }
    try:
        persist(_jobs[key])
    except OSError as exc:
        _jobs.pop(key, None)
        raise HTTPException(503, "Cannot save research job; check disk availability") from exc
    _events[key] = threading.Event()
    task = asyncio.create_task(run_job(key, request))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return _jobs[key].copy()


def get_v2_job(job_id: str) -> dict[str, Any]:
    if job_id in _jobs:
        return _jobs[job_id].copy()
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise HTTPException(404, "Unknown research job")
    path = JOBS / f"{job_id}.json"
    if not path.exists():
        raise HTTPException(404, "Unknown research job; completed runs remain in Saved experiments")
    state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if state.get("status") == "running":
        state.update(
            status="interrupted",
            phase="Server restarted; rerun to reuse stored candles and signals.",
        )
    return state


def cancel_v2_job(job_id: str) -> dict[str, Any]:
    state = get_v2_job(job_id)
    if job_id in _events:
        _events[job_id].set()
    return state


def active_job() -> dict[str, Any] | None:
    """The job currently holding the gate, whoever started it.

    Only one research job runs at a time. Without this the page could only
    report a 409 and the reason for it - a run started in another tab, or by a
    script, or before a reload - was invisible and uncancellable.
    """
    for state in _jobs.values():
        if state.get("status") == "running":
            return {
                "id": state["id"],
                "kind": state.get("kind", "backtest"),
                "phase": state.get("phase", ""),
                "progress": state.get("progress", 0),
                "symbol": state.get("symbol", ""),
                "version": 2,
            }
    # A v1 job takes the same gate but is not ours to cancel from here.
    from scalper.api.research_jobs import _jobs as v1_jobs

    for state in v1_jobs.values():
        if state.get("status") == "running":
            return {
                "id": state["id"],
                "kind": "backtest",
                "phase": state.get("phase", ""),
                "progress": state.get("progress", 0),
                "symbol": state.get("symbol", ""),
                "version": 1,
            }
    return None


def v2_inventory() -> dict[str, Any]:
    """What is already offline, so the page can say what a run will cost."""
    symbols = []
    if store.ROOT.is_dir():
        for directory in sorted(store.ROOT.iterdir()):
            if not directory.is_dir() or directory.name.startswith("_"):
                continue
            manifest = store.read_manifest(directory.name, "1m")
            if not manifest:
                continue
            months = sorted(manifest)
            symbols.append(
                {
                    "symbol": directory.name,
                    "months": len(months),
                    "first": months[0],
                    "last": months[-1],
                    "candles": sum(int(cast(int, m.get("candles", 0))) for m in manifest.values()),
                    "megabytes": round(
                        sum(p.stat().st_size for p in (directory / "1m").glob("*.npy")) / 1e6, 1
                    ),
                }
            )
    return {
        "symbols": symbols,
        "active_job": active_job(),
        "cached_signal_series": signal_cache.cached_count(),
        "generated_at": int(datetime.now(tz=UTC).timestamp() * 1000),
    }
