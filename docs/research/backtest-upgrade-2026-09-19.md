# Backtest & Research upgrade — 2026-09-19

## Findings and design

This upgrade is a fixed-rule research workflow. It does not train a model, place
orders, or certify that a strategy will be profitable. Live-market public history
is used for automatic downloads even when the trading account uses testnet.

1. **Chronological validation.** Time-series evaluation must respect ordering;
   equally spaced data allows comparable time windows. Warm-up data is context,
   not scored performance. [scikit-learn TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)
2. **Selection bias.** Picking the winner from many trials can select noise.
   A separate validation period helps structure selection, but is not a complete
   correction for all previous experiments. Save losing as well as winning runs.
   [Bailey et al., Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)
3. **Do not invent confidence scores.** Deflated Sharpe calculations need more
   information than a win rate and a strategy count, including distribution and
   trial information. This upgrade deliberately does not label its shortlist a
   probability of profitability. [Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
4. **Execution assumptions matter.** OHLC data does not establish the path inside
   a candle. Lower-resolution execution assumptions and fees can materially affect
   results. Signals remain close-confirmed with next-open entries; one-minute
   candles execute both 1m and 5m strategies, with adverse costs and stop-first
   handling when both levels are touched.
   [TradingView broker emulator](https://www.tradingview.com/pine-script-docs/concepts/strategies/),
   [Bar Magnifier](https://www.tradingview.com/support/solutions/43000669285-what-is-bar-magnifier-backtesting-mode/)
5. **Causality checks.** Future data must not influence an earlier signal or closed
   trade. Existing replay prefix-invariance and closed-candle tests are retained.
   This is not an exhaustive automated lookahead audit of every strategy.
   [Freqtrade lookahead analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/)
6. **True walk-forward is different.** Rolling selection would choose rules using
   preceding data and evaluate the frozen selection on each later interval. The
   delivered three-period evaluation is not an optimized walk-forward portfolio.
   [QuantConnect walk-forward optimization](https://www.quantconnect.com/docs/v2/writing-algorithms/optimization/walk-forward-optimization)
7. **Transparent reports.** Fees, drawdown, forced exits, saved results and explicit
   fill assumptions are useful parts of strategy review.
   [Freqtrade backtesting documentation](https://www.freqtrade.io/en/stable/backtesting/)

## Delivered behavior

- Choose a coin and download up to 10,000 consecutive, completed 1m candles.
- Set an optional historical end timestamp in UTC to test a different period.
  This is an exclusive end boundary, rounded down to a completed minute.
- Keep identical candles, common warm-up and cost settings across a comparison.
- Adjust planned risk per trade (up to 5%) and a minimum sample screening threshold.
- Periods 1–2 determine the development ranking using stressed net profit.
- Period 3 is computed and saved, but hidden in the UI until explicitly revealed.
  Exported full reports include it. Hiding is a workflow aid, not cryptographic
  isolation; repeated inspection or tuning contaminates validation.
- Base/stress executions reset starting capital independently for each period.
  Summed net is a comparison statistic, not compounded portfolio performance.
- Shortlisting requires the selected minimum trade count in both development cost
  scenarios, positive aggregate net in both, and positive stressed net in both
  development periods. These are our screening heuristics, not published universal
  thresholds or statistical significance tests.
- Show closed-equity curves, worst period drawdown, ambiguous candles, forced
  end-of-data exits, average win/loss (full JSON), and longest loss streak.
- Append completed reports, including losing/zero-trade results, to SQLite at
  `var/research.sqlite`; identical reruns remain separate records. The newest 50
  runs are listed in the UI; older records remain stored. Reopen and export reports.
- Persist model/data/config hashes and full simulated trades. Raw candle CSVs can
  be downloaded separately; the archive stores their fingerprint, not the CSV.
  Failed requests remain visible in the current comparison/export but are not
  archived as completed reports.

## How to use

1. Select a coin, dates, strategy group, capital and cost assumptions.
2. Run the comparison and review development trade counts and stressed results.
3. Record your shortlist before revealing validation. Inspect validation losses
   as well as profits; a short sample is not evidence of a dependable edge.
4. Test new dates and additional coins without changing the selected rules.
5. Continue testnet forward testing. This screen never changes trading bots.

## Remaining limits

The simulator does not reproduce funding, liquidations, intrabar unrealized
maximum drawdown, partial fills, historical order books, queue priority, changing
historical exchange filters, or all live scanner gates. Current active-symbol
selection can introduce survivorship bias. Costs are assumptions, not measured
historical spreads. An ambiguous candle can still hide a different execution
path; the conservative rule does not make OHLC execution exact.

No DSR/PBO, bootstrap confidence claim, parameter fitting, true walk-forward
reselection, multi-coin portfolio engine, or production-readiness certificate is
implemented. The current bounded download window is at most about seven days;
use separate historical end dates for repeated independent windows.

## Research method

Reviewed primary academic papers and official framework/platform documentation,
with emphasis on chronological testing, multiple-trial bias, candle execution,
transaction costs, and reproducibility. Web search/open was used because the
Firecrawl/Exa connectors are unavailable. Recommendations above are engineering
inferences, not evidence that any of the platform's strategies is profitable.
