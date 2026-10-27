# BTC one-minute archive review

## Outcome

No strategy in this review is established as profitable. A new fixed-rule
candidate, **BTC Selective Reversion 1 Minute**, was implemented and rejected by
its 90-day comparison. It remains backtest-only. No trading was started.

## Coverage

The final inventory read 587 SQLite reports and identified 260 one-minute reports
covering 78 strategy IDs, including 82 BTCUSDT reports. These include the two
new comparisons from this audit; the initial archive contained 258 one-minute
reports and 80 BTC reports. Other research continued while this review ran.
Every base-cost trade ledger reconciled to its saved net totals.

It also read 17 Alpha Lab job files and seven one-minute Quant Lab job archives,
including 6,691 logged candidate records. Six BTC Alpha finalists failed their
saved validation criteria. Both BTC Quant jobs contained the same three rejected
finalists; they are not six independent strategies. A positive test period in
one finalist does not override its other failed validation checks.

No BTC SQLite run met all of: at least 30 total trades, positive aggregate net,
positive aggregate stress net, positive last period, and positive net in every
base-cost period. This is an audit screen, not a statistical profitability test.
Overlapping windows, duplicated experiments and different configurations make
summed archive PnL misleading; results are retained individually.

## Evidence used for the new hypothesis

The strongest BTC run with at least 30 trades was Bollinger reversal + RSI
(`user-07`): +2.12 USDT from 54 trades on a short window. It lost money under
stress costs and in its final period. Related Bollinger re-entry (`lab-28`)
also failed on a longer window. Sparse profitable examples contained too few
trades to support selection. Alpha/Quant searches did not provide a validated
alternative.

Hypothesis: retaining the Bollinger/RSI trigger while avoiding strong 5m trends
might reduce failed mean-reversion entries. The new candidate uses closed 1m
BB20/2 re-entry, RSI14 recovering toward 50, and closed 5m ADX14 below 25.
It retains the existing max(1.5 ATR14, 0.45%) stop and 2R target. No parameters
were tuned after observing these new results. Tests check parity of the
unfiltered control against the original entry rules and brackets.

## Same-data comparison

Cached BTC data from the existing 90-day Changez report was reused. Capital is
100 USDT, leverage cap 3, risk 1%, fees 5 bps per side, slippage 2 bps per side,
spread 2 bps, minimum notional 50 USDT, quantity step/minimum 0.001 BTC.
Stress doubles spread and slippage. Each of three chronological periods starts
again at 100 USDT: sums below are three independent period outcomes, not a
single compounded account return.

| Candidate | Base-cost trades | Base-cost net USDT | Stress net USDT |
| --- | ---: | ---: | ---: |
| Bollinger/RSI control | 663 | -90.38 | -116.51 |
| BTC Selective Reversion | 417 | -68.84 | -85.25 |

Selective base-cost period results: -37.23, -20.49, -11.12 USDT. Reduced total
loss does not establish better expectancy or profitability; there is simply
less participation, and every period still loses. The candidate failed the
predeclared requirement of positive base/stress results and at least 30 trades
in each period. It must not be promoted to trading from this evidence.

All data here was previously available/inspected. This is retrospective
research, not an untouched holdout. Selecting further filters from these
outcomes would add selection bias. Future data remains necessary; no
profitable future result can be promised.

## Saved evidence

- Full inventory: `var/one-minute-archive-audit.json`
- Sortable report table: `var/one-minute-archive-audit.csv`
- Frozen comparison protocol: `var/btc-selective-protocol.json`
- Comparison summary: `var/btc-selective-comparison.json`
- SQLite control report: `fce83ef1a60d45bc8ee094fcb4ce2124`
- SQLite new-candidate report: `0cb7c3e3d38446c8abf726bb5f685052`
- Reproduction scripts: `backend/tools/audit_one_minute_archive.py` and
  `backend/tools/evaluate_btc_selective.py`

The new candidate is registered as `btc-selective-reversion-1m`. Its historical
API evaluation requires the BTCUSDT dataset label. User-supplied CSV labels are
assertions, not cryptographic evidence of a dataset's symbol. Simulation retains
the existing documented limitations: OHLC intrabar uncertainty, funding and
liquidation omissions, and supplied execution-cost assumptions.
