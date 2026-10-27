# Changez crypto backtests

In Backtest & Research, search for **Changez** and select **Changez 1 Minute
(Crypto)** and/or **Changez 5 Minute (Crypto)**. Choose coins, capital, leverage
and dates, then start the historical backtest. Both use the existing download
cache and saved-report database, including TP/SL counts, costs and net PnL.

These Python versions port the supplied MT5 EA's closed-bar signal logic:
confirmed two-left/two-right swing crossing, 100-bar premium/discount zone,
displacement FVG on either of the last two bars, ten-bar swing stop and 5R target.
The displacement threshold is 1.5 times the five-body average including the
displacement candle itself. Entry sessions use the signal candle's opening time:
02:00–05:00 or 08:30–11:00 America/New_York, with historical DST and weekends.

Differences: pivots are reconstructed in a bounded 200-bar window for reproducible
replay and scanning, rather than retained indefinitely as MT5 state. Binance
timestamps are UTC, so no broker offset is required. Risk, fees, slippage,
leverage and exchange checks belong to the existing platform. The MT5 account
balance risk tiers are not used. Stops are not widened by the strategy.

IDs: `changez-1m`, `changez-5m`. These research candidates are registered for
crypto backtesting only, available without the trading engine. Adding them does not start bots.
No profitability is established by compilation, registration or unit tests.

## Cost-filtered experiment

Separate `changez-cost-filtered-1m` and `changez-cost-filtered-5m` candidates
skip a setup when its original swing stop is less than 0.45% from entry. The
threshold was taken from the existing platform floor, not optimized. Signals,
sessions and 5R targets remain unchanged. The original candidates remain available.
Both versions are backtest-only. This filter is a research hypothesis, not a
profitability certification or a model of arbitrary user-selected fee rates.

The September 21 audit reproduced the saved 30-day BTC report's trades exactly.
It recorded 12 targets and 40 stops, +3.068886 USDT before fees and -1.013416
after 4.082303 fees. Of 52 trades, 47 had original stops below 0.45%; their net
sum was -3.356880. Median stop distance was 0.187837%. This subgroup calculation
is diagnostic, not a replay: skipping positions changes subsequent availability.

The saved 90-day original report lost 16.095981 net, including 15.501629 fees.
It was slightly negative even before commissions, so fees do not explain all
underperformance. The three chronological periods reset starting capital; sums
are not a compounded portfolio return. All these datasets have already been
inspected and overlap. They are retrospective comparisons, not fresh holdouts.

`backend/tools/audit_changez.py` uses cached BTC candles, checks the original
30-day trade ledger, and stores every new comparison in the research SQLite
archive. Its summary is `var/changez-cost-audit.json`. Future untouched data and
more trades are required before selecting this variant for execution.

### Recorded comparison

| Window | Original net USDT | Filtered net USDT | Filtered trades | Filtered TP / SL |
| --- | ---: | ---: | ---: | ---: |
| 30 days | -1.01 | +1.62 | 2 | 1 / 1 |
| 90 days | -16.10 | -1.91 | 7 | 1 / 6 |

The filtered 90-day doubled-spread/slippage result is -2.17 USDT. Its first two
periods both lost money. The positive 30-day result is the final portion of the
90-day test, not independent confirmation. Reduced aggregate loss mostly reflects
reduced participation; the candidate has not demonstrated a positive expectancy.
No promotion to trading is warranted from this experiment.

Saved filtered report IDs:
- 30 days: `4c2b0bc9a4224e648a7c95437f3504e0`
- 90 days: `0a722d6a8ebe4adbb387fca789d0c27e`
