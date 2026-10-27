"""Compare genuine opposite-side execution against the screenshot's saved run."""
import hashlib
import json
from pathlib import Path

from audit_changez import cached_bars

from scalper.api.research import ResearchRequest, evaluate_candles
from scalper.api.research_store import load_run, save_run

ROOT = Path(__file__).resolve().parents[2]


def main():
    source = load_run('05fc3e0420de435db019e614cebe57b3')
    bars = cached_bars(source)
    digest = hashlib.sha256(json.dumps([[b.open_time,b.open,b.high,b.low,b.close,b.volume]
                                      for b in bars]).encode()).hexdigest()
    summaries = []
    for strategy in ('btc-selective-reversion-1m', 'btc-selective-reversed-1m'):
        print('Testing '+strategy, flush=True)
        request = ResearchRequest(csv='cached:'+digest,
            **{**source['config'], 'strategy': strategy})
        report = evaluate_candles(request,bars)
        report['dataset_sha256'] = digest
        report['comparison_source_run'] = source['run_id']
        report['research_note'] = (
            'User-requested reversal on previously seen BTC data. Same signal conditions, '
            'opposite direction; original target becomes stop and stop becomes target, '
            'relative to actual reversed fill. Independent risk sizing, not relabelled results. '
            'Retrospective comparison, not a fresh holdout or profitability guarantee.')
        if strategy == source['config']['strategy']:
            assert all(a['trades']==b['trades'] for a,b in
                       zip(report['results'],source['results'],strict=True))
        saved = save_run(report)
        summary = {'strategy':strategy,'run_id':saved['run_id'],
            'periods':[{k:r[k] for k in ('period','stress','net','trade_count','tp_hits',
                        'sl_hits','forced_exits','total_fees','rejected_entries')}
                       for r in report['results']]}
        summaries.append(summary)
        (ROOT/'var/btc-reversed-comparison.json').write_text(json.dumps(summaries,indent=2))
        print(json.dumps(summary),flush=True)


if __name__ == '__main__':
    main()
