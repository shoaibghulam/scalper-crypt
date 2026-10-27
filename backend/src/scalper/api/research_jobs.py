"""Long-running public-data research. No account credentials or order calls."""
import asyncio
import hashlib
import json
import math
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from scalper.api.research import ResearchRequest, _gate, catalog, evaluate_candles
from scalper.api.research_store import save_run
from scalper.config import LIVE_REST
from scalper.exchange.market_data import Candle, MarketDataClient
from scalper.symbols import SYMBOL_PATTERN, is_symbol

CACHE = Path(__file__).resolve().parents[4] / 'var' / 'research-candles'
JOBS_PATH = CACHE.parent / 'research-jobs'
_jobs: dict[str, dict[str, Any]] = {}
_events: dict[str, threading.Event] = {}
_tasks: set[asyncio.Task[None]] = set()


class AnnualRequest(BaseModel):
    coin_mode: Literal["single", "top20", "custom"] = "single"
    symbol: str = Field(default='BTCUSDT', pattern=SYMBOL_PATTERN)
    symbols: list[str] = Field(default_factory=list)
    strategies: list[str] = Field(min_length=1, max_length=200)
    days: int = Field(default=365, ge=2, le=365)
    end_time: int | None = Field(default=None, ge=0)
    capital: float = Field(default=100, gt=0, le=100000, allow_inf_nan=False)
    leverage: int = Field(default=3, ge=1, le=10)
    risk_pct: float = Field(default=1, gt=0, le=5, allow_inf_nan=False)
    fee_bps: float = Field(default=5, ge=0, le=100, allow_inf_nan=False)
    slippage_bps: float = Field(default=2, ge=0, le=100, allow_inf_nan=False)
    spread_bps: float = Field(default=2, ge=0, le=100, allow_inf_nan=False)
    minimum_trades: int = Field(default=30, ge=10, le=500)

    @field_validator('symbols')
    @classmethod
    def valid_symbols(cls, symbols: list[str]) -> list[str]:
        if any(not is_symbol(symbol) for symbol in symbols):
            raise ValueError('Choose valid USDT perpetual symbols')
        return list(dict.fromkeys(symbols))

    @model_validator(mode='after')
    def custom_selection(self) -> 'AnnualRequest':
        if self.coin_mode == 'custom' and not self.symbols:
            raise ValueError('Select at least one coin')
        return self

    @field_validator('strategies')
    @classmethod
    def supported(cls, ids: list[str]) -> list[str]:
        allowed = {row['id'] for row in catalog()}
        if any(key not in allowed for key in ids):
            raise ValueError('Choose supported candle strategies; order-flow needs event data')
        return list(dict.fromkeys(ids))


def persist(state: dict[str, Any]) -> None:
    JOBS_PATH.mkdir(parents=True, exist_ok=True)
    path = JOBS_PATH / f"{state['id']}.json"
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, allow_nan=False), encoding='utf-8')
    tmp.replace(path)


def check_cancel(event: threading.Event) -> None:
    if event.is_set():
        raise InterruptedError('Cancelled; completed experiments remain saved')


