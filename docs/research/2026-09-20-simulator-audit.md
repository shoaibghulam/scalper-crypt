# Strategy Research simulator audit — 20 September 2026

## Scope and conclusion

Audited the saved DOGEUSDT 1-minute, 120-day experiment
`866ea1ecb3764c498a5dce3c18d6747e` and its cached 172,800-candle dataset.
This concerns the Strategy Research Lab simulator, not an audit of every live
strategy or the separate historical backtest engine.

All nine finalist/stage replays reproduced the saved trades. Independent ledger
calculations reconciled quantity × direction × (exit − entry) − fees − funding
against recorded trade P/L and ending equity (largest balance error below
0.000000000001 USDT). This rules out an accounting discrepancy in these records;
it does not establish realistic exchange execution or strategy profitability.

Machine-readable evidence, fixed baseline definitions, settings, dataset hash,
and the future evaluation protocol: `var/quant-audit-20260920.json`.

## Findings and fixes

- Added rejection of strategy/dataset timeframe mismatches. Previously these could
  run and use the strategy timeframe to annualize metrics incorrectly. The supplied
  experiment already matches 1m, so this was not its loss cause.
- Two finalists differed only in one ATR period (4 versus 3). They generated
  identical validation and test trades, but different training trades. This is
  observed behavioral duplication, not evidence of a broken indicator.
- Finalist selection now reserves slots for distinct validation equity/trade
  paths. Duplicate identities remain in `selection_audit` and are identified in
  `validation_duplicates`. No holdout results are used for this decision.
- Historical reports are preserved and are not silently rewritten.

## Cost and sizing diagnostics

The first finalist's final-period net was -0.061303 USDT. Replaying with zero
commission, while retaining spread, slippage and funding, produced -0.016459
USDT. Commissions contribute to the loss, but are not the entire explanation.

Its positions were approximately 2.48–2.60 USDT notional; the third finalist's
positions were approximately 0.99–1.00 USDT. The configured 5x is a ceiling:
these candidates selected 1x and allocated only part of the 10 USDT account.

Exchange minimum notionals, quantity steps, tick rounding, liquidity-dependent
fills and liquidation are not modeled. These small simulated orders have NOT
been verified as executable on Binance. This remains a material limitation,
particularly for small accounts. Do not interpret `research_passed` as an
exchange-execution approval.

Limit fills require trade-through; targets are suppressed on the limit-entry
bar because OHLC cannot establish whether the target preceded the fill. This
is conservative and can reduce measured performance. Exact intrabar ordering
requires finer data. Loss-streak and daily-loss safety halts remain active;
they can stop the remainder of a replay period.

The metrics field `gross_profit` is the sum of positive NET trade outcomes,
used for profit factor. It is not profit before fees.

## Fixed baseline comparison

Retrospective diagnostics on the same dates, costs, 10 USDT capital and safety
limits. Four market-order rules, no optimization, each 20% allocation at 1x,
0.8% stop, 1.6% target, three-bar cooldown and 72-bar maximum hold. EMA rules
exit on the opposite cross; RSI recovery rules exit at the 50 midpoint.

| Baseline | Learning net | Validation net | Final-period net | Final trades |
|---|---:|---:|---:|---:|
| Cash / no trade | 0.0000 | 0.0000 | 0.0000 | 0 |
| EMA9/21 long | +0.0258 | -0.0281 | -0.1042 | 48 |
| EMA9/21 short | -0.0402 | -0.0561 | -0.0461 | 14 |
| RSI14 recovery long | -0.0464 | -0.0145 | -0.2031 | 32 |
| RSI14 recovery short | -0.1743 | -0.0123 | -0.0440 | 14 |

Each period resets capital. All four baselines halted under the configured
safety rules in each period; these are stopped-account outcomes, not unrestricted
full-period trading results. None established an edge over cash on this sample.

## Verification and future evaluation

Hand-calculated tests cover long/short round-trip costs, adverse gap stops,
exact targets, limit touches, and timeframe mismatch. Existing tests cover
funding signs and timing, stop-first ambiguous candles, causal features,
split embargoes, safety limits and unchanged selection when holdout data changes.
Compilation-versus-Python comparison alone is not treated as an independent oracle.

The supplied historical window has already been examined. It cannot become a
fresh holdout by relabeling or rerunning it. The JSON artifact freezes the four
baseline definitions with a UTC timestamp and specifies evaluating only later
candles without retuning. This forward evaluation has not run: future data is
not yet available. No strategies were promoted or trading processes started.
