# Backtest & Research

Open the dashboard's **Backtest & Research** tab. This feature never sends trading
commands or changes active bot settings. Choose one of the 70 research candidates
or eight existing candle strategies. Choose Automatic coin history and a USDT coin
(or optionally upload your own CSV),
enter costs and instrument sizing assumptions, and run the evaluation.
Use **Compare all 50 new candidates** or **Compare library including existing
strategies** for a sequential comparison with progress and a stop-after-current
control. Export results before leaving the tab. The source-linked rules and
selection protocol are in [the research catalog](research/scalping-library-50/README.md).
The 70 added research candidates are available in **testnet Create Scalp and Auto Trade**.
They are blocked in real-money mode and included in the testnet automatic fleet.
For the19 requested combinations and the15m warm-up requirements, see the
[implementation guide](research/scalping-library-50/20-user-strategies-implementation.md).

The new [Funded Price Action video strategy](video%20docs/found%20action/README.md)
uses 5m structural entries with 1h context and candle-based stops. It needs at
least 1,960 input rows; comparisons share the longest warm-up.

Automatic mode fetches 2,000 to 10,000 closed one-minute candles from Binance
USD-M Futures public live-market data. It loads current quantity step, minimum
quantity and minimum notional. It uses the same downloaded dataset for every
strategy in a comparison; you can download that CSV and export the experiment
metadata. Fees and execution costs remain assumptions. Missing candles, new
listings with insufficient history, network blocks and rate limits stop the run
with an error; the app never silently switches to testnet candles or fake data.

Automatic mode fetches 2,000 to 10,000 closed one-minute candles from Binance
USD-M Futures public live-market data. It loads current quantity step, minimum
quantity and minimum notional. It uses the same downloaded dataset for every
strategy in a comparison; you can download that CSV and export experiment
metadata. Fees and execution costs remain assumptions. Missing candles, new
listings with insufficient history, network blocks and rate limits stop the run
with an error; the app never silently switches to testnet candles or fake data.

API reference: [Binance Futures market data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data).

CSV headers: `open_time,open,high,low,close,volume`. Timestamps are UTC Unix
milliseconds. Supply 1,200–10,000 consecutive, aligned, closed one-minute candles,
at most 2 MB. Missing, duplicate, unordered and invalid candles are rejected.
Do not combine different coins in one file. Obtain genuine historical data;
synthetic candles are suitable for software checks, not profitability claims.

At least 500 candles provide history (780 for user-20; 1,260 for Funded Price Action). Three subsequent disjoint periods each
start flat with the supplied capital, using preceding history for indicators only.
Each runs at base costs and again with double spread and slippage. Parameters are
fixed; this is chronological evaluation, not model training or optimized walk-forward
selection. All periods are visible: none remains untouched after inspecting results.
Save genuinely unseen dates for later evaluation.

Sizing budgets 1% of simulated equity for stop loss plus modeled costs, caps margin
at 50%, rounds quantity down, and rejects orders below the supplied minimum notional.
Verify these assumptions for the actual coin. They are not downloaded exchange rules.
The next candle's open determines entry; frozen levels use the previous close.
EMA bounded IOC entries skip worse-than-limit opens. Queue depth and partial fills
cannot be reconstructed from OHLC. Same-bar stop/target ambiguity resolves stop-first.
Remaining positions close at each period end and are labeled `end_of_data`.

The downloadable JSON contains every simulated trade, costs, settings, period
boundaries, dataset and experiment hashes, metrics and limitations. Save every run,
including losing experiments. Reports are not automatically persisted on the server.
No trades means insufficient evidence. Undefined profit factor is not infinity.
Drawdown is based on closed equity and can understate intratrade drawdown.

## Boundaries

This evaluates strategy patterns with execution approximations, not the complete live
system. Funding, liquidation, historical liquidity, latency, partial fills, full
scanner gates, exchange tick sizes and changing historical instrument rules remain
unmodeled. Ten thousand candles cover less than seven days, so use this initial UI
for bounded experiments, not proof across market regimes. Research requests run
outside the API event loop, one at a time per API worker; the trading engine remains
a separate process. Do not expose the local API publicly.

ML training, automatic strategy selection, long-running historical data ingestion,
shadow execution and a durable experiment database are not implemented by this tab.
Those require broader data and validated execution assumptions first. No automatic
promotion to live trading occurs.
