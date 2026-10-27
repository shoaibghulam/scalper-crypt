from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from scalper.api import research_store
from scalper.api.research import evaluate
from scalper.api.research_metrics import diagnostics
from scalper.api.history import HistoryRequest, fetch_history
from tests.unit.test_research import request
from tests.unit.test_black_replay import aligned
from tests.unit.test_strategy_black import candle, long_setup
from scalper.backtest import replay


def test_validation_results_cannot_change_development_verdict():
    rows = [dict(period=p, stress=s, net=10, closed_equity_drawdown_pct=2,
                 trades=[{'net': 1}] * 20) for p in (1, 2, 3) for s in (False, True)]
    before = diagnostics(rows, 30)
    changed = deepcopy(rows)
    for row in changed:
        if row['period'] == 3:
            row['net'] = -999
            row['trades'] = [{'net': -999}]
    after = diagnostics(changed, 30)
    assert before['status'] == after['status'] == 'shortlist_candidate'
    assert before['development_stress'] == after['development_stress']
    assert after['holdout']['net'] == -999
    assert diagnostics(rows, 50)['status'] == 'insufficient_trades'


def test_replay_exposes_ambiguity_and_equity_without_future_changes():
    bars = aligned(long_setup() + [candle(110.30, 115, 105, 110.30)])
    result = replay(bars)
    assert result['ambiguous_bars'] == 1
    assert result['max_consecutive_losses'] == 1
    assert result['equity_curve'][-1]['equity'] == result['final_equity']
    assert result['forced_exits'] == 0


def test_archive_preserves_identical_repeated_trials_and_safe_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(research_store, 'RUNS_PATH', tmp_path / 'research.sqlite')
    report = evaluate(request())
    first = research_store.save_run(report)
    second = research_store.save_run(report)
    assert first['run_id'] != second['run_id']
    assert len(research_store.list_runs()) == 2
    assert research_store.load_run(first['run_id']) == first
    assert first['experiment_sha256'] == second['experiment_sha256']
    with pytest.raises(HTTPException) as exc:
        research_store.load_run("' OR 1=1 --")
    assert exc.value.status_code == 404


async def test_custom_end_date_is_used_and_future_date_rejected():
    from decimal import Decimal
    from types import SimpleNamespace
    market = AsyncMock()
    market.symbol_filters.return_value = {'ETHUSDT': SimpleNamespace(
        min_notional=Decimal(5), step_size=Decimal('.01'), min_qty=Decimal('.01'))}
    async def get(path, params=None):
        if path.endswith('/time'):
            return {'serverTime': 5000 * 60000}
        assert params['endTime'] == 3000 * 60000 - 1
        return [[t, 100, 101, 99, 100, 1, t + 59999, 100, 1, .5]
                for t in range(params['startTime'], params['startTime'] + params['limit'] * 60000, 60000)]
    market._get.side_effect = get
    result = await fetch_history(HistoryRequest(symbol='ETHUSDT', candles=2000, end_time=3000*60000), market)
    assert result['start'] == 1000 * 60000
    assert result['candles'] == 2000
    with pytest.raises(ValueError, match='future'):
        await fetch_history(HistoryRequest(symbol='ETHUSDT', candles=2000, end_time=6000*60000), market)


def test_tested_strategies_includes_full_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(research_store, 'RUNS_PATH', tmp_path / 'research.sqlite')
    assert research_store.tested_strategies() == []
    research_store.save_run({'config': {'strategy': 'old-strategy'}})
    for _ in range(51):
        research_store.save_run({'config': {'strategy': 'new-strategy'}})
    assert len(research_store.list_runs()) == 50
    assert research_store.tested_strategies() == ['new-strategy', 'old-strategy']
