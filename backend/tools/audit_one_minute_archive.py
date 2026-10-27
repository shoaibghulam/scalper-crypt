"""Inventory every saved minute report; preserve per-run and per-period evidence."""
import csv
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from scalper.api.research import catalog

ROOT = Path(__file__).resolve().parents[2]


def main():
    known = {r['id']: r['timeframe'] for r in catalog()}
    db = sqlite3.connect(f"file:{ROOT / 'var/research.sqlite'}?mode=ro", uri=True)
    records = db.execute('SELECT id, strategy, dataset_label, report FROM runs').fetchall()
    db.close()
    reports, unknown = [], []
    for key, strategy, coin, raw in records:
        report = json.loads(raw)
        spec = report.get('strategy_specification', {})
        timeframe = spec.get('timeframe') or known.get(strategy)
        if timeframe is None:
            unknown.append(key)
        if timeframe != '1m':
            continue
        base = [r for r in report['results'] if not r.get('stress')]
        stress = [r for r in report['results'] if r.get('stress')]
        trades = [t for p in base for t in p.get('trades', [])]
        # Exact duplicate ledgers are not independent evidence. Different windows
        # can still overlap; never sum per-run outcomes into a portfolio return.
        fingerprint = hashlib.sha256(json.dumps(
            [report.get('data_quality'), report['config'], report.get('model_sha256'),
             trades], sort_keys=True).encode()).hexdigest()
        net = sum(r['net'] for r in base)
        fees = sum(r['total_fees'] for r in base)
        reports.append({
            'run_id': key, 'strategy': strategy, 'name': spec.get('name', strategy),
            'coin': coin, 'net': net, 'fees': fees, 'gross': net+fees,
            'trades': len(trades), 'wins': sum(t['net'] > 0 for t in trades),
            'losses': sum(t['net'] < 0 for t in trades),
            'stress_net': sum(r['net'] for r in stress),
            'positive_periods': sum(r['net'] > 0 for r in base),
            'period_count': len(base), 'last_period_net': base[-1]['net'] if base else None,
            'config': report['config'], 'coverage': report.get('data_quality'),
            'fingerprint': fingerprint,
            'periods': [{k: p.get(k) for k in ('period','stress','start','end','net',
                'total_fees','trade_count','tp_hits','sl_hits','closed_equity_drawdown_pct')}
                for p in report['results']],
            'sides': {side: {'trades': sum(t['side'] == side for t in trades),
                'net': sum(t['net'] for t in trades if t['side'] == side)}
                for side in ('LONG','SHORT')},
            'ledger_net_matches': abs(sum(t['net'] for t in trades)-net) < 1e-7,
        })
    alpha = []
    for path in sorted((ROOT/'var/alpha-lab').glob('*.json')):
        job = json.loads(path.read_text(encoding='utf-8'))
        result = job.get('result') or {}
        alpha.append({'id': job.get('id'), 'status': job.get('status'),
            'coin': job.get('config', {}).get('symbol'),
            'finalists': [{k: f.get(k) for k in ('method','formula','passed','status')}
                | {'periods': {name: {k: (f.get(name) or {}).get(k)
                    for k in ('net','trade_count','profit_factor')}
                    for name in ('training','validation','holdout','stress_holdout')}}
                for f in result.get('finalists', [])]})
    quant = []
    candidate_count = 0
    for path in sorted((ROOT/'var/quant-lab').glob('*/state.json')):
        job = json.loads(path.read_text(encoding='utf-8'))
        if job.get('request', {}).get('timeframe') != '1m':
            continue
        candidates = []
        for candidate_file in path.parent.glob('*-candidates.jsonl'):
            for line in candidate_file.read_text(encoding='utf-8').splitlines():
                candidate = json.loads(line)
                candidate_count += 1
                candidates.append({'coin': candidate_file.name.split('-candidates')[0],
                    'strategy_id': candidate.get('strategy_id'),
                    'score': candidate.get('score'), 'training': candidate.get('training')})
        quant.append({'id': job.get('id'), 'status': job.get('status'),
            'candidate_count': len(candidates), 'candidates': candidates,
            'results': [{'coin': result['symbol'], 'total_tested': result.get('total_tested'),
                'strategies': [{k: s.get(k)
                    for k in ('strategy_id','definition','status','reasons')}
                    | {'periods': {period: (s.get(period) or {}).get('metrics')
                        for period in ('training','validation','test','stress_test')}}
                    for s in result.get('strategies', [])]} for result in job.get('results', [])]})
    btc = [r for r in reports if r['coin']=='BTCUSDT']
    summary = {'total_sqlite_reports': len(records), 'one_minute_reports': len(reports),
        'unique_strategies': len({r['strategy'] for r in reports}),
        'coins': dict(Counter(r['coin'] for r in reports)),
        'unknown_timeframe_runs': unknown, 'btc_reports': len(btc),
        'btc_qualifying_runs': [r['run_id'] for r in btc if r['trades'] >= 30
            and r['net'] > 0 and r['stress_net'] > 0 and r['last_period_net'] > 0
            and r['positive_periods'] == r['period_count']],
        'notes': ['Ranking is retrospective and affected by multiple testing.',
                  'Reports overlap; counts and net totals across reports are not independent.',
                  'Other coins are context, not evidence of BTC profitability.']}
    summary['quant_minute_jobs'] = len(quant)
    summary['quant_minute_candidate_records'] = candidate_count
    artifact = {'summary': summary, 'reports': reports, 'alpha_jobs': alpha, 'quant_jobs': quant}
    (ROOT/'var/one-minute-archive-audit.json').write_text(json.dumps(artifact, indent=2))
    fields = ['run_id','strategy','coin','trades','wins','losses','net','fees','stress_net',
              'positive_periods','last_period_net','ledger_net_matches']
    with (ROOT/'var/one-minute-archive-audit.csv').open('w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(sorted(reports, key=lambda r:(r['coin'], -r['net'])))
    print(json.dumps(summary))
    print('Alpha jobs:',len(alpha),'BTC finalists:',sum(len(j['finalists'])
          for j in alpha if j['coin']=='BTCUSDT'))


if __name__ == '__main__':
    main()
