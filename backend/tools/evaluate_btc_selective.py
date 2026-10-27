"""Two frozen hypotheses, same cached 90-day BTC dataset, all results saved."""
import hashlib
import json
from pathlib import Path

from audit_changez import cached_bars

from scalper.api.research import ResearchRequest, evaluate_candles
from scalper.api.research_store import load_run, save_run
from scalper.strategies.btc_selective import BtcSelectiveReversion
from scalper.strategies.research_registry import RESEARCH_STRATEGIES

ROOT = Path(__file__).resolve().parents[2]


def main():
    source = load_run('fe95afb5635d4ad780e93761d72cea28')
    bars = cached_bars(source)
    baseline = BtcSelectiveReversion()
    baseline.id = 'btc-bb-rsi-audit-baseline-1m'
    baseline.name = 'BTC Bollinger RSI Baseline (Audit)'
    baseline.use_regime = False
    baseline.assumptions = (*baseline.assumptions,
                           'Audit control: ADX filter disabled; matches user-07 entry rules.')
    RESEARCH_STRATEGIES[baseline.id] = baseline
    digest = hashlib.sha256(json.dumps([[b.open_time,b.open,b.high,b.low,b.close,b.volume]
                                      for b in bars]).encode()).hexdigest()
    protocol = {'dataset_source': source['run_id'], 'dataset_sha256': digest,
                'strategies': [baseline.id, BtcSelectiveReversion.id],
                'parameters_frozen': True, 'fresh_holdout': False,
                'selection': 'No tuning after results; compare three chronological periods '
                             'and doubled spread/slippage. All previously inspected periods.',
                'acceptance': 'Positive net in every period at base and stress costs; '
                              'at least 30 trades per period. Otherwise research only.'}
    (ROOT/'var/btc-selective-protocol.json').write_text(json.dumps(protocol, indent=2))
    summaries = []
    for strategy in protocol['strategies']:
        print('Evaluating '+strategy, flush=True)
        request = ResearchRequest(csv='cached:'+digest,
            **{**source['config'], 'strategy': strategy})
        pass_number = 0
        def progress(done, total):
            nonlocal pass_number
            if done == 0:
                pass_number += 1
                print(f'Pass {pass_number}/6', flush=True)
        report = evaluate_candles(request, bars, progress=progress)
        report['dataset_sha256'] = digest
        report['archive_research_protocol'] = protocol
        report['research_acceptance_passed'] = all(
            r['net'] > 0 and r['trade_count'] >= 30 for r in report['results'])
        saved = save_run(report)
        summary = {'strategy':strategy,'run_id':saved['run_id'],
                   'accepted':report['research_acceptance_passed'],
                   'periods':[{k:r[k] for k in ('period','stress','net','trade_count',
                        'tp_hits','sl_hits','total_fees','closed_equity_drawdown_pct')}
                        for r in report['results']]}
        summaries.append(summary)
        (ROOT/'var/btc-selective-comparison.json').write_text(json.dumps(summaries, indent=2))
        print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
