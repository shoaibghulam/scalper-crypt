# MTF Structure: LuxAlgo-inspired 1h to 1m

Strategy ID: `mtf-lux-inspired-structure`. Available in Backtest & Research and testnet trading. This is an original implementation inspired by public market-structure concepts, not a LuxAlgo product, copied script, paid signal feed or exact replication. The user selected the public-rule adaptation on 2026-09-20.

The objective is to check direction from higher to lower timeframes and enter only on the 1-minute chart. These indicators have distinct jobs; adding more confirmations does not itself establish an edge. The parameters below are hypotheses, not optimized or proven to be the best.

| Step | Timeframe | Long setup (shorts mirror it) |
|---|---|---|
| 1. Bias | 1 hour | Close > EMA20 > EMA50; EMA20 rises over three completed bars. |
| 2. Confirmation | 30 minutes | Same ordering and positive EMA20 slope. Disagreement means no trade. |
| 3. Strength | 15 minutes | Wilder ADX14 >=20, +DI > -DI, close above EMA20. |
| 4. Structure | 5 minutes | Close breaks a previously confirmed swing high in the last six completed bars. No earlier close broke that swing; subsequent 5m closes must remain above it. |
| 5. Entry | 1 minute | After the 5m breakout closes, the previous 1m candle retests the broken level within +/-0.25 ATR. The trigger closes above that candle's high with a bullish body and volume >=1.2x the prior 20-bar average. |

For shorts, reverse price comparisons, use swing lows, -DI > +DI, and a bearish 1m trigger below the prior low. There are no trades in a neutral or conflicting trend. Each waiting state explains the first unmet step in the existing scanner.

The 1m trigger must close above/below the structural level by no more than 1.5 ATR to avoid chasing. Entry is at the next executable price, with a maximum 0.5 ATR deviation from the signal close. Stop distance is the largest of: distance beyond the two retest/trigger candles plus 0.1 ATR, 1.5 ATR, and the platform's 0.45% minimum. Target is 2 times the stop distance. ATR14 uses the 200-bar 1m context. Stops and targets are frozen; no partial exits, trailing stops, or discretionary changes.

## Indicator selection and sources

- **Market structure:** LuxAlgo describes BOS as a continuation break and CHoCH as a reversal warning. Our simplified trend-aligned swing-break filter uses independently authored rules and does not implement its complete CHoCH state machine, order blocks, FVGs, or proprietary filters. [LuxAlgo market-structure documentation](https://docs.luxalgo.com/platform/algos/price-action-concepts/market-structures), [LuxAlgo Smart Money Concepts overview](https://www.luxalgo.com/library/indicator/smart-money-concepts-smc/).
- **EMA20/50:** a reproducible directional bias. These periods and the slope rule are our design choices, not claimed LuxAlgo defaults.
- **ADX14 and DI:** trend strength and directional confirmation, separate from moving-average direction. The app's existing Wilder implementation is reused. [TradingView ADX documentation](https://www.tradingview.com/support/solutions/43000589099-average-directional-index-adx/).
- **Relative volume:** compares trigger participation with the previous 20 bars, excluding the trigger. It does not identify institutional orders.
- **ATR14:** scales retest tolerances and stop placement to observed volatility. It does not predict future volatility. [TradingView ATR documentation](https://www.tradingview.com/support/solutions/43000501823-average-true-range-atr/).

## Causality and execution

1h, 30m and 15m candles are aggregated from complete UTC-aligned 5m groups. Incomplete groups are discarded, not filled with future data. 1,212 completed 5m candles provide at least 100 full hourly candles at any alignment; the scanner requests 1,213 including possible forming data, below its existing 1,500-candle request bound. Replay warm-up is 6,060 one-minute candles. EMA calculations use the most recent 100 completed bars of each higher timeframe.

Swing pivots use two bars on each side and are available only after both right-side bars close. A breakout cannot confirm its own pivot. The retest candle must start after the completed 5m breakout, which prevents a visually convenient but unavailable intra-candle signal. Missing, duplicated, misaligned, future or stale candles are rejected. This causal pivot convention differs from retrospectively placed chart labels.

The existing testnet-only, fees, spread, minimum order, risk and 1.5R execution gates remain enforced. No bots or orders are started by installing this strategy. Backtests approximate fills from candles and exclude funding and historical order-book liquidity. Functional tests establish the implementation's behavior, not trading profitability. Compare chronological base/stressed-cost periods across several coins, retain losing results, and validate on untouched dates before considering further use.
