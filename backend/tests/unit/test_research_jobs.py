import threading
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from scalper.api import research_jobs as jobs


def request(**kwargs):
    return jobs.AnnualRequest(symbol='ETHUSDT', strategies=['lab-01'], **kwargs)


def test_limits_and_strategy_validation():
    assert request().days == 365
    with pytest.raises(ValidationError):
        request(days=366)
    with pytest.raises(ValidationError):
        jobs.AnnualRequest(symbol='ETHUSDT', strategies=['unknown'])
    assert jobs.AnnualRequest(symbol='ETHUSDT', strategies=['lab-01', 'lab-01']).strategies == ['lab-01']


@pytest.mark.asyncio
async def test_download_full_coverage_and_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'CACHE', tmp_path)
    monkeypatch.setattr(jobs, 'catalog', lambda: [{'id': 'lab-01', 'warmup_candles': 500}])
    market = AsyncMock()
    market.__aenter__.return_value = market
    market.symbol_filters.return_value = {'ETHUSDT': SimpleNamespace(min_notional=Decimal(5), step_size=Decimal('.01'), min_qty=Decimal('.01'))}
    async def get(path, params=None):
        if path.endswith('/time'):
            return {'serverTime': 500 * 86400000}
        return [[t, '100', '102', '99', '101', '10', t+59999, '1000', 1, '6']
                for t in range(params['startTime'], params['endTime']+1, 60000)]
    market._get.side_effect = get
    monkeypatch.setattr(jobs, 'MarketDataClient', lambda _: market)
    state = {}
    bars, metadata = await jobs.download(request(days=2), state, threading.Event())
    assert len(bars) == 2*1440+500
    assert bars[-1].close_time+1 == metadata['end']
    assert bars[0].open_time == metadata['start']
    assert bars[0].taker_buy_base == 6
    calls = market._get.call_count
    reused = {}
    again, same = await jobs.download(request(days=2), reused, threading.Event())
    assert reused['cache_hits'] == state['cache_misses']
    assert reused['cache_misses'] == 0
    assert market._get.call_count == calls + 1  # Server time only; daily candles cached.
    assert same['dataset_sha256'] == metadata['dataset_sha256']
    assert again == bars
    event = threading.Event(); event.set()
    with pytest.raises(InterruptedError):
        await jobs.download(request(days=2), {}, event)
    # A year is requested explicitly; abort after its first download to inspect range.
    for path in tmp_path.glob('*.json'):
        path.unlink()
    requested = []
    async def missing(path, params=None):
        if path.endswith('/time'):
            return {'serverTime': 500 * 86400000}
        requested.append(params)
        return []
    market._get.side_effect = missing
    with pytest.raises(ValueError, match='Full requested history'):
        await jobs.download(request(), {}, threading.Event())
    expected_start = 500*86400000 - (365*1440+500)*60000
    assert requested[0]['startTime'] == expected_start // 86400000 * 86400000


@pytest.mark.asyncio
async def test_failure_and_cancel_leave_results_and_release_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, 'JOBS_PATH', tmp_path)
    key = 'unit-job'
    jobs._jobs[key] = {'id': key, 'status': 'running', 'results': [{'run_id': 'saved'}]}
    jobs._events[key] = threading.Event()
    async def fail(*args):
        raise ValueError('Full requested history is unavailable')
    monkeypatch.setattr(jobs, 'download', fail)
    market = AsyncMock()
    market.__aenter__.return_value = market
    market._get.return_value = {'serverTime': 500 * 86400000}
    monkeypatch.setattr(jobs, 'MarketDataClient', lambda _: market)
    try:
        await jobs.run_job(key, request())
        assert jobs.get_job(key)['status'] == 'failed'
        assert jobs.get_job(key)['results'] == [{'run_id': 'saved'}]
        assert not jobs._gate.locked()
        assert key not in jobs._events
        jobs._jobs[key]['status'] = 'running'
        with pytest.raises(HTTPException) as exc:
            await jobs.start_job(request())
        assert exc.value.status_code == 409
        jobs._events[key] = threading.Event()
        jobs.cancel_job(key)
        assert jobs._events[key].is_set()
    finally:
        jobs._jobs.pop(key, None); jobs._events.pop(key, None)


def test_job_manifest_survives_restart_without_resuming_trades(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'JOBS_PATH', tmp_path)
    key = 'a' * 32
    jobs.persist({'id': key, 'status': 'running', 'results': [{'tp_hits': 5, 'sl_hits': 3}]})
    restored = jobs.get_job(key)
    assert restored['status'] == 'interrupted'
    assert restored['results'][0]['tp_hits'] == 5
    with pytest.raises(HTTPException):
        jobs.get_job('../credentials')


