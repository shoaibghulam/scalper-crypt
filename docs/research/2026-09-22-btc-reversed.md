# BTC Selective Reversed 1 Minute

User-requested reversal of BTC Selective Reversion. Original remains unchanged.
New ID: `btc-selective-reversed-1m`; backtest-only, BTCUSDT dataset required.

The same closed-candle signal conditions are evaluated. A long becomes a short
and a short becomes a long. At the actual reverse fill, compute the original
bracket for the opposite direction, then swap its stop and target. With
D = max(1.5 ATR14, 0.45% of entry), the reverse stop is 2D away and target D
away. Reward/risk is 0.5. Position sizing uses the new stop and the same risk
limit; it is not the old position's quantity or a renamed historical exit.

The screenshot's saved source is `05fc3e0420de435db019e614cebe57b3`.
Its original trade ledgers were reproduced exactly, including all stress
periods, before evaluating the reverse on the identical cached BTC dataset.
No extra parameter search was performed.

| Base-cost comparison | Trades | TP | SL | Period-end | Net USDT |
| --- | ---: | ---: | ---: | ---: | ---: |
| Original | 417 | 119 | 297 | 1 | -70.27 |
| Reversed | 382 | 251 | 129 | 2 | -36.84 |

Reversed period net outcomes are -6.35, -13.10 and -17.39 USDT. With doubled
spread/slippage they are -13.53, -19.06 and -20.08 (total -52.67).
All three periods lose money. More TP hits do not make the candidate profitable:
each stop loses roughly twice a target's gain before transaction costs.

Counts do not swap exactly because opposite orders receive different adverse
fills, levels are based on those fills, later entry availability changes,
quantity constraints can reject trades, and period-end exits remain separate.
OHLC ambiguity continues to use the simulator's conservative stop-first rule.

These are sums across three independent chronological periods, each starting
at 100 USDT, not a single compounded portfolio. Fees: 5 bps/side; slippage:
2 bps/side; spread: 2 bps; risk: 1%; leverage cap: 3. Previously inspected data
is retrospective evidence, not an untouched holdout. This experiment does not
justify enabling trading.

Saved reports in research SQLite:
- Original reproduction: `a0bf8b6295904adeab7b201718e9116c`
- Reversed experiment: `e81e136f67124cbbbd5c09f6469afd3e`

Reproduce with `backend/tools/evaluate_btc_reversed.py`. Summary:
`var/btc-reversed-comparison.json`. Both reports retain full trade ledgers,
settings, strategy definitions and dataset hashes.