async def download(request: AnnualRequest, state: dict[str, Any], event: threading.Event, *, minimum_warmup: int = 0) -> tuple[list[Candle], dict[str, Any]]:
    CACHE.mkdir(parents=True, exist_ok=True)
    state.update(cache_hits=0, cache_misses=0)
    warmup = max(int(cast(int, row['warmup_candles'])) for row in catalog() if row['id'] in request.strategies)
    warmup = max(warmup, minimum_warmup)
    async with MarketDataClient(LIVE_REST) as market:
        rules = await market.symbol_filters()
        if request.symbol not in rules:
            raise ValueError('Symbol is not an active USDT perpetual')
        now = int((await market._get('/fapi/v1/time'))['serverTime']) // 60000 * 60000
        end = request.end_time // 60000 * 60000 if request.end_time is not None else now
        if end > now:
            raise ValueError('End date must not be in the future')
        start = end - (request.days * 1440 + warmup) * 60000
        if start < 0:
            raise ValueError('Requested range starts before the Unix epoch')
        bars: list[Candle] = []
        cursor = start
        digest = hashlib.sha256()
        while cursor < end:
            check_cancel(event)
            # Cache aligned UTC days so overlapping experiments reuse data.
            page_start = cursor // 86400000 * 86400000
            page_end = min(page_start + 86400000, end)
            cache = CACHE / f'{request.symbol}-{page_start}-{page_end}.json'
            if cache.exists():
                raw = json.loads(cache.read_text(encoding='utf-8'))
                state['cache_hits'] += 1
            else:
                state['cache_misses'] += 1
                raw = await market._get('/fapi/v1/klines', {
                    'symbol': request.symbol, 'interval': '1m', 'startTime': page_start,
                    'endTime': page_end - 1, 'limit': 1500})
            if not isinstance(raw, list):
                raise ValueError('Invalid historical response')
            page = [Candle.from_raw(row) for row in raw if cursor <= int(row[0]) < page_end]
            for bar in page:
                if (bar.open_time != cursor or bar.close_time != cursor + 59999
                    or bar.volume < 0 or bar.low > min(bar.open, bar.close)
                    or bar.high < max(bar.open, bar.close)):
                    raise ValueError('History has missing or invalid candles; incomplete years are not reported as full years')
                bars.append(bar)
                cursor += 60000
            if cursor != page_end:
                raise ValueError('Full requested history is unavailable (new listing or missing candles); choose a shorter period')
            encoded = json.dumps(raw, separators=(',', ':'))
            digest.update(json.dumps([[b.open_time, b.open, b.high, b.low, b.close,
                                       b.volume, b.close_time, b.quote_volume, b.taker_buy_base]
                                      for b in page], separators=(",", ":")).encode())
            if not cache.exists():
                tmp = cache.with_suffix('.tmp')
                tmp.write_text(encoded, encoding='utf-8')
                tmp.replace(cache)
            state.update(phase='Downloading candles', progress=round(100 * (cursor-start)/(end-start), 1),
                         downloaded=len(bars), expected=(end-start)//60000)
        rule = rules[request.symbol]
        return bars, {'warmup': warmup, 'start': start, 'end': end, 'dataset_sha256': digest.hexdigest(),
                      'minimum_notional': float(rule.min_notional), 'quantity_step': float(rule.step_size),
                      'minimum_quantity': float(rule.min_qty)}


def execute(request: AnnualRequest, state: dict[str, Any], event: threading.Event,
            bars: list[Candle], metadata: dict[str, Any]) -> None:
    state['coverage'] = {**metadata, 'candles': len(bars), 'days': request.days}
    for index, strategy in enumerate(request.strategies):
        check_cancel(event)
        state.update(phase=f'Testing {strategy} ({index+1}/{len(request.strategies)})', progress=0)
        pass_number = 0
        def progress(done: int, total: int) -> None:
            nonlocal pass_number
            if done == 0:
                pass_number += 1
            state["phase"] = f"Testing {strategy} ({index+1}/{len(request.strategies)}), pass {pass_number}/6"
            check_cancel(event)
            state['progress'] = round(done / total * 100, 1)
        config = ResearchRequest(csv=metadata['dataset_sha256'], strategy=strategy,
            warmup=metadata['warmup'], dataset_label=request.symbol,
            **request.model_dump(exclude={'symbol', 'strategies', 'days', 'end_time', 'coin_mode', 'symbols'}),
            **{key: metadata[key] for key in ('minimum_notional', 'quantity_step', 'minimum_quantity')})
        report = evaluate_candles(config, bars, progress)
        report['dataset_sha256'] = metadata['dataset_sha256']
        report['coverage'] = state['coverage']
        cast(list[str], report['limitations']).append('One requested year is 365 days, plus warm-up. Current symbol filters introduce survivorship bias.')
        saved = save_run(report)
        results = saved['results']
        base = [row for row in results if not row['stress']]
        summary = {'symbol': request.symbol, 'strategy': strategy, 'run_id': saved['run_id'],
                   'timeframe': saved['strategy_specification'].get('timeframe'),
                   'gross_profit': sum(row['net'] + row['total_fees'] for row in base),
                   'net': sum(row['net'] for row in base),
                   'trades': sum(row['trade_count'] for row in base),
                   'tp_hits': sum(row['tp_hits'] for row in base),
                   'sl_hits': sum(row['sl_hits'] for row in base),
                   'forced_exits': sum(row['forced_exits'] for row in base),
                   'session_exits': sum(row.get('session_exits', 0) for row in base),
                   'fees': sum(row['total_fees'] for row in base),
                   'diagnostics': saved['diagnostics']}
        state['results'] = [*state['results'], summary]
        persist(state)


def select_top_coins(rules: dict[str, Any], tickers: dict[str, Any]) -> list[str]:
    eligible = [ticker for symbol, ticker in tickers.items()
                if symbol in rules and is_symbol(symbol)
                and math.isfinite(ticker.quote_volume) and ticker.quote_volume > 0]
    return [ticker.symbol for ticker in sorted(eligible, key=lambda t: (-t.quote_volume, t.symbol))[:20]]


async def run_job(key: str, request: AnnualRequest) -> None:
    state = _jobs[key]
    async with _gate:
        try:
            symbols = (request.symbols if request.coin_mode == 'custom'
                       else [request.symbol])
            # Freeze end time and universe once so every coin uses identical dates.
            async with MarketDataClient(LIVE_REST) as market:
                if request.coin_mode == 'top20':
                    rules = await market.symbol_filters()
                    tickers = await market.tickers()
                    symbols = select_top_coins(rules, tickers)
                    if len(symbols) < 20:
                        raise ValueError('Fewer than 20 eligible markets; no partial universe was started')
                end = request.end_time
                if end is None:
                    end = int((await market._get('/fapi/v1/time'))['serverTime']) // 60000 * 60000
            state.update(symbols=symbols, end_time=end, errors=[], completed_coins=0)
            persist(state)
            for index, symbol in enumerate(symbols):
                check_cancel(_events[key])
                state.update(current_coin=symbol, coin_index=index+1, coin_count=len(symbols),
                             phase=f'Preparing {symbol}', progress=0, downloaded=0)
                coin_request = request.model_copy(update={'symbol': symbol, 'end_time': end})
                try:
                    bars, metadata = await download(coin_request, state, _events[key])
                    await asyncio.to_thread(execute, coin_request, state, _events[key], bars, metadata)
                    del bars
                except ValueError as exc:
                    state['errors'] = [*state['errors'], {'symbol': symbol, 'error': str(exc)}]
                    if request.coin_mode == 'single':
                        raise
                state['completed_coins'] = index+1
                persist(state)
            state.update(status='completed_with_errors' if state['errors'] else 'completed',
                         phase='Completed; review unavailable coins' if state['errors'] else 'Completed', progress=100)
        except InterruptedError as exc:
            state.update(status='cancelled', phase=str(exc))
        except Exception as exc:
            # No authenticated requests occur here. Never return raw HTTP URLs/headers.
            state.update(status='failed', phase=str(exc) if isinstance(exc, ValueError)
                         else f'{type(exc).__name__}: research stopped; retry later. Saved results remain available.')
        finally:
            _events.pop(key, None)
            try:
                persist(state)
            except OSError:
                state['phase'] += '; job summary could not be saved; individual reports remain in the archive'


async def start_job(request: AnnualRequest) -> dict[str, Any]:
    if _gate.locked() or any(row['status'] == 'running' for row in _jobs.values()):
        raise HTTPException(409, 'A research job is already running')
    while len(_jobs) >= 20:
        _jobs.pop(next(iter(_jobs)))
    key = uuid.uuid4().hex
    label = request.symbol
    basis = 'User-selected coins'
    if request.coin_mode == 'top20':
        label = 'Top 20 coins'
        basis = 'Current 24h quote volume; not historical rank or a ready-to-trade signal'
    elif request.coin_mode == 'custom':
        label = f'{len(request.symbols)} selected coins'
    _jobs[key] = {'id': key, 'status': 'running', 'phase': 'Starting', 'progress': 0,
                  'symbol': label,
                  'config': request.model_dump(), 'selection_basis': basis,
                  'created_at': time.time(), 'results': []}
    try:
        persist(_jobs[key])
    except OSError as exc:
        _jobs.pop(key, None)
        raise HTTPException(503, 'Cannot save research job; check disk availability') from exc
    _events[key] = threading.Event()
    task = asyncio.create_task(run_job(key, request))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return _jobs[key].copy()


def get_job(job_id: str) -> dict[str, Any]:
    if job_id not in _jobs:
        if len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
            raise HTTPException(404, 'Unknown research job')
        path = JOBS_PATH / f'{job_id}.json'
        if not path.exists():
            raise HTTPException(404, 'Unknown research job; completed experiments remain in Saved experiments')
        state: dict[str, Any] = json.loads(path.read_text(encoding='utf-8'))
        if state['status'] == 'running':
            state.update(status='interrupted', phase='Server restarted; rerun to reuse cached candles. Completed results are preserved.')
        return state
    return _jobs[job_id].copy()


def cancel_job(job_id: str) -> dict[str, Any]:
    state = get_job(job_id)
    if job_id in _events:
        _events[job_id].set()
    return state


def capabilities() -> dict[str, Any]:
    from scalper.strategies.pdf_advanced_five_minute import UNAVAILABLE_STRATEGIES

    return {"top20": True, "custom": True, "unavailable_strategies": list(UNAVAILABLE_STRATEGIES)}
