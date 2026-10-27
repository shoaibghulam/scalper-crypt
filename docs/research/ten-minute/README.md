# Ten-minute crypto experiments (24/7)

Added eleven entries: a 10-minute adaptation of the user's multi-timeframe structure strategy, plus ten distinct 10-minute hypotheses. All support symmetric long/short signals, backtesting and testnet execution. None restricts entries to New York, London, a cash opening range, weekdays or another session. None forces a session-close exit. Existing historical session-specific models remain separately named models.

## Multi-timeframe adaptation

`mtf-lux-inspired-structure-10m` — **MTF Structure: LuxAlgo-inspired 1h to 10m**.

Retains the original 1h EMA20/50 bias, 30m confirmation, 15m ADX/DI strength and a confirmed 5m structure break, but evaluates the retest and trigger on **10-minute candles**. The 5m layer is a structure sub-step, not a higher timeframe than 10m. The entire 10m retest candle must begin after the 5m breakout is confirmed. A 10m trigger closes beyond the retest's extreme, with RVOL20 >=1.2 and the same ATR-based tolerances as the original 1m version. Both use 200 entry bars, so indicator lookback duration changes with timeframe. Higher-timeframe checks use the latest completed bars at each decision time. Warm-up: 6,060 one-minute candles.

This is an independently authored adaptation of publicly described structure concepts, not an integration with paid LuxAlgo signals. Source and original assumptions: [multi-timeframe model](../mtf-structure/README.md).

## Ten additional strategies

| Strategy ID suffix (`ten-`) | Name | Entry hypothesis |
|---|---|---|
| `ema-pullback` | EMA Ribbon Pullback | EMA9/20/50 ordered trend; EMA20 slope; previous bar overlaps prior EMA9/20 zone; break prior high/low; RVOL20 >=1.2. |
| `macd-recovery` | MACD Momentum Recovery | Price beyond EMA50, MACD12/26 line agrees, histogram crosses zero in that direction; RVOL20 >=1.2. |
| `donchian-volume` | Donchian Volume Breakout | EMA20/50 and slope aligned; close breaks previous 20-bar high/low; RVOL20 >=1.5. |
| `bb-reentry` | Bollinger RSI Re-entry | ADX14 <20; prior close outside BB20/2, current close back inside that band; prior RSI14 <35/>65 and improving; RVOL20 >=1. |
| `stoch-reset` | StochRSI Trend Reset | EMA20/50 and slope aligned; StochRSI14/14/3/3 K/D cross from prior K <0.3/>0.7; break prior high/low; RVOL20 >=1. |
| `rolling-vwap` | Rolling VWAP Reclaim | Close crosses 50-bar volume-weighted typical price and agrees with EMA50; RVOL20 >=1.2. |
| `adx-expansion` | ADX Momentum Expansion | ADX14 crosses upward through 25; aligned DI and EMA20/50 slope; break prior high/low; RVOL20 >=1.2. |
| `squeeze` | Bollinger Squeeze Release | Previous BB20/2 relative width is in lowest 20% of 60 observations; fresh trend-side outer-band close; RVOL20 >=1.5. |
| `liquidity-sweep` | Liquidity Sweep Reclaim | Trigger wick exceeds prior 20-bar extreme, then closes inside the old range; improving RSI14; RVOL20 >=1.5. This is a candle proxy, not proof of liquidation or institutional activity. |
| `rsi-pullback` | RSI Midline Recovery | EMA20/50 slope aligned, prior close on trend side of EMA50; RSI14 crosses 50 with the trend; break prior high/low; RVOL20 >=1.2. |

All recipes additionally require a directional candle body. EMA slopes compare current to preceding value; volume baselines exclude the trigger. Bollinger uses population standard deviation. ATR/ADX/RSI use existing Wilder calculations. Rolling VWAP uses the preceding/current **50 bars**, not midnight or a regional session reset. The mean-reversion model uses the common 2R exit rather than claiming its target is the Bollinger midline.

## Candles, exits and platform integration

10m candles are built from two consecutive completed 5m candles, aligned to minutes 00, 10, 20, 30, 40 and 50 of each UTC hour. This alignment defines the candles, not permitted trading hours. Partial groups and stale signal times cannot produce an entry. Replay continues to process execution on 1m data and enters at the first available 1m open after the 10m signal closes. No native exchange 10m request is made.

Each additional recipe uses 200 completed 10m bars; 402 5m history bars provide enough boundary buffer. Warm-up: 2,010 one-minute bars. All new strategies use the common structural stop beyond the last two entry candles plus 0.1 ATR, at least 1.5 ATR and 0.45% of entry. Target: 2R. Entry chase is limited to 0.5 ATR. Frozen brackets, full exits, costs, order minimums and account risk gates remain unchanged. The MTF variant has its longer warm-up. The existing 1m strategy is retained for comparison.

10m is discoverable through the research timeframe filter, Create Scalp and Favorites. Single Coin mode also supports 10m and ranks only 10m execution strategies with qualifying verified trade history. Installing these models does not start a bot. The batch request capacity accommodates the expanded library.

## Comparing 1m and 10m

In Backtest & Research, choose **All entry timeframes**, select the original **MTF Structure: LuxAlgo-inspired 1h to 1m** and the new **1h to 10m** variant, and run them on the same selected coins and dates with identical capital, risk, leverage, fees and slippage. The shared long-range download uses the maximum selected warm-up, keeping their scored dates aligned. Select the additional 10m strategies for a broader comparison, but do not treat different rules as a pure timeframe experiment.

Compare net results after costs, trade count, drawdown, expectancy and stability across the three chronological periods, including stressed costs and the untouched validation period. No timeframe is declared best by this implementation. More models increase selection bias; selecting a winner and evaluating it on the same data does not establish an edge. Candle replay excludes funding and historical order-book liquidity.

Tests cover all ten recipes with unmocked indicator calculations in both directions on fixed synthetic fixtures, causal entry timing, incomplete/gapped data, clock-shift invariance, 2R brackets, MTF retest chronology, catalog selection and 10m ranking. These are functional tests, not profitability evidence.