@pytest.mark.asyncio
async def test_top20_batch_freezes_dates_continues_missing_history(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'JOBS_PATH', tmp_path)
    market = AsyncMock()
    market.__aenter__.return_value = market
    symbols = [f'COIN{i:02}USDT' for i in range(23)]
    market.symbol_filters.return_value = dict.fromkeys(symbols[:22])
    market.tickers.return_value = {s: SimpleNamespace(symbol=s, quote_volume=1000-i) for i, s in enumerate(symbols)}
    market._get.return_value = {'serverTime': 500*86400000+59999}
    monkeypatch.setattr(jobs, 'MarketDataClient', lambda _: market)
    seen = []
    async def download(req, state, event):
        seen.append((req.symbol, req.end_time))
        if req.symbol == symbols[4]:
            raise ValueError('Full requested history is unavailable')
        return [], {}
    def execute(req, state, event, bars, metadata):
        state['results'] = [*state['results'], {'symbol': req.symbol, 'strategy': 'lab-01'}]
    monkeypatch.setattr(jobs, 'download', download)
    monkeypatch.setattr(jobs, 'execute', execute)
    key = 'b'*32
    jobs._jobs[key] = {'id': key, 'status': 'running', 'results': []}
    jobs._events[key] = threading.Event()
    try:
        await jobs.run_job(key, request(coin_mode='top20'))
        state = jobs.get_job(key)
        assert state['symbols'] == symbols[:20]
        assert len(seen) == 20
        assert {end for _, end in seen} == {500*86400000}
        assert state['status'] == 'completed_with_errors'
        assert len(state['results']) == 19
        assert state['errors'][0]['symbol'] == symbols[4]
        assert state['completed_coins'] == 20
    finally:
        jobs._jobs.pop(key, None)
        jobs._events.pop(key, None)


def test_universe_excludes_invalid_inactive_and_zero_volume():
    tickers = {s: SimpleNamespace(symbol=s, quote_volume=v) for s, v in
               [('BTCUSDT', 100), ('ETHUSDT', 100), ('ZEROUSDT', 0), ('BADUSDT', float('nan')), ('INACTIVEUSDT', 900)]}
    assert jobs.select_top_coins(dict.fromkeys(['BTCUSDT', 'ETHUSDT', 'ZEROUSDT', 'BADUSDT']), tickers) == ['BTCUSDT', 'ETHUSDT']


def test_result_keeps_timeframe_and_base_profit_without_double_counting_stress(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'JOBS_PATH', tmp_path)
    report = {'strategy_specification': {'timeframe': '1m'}, 'limitations': [], 'diagnostics': {},
              'results': [dict(stress=stress, net=net, trade_count=1, tp_hits=1, sl_hits=0,
                               forced_exits=0, total_fees=1) for stress, net in [(False, 5), (True, -9)]]}
    monkeypatch.setattr(jobs, 'evaluate_candles', lambda *args: report)
    monkeypatch.setattr(jobs, 'save_run', lambda result: {**result, 'run_id': 'saved'})
    state = {'id': 'test', 'results': []}
    jobs.execute(request(), state, threading.Event(), [], {'warmup': 500, 'dataset_sha256': 'hash',
                 'minimum_notional': 5, 'quantity_step': .001, 'minimum_quantity': .001})
    row = state['results'][0]
    assert row['timeframe'] == '1m'
    assert row['net'] == 5
    assert row['gross_profit'] == 6
    assert row['fees'] == 1
    assert row['trades'] == 1

@pytest.mark.parametrize('count', [1, 2, 3, 25])
@pytest.mark.asyncio
async def test_custom_coins_run_exact_selection(count, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'JOBS_PATH', tmp_path)
    market = AsyncMock()
    market.__aenter__.return_value = market
    market._get.return_value = {'serverTime': 500 * 86400000}
    monkeypatch.setattr(jobs, 'MarketDataClient', lambda _: market)
    symbols = [f'COIN{i}USDT' for i in range(count)]
    download = AsyncMock(return_value=([], {}))
    monkeypatch.setattr(jobs, 'download', download)
    monkeypatch.setattr(jobs, 'execute', lambda *args: None)
    key = 'custom-test'
    jobs._jobs[key] = {'id': key, 'status': 'running', 'results': []}
    jobs._events[key] = threading.Event()
    try:
        await jobs.run_job(key, request(coin_mode='custom', symbols=symbols))
        assert jobs._jobs[key]['status'] == 'completed'
        assert jobs._jobs[key]['coin_count'] == count
        assert [call.args[0].symbol for call in download.call_args_list] == symbols
        assert {call.args[0].end_time for call in download.call_args_list} == {500 * 86400000}
        market.tickers.assert_not_called()
    finally:
        jobs._jobs.pop(key, None)
        jobs._events.pop(key, None)


def test_custom_selection_validation():
    for symbols in ([], ['../BTCUSDT'], ['BTC']):
        with pytest.raises(ValidationError):
            request(coin_mode='custom', symbols=symbols)
    assert request(coin_mode='custom', symbols=['BTCUSDT', 'BTCUSDT']).symbols == ['BTCUSDT']
