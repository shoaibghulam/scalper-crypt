# Long-range backtesting

Use **Backtest & Research → One-year strategy & coin testing**.

1. Choose an active Binance USDT perpetual from the coin list.
2. Enter starting capital and leverage (not a deposit or trade order).
3. Choose 7, 30, 90 or 365 days, optionally ending at a historical UTC date.
4. Select individual strategies or Select all strategies. Order-flow strategies requiring event data cannot be tested with candles.
5. Start the backtest. Progress covers the current download/replay pass. Comparisons may take hours; Cancel stops ongoing work at the next download page or replay checkpoint.

The year is exactly 365 days of scored one-minute candles plus strategy warm-up. Daily public futures data is cached on disk. Gaps, invalid data and newer listings without sufficient history fail visibly. No synthetic candles are inserted. Completed strategy reports are saved in SQLite; job summaries preserve the group across browser navigation and server restart. An unfinished job does not resume automatically after restart; rerunning reuses cached candles. One job is allowed per API process. Use the existing single API process deployment, not multiple workers, for this local job manager.

## Results

Each strategy shows trade count, take-profit hits, stop-loss hits, forced period-end exits, net PnL and fees. Counts use the base-cost scenario once; stressed runs are separate in the report. TP/SL labels describe the exit trigger, not net profitability. Both touched in a candle resolves to stop first. The three chronological periods reset starting capital independently; their net totals are not a continuously compounded one-year portfolio return. Viewing the full-period table reveals the validation result; do not subsequently treat it as untouched data.

Defaults: 1% planned risk per trade, 5 bps fee per side, 2 bps slippage per side and 2 bps full spread. Stress doubles spread and slippage. Detailed short-window evaluation retains adjustable costs/risk. Current exchange order filters are applied, so small capital can yield rejected orders and no trades.

The **Confirmed setups now** scanner screens all current markets for one selected strategy, retains its normal liquidity/spread checks, and shows only fresh unexpired confirmed setups. It does not use historical returns to call a coin ready. Select a listed coin to populate the historical test. No research action places an order or changes bots. Signals can expire and account-level checks still apply.

## Limits and verification

Funding, liquidation, intratrade drawdown, historical order books, partial fills and historical exchange-rule changes remain outside this candle simulator. Current-market selection has survivorship bias. Backtest winners do not establish future profitability.

Verified: year-boundary/range validation, cached downloads, missing-history rejection, cancellation and concurrency checks, restart restoration, stale-signal exclusion, full backend unit suite, frontend suite/build/lint and a real seven-day ETHUSDT job with saved reports. A full year across every strategy has not been completed as part of implementation verification.

Implementation reference: [Binance official futures connector market endpoints](https://github.com/binance/binance-futures-connector-python/blob/main/binance/um_futures/market.py). Uses public `/fapi/v1/klines` with explicit start/end bounds, daily pagination and the existing request-weight limiter.


## Automatic 20-coin comparison

The long-range form now defaults to **Automatic top 20 coins by volume**. Select strategies, amount, leverage and duration, then Start historical backtest. The backend snapshots the top 20 active supported USDT perpetuals by current 24-hour quote volume and one common end time. Every strategy runs independently on each coin with identical dates and starting capital. This selection is not a historical top-volume universe and carries survivorship/selection bias.

The report adds a coin column, sorts rows by net result and can be downloaded as a complete JSON comparison including selected universe, settings, report IDs and errors. Per-coin reports remain in the experiment archive. A missing-history coin is recorded and not replaced; other coins continue. Network or infrastructure errors stop the job visibly rather than flooding retries. Partial results stay saved. Single-coin mode remains available. Starting a 20-coin year comparison does not mean all twenty outcomes are instantly available: completion can take many hours depending on strategy count.


## Saved market data and coin picker

The Single coin selector now exposes all active supported USDT perpetuals as a searchable list rather than browser datalist suggestions filtered by the prefilled BTCUSDT text. Searches do not silently change the selected coin.

Long-range files remain in `var/research-candles/`; they are not deleted after testing. The saved-data panel reports retained files, disk usage and earliest/latest chunk dates. These dates are not a guarantee of continuous coverage. Matching complete daily chunks are reused across strategies, even when their warm-up requirements differ. Missing days and an updated partial-day boundary may still need a download. Download progress distinguishes cached chunks reused from requests to Binance. Short-window automatic CSV downloads are a separate path and do not populate this long-range cache.


## Timeframes and profit columns

Entry timeframe filters the enabled strategy list and the submitted strategy IDs; it does not resample a fixed strategy or change its confirmation rules. New result rows snapshot the timeframe from the saved strategy specification. Legacy rows without that snapshot use the current catalog definition and are labelled in the cell tooltip.

Profit/loss before fees equals net plus recorded fees and already reflects simulated spread/slippage. Net profit/loss is after recorded fees. Positive and negative values have signed text and distinct colors. Values aggregate independent chronological periods, not a continuously compounded portfolio return.
