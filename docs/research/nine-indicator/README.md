# 1m Nine-Indicator Trend Pullback

ID: `nine-indicator-1m-pullback`. A separate strategy from the user's 1h-to-1m model. All nine requested indicators operate on closed **one-minute** candles. Available for backtesting and testnet execution; installation does not start a bot.

This is a fixed, unoptimized trend-pullback hypothesis. All gates must pass. Nine indicators do not provide nine independent pieces of evidence: RSI, StochRSI and MACD overlap. Extra filters can remove good trades and reduce sample size. No claim of superior or profitable performance is made.

| Indicator | Settings | Exact role |
|---|---|---|
| EMA | 9 / 20 / 50 | Ordered trend, EMA20 slope over three bars; prior candle overlaps the EMA9/20 zone. |
| VWAP | UTC session | Long closes above VWAP, short closes below; typical-price volume weighting since 00:00 UTC. |
| RSI | 14, Wilder | Long 50–70 and rising; short 30–50 and falling. |
| MACD | 12, 26, 9 | Line agrees with direction; histogram improves in that direction, without requiring it already crossed zero. |
| Bollinger Bands | 20, 2 population standard deviations | Trigger closes between middle and trend-side outer band; pullback does not close beyond opposite band. |
| Stochastic RSI | 14, 14, 3, 3 | Directional K/D crossover within the last three transitions; recent K reset through midpoint, current K aligned with D and not extreme. |
| Volume | Prior 20-bar average | Trigger >=1.2x average, excluding trigger from baseline. |
| ATR | 14, Wilder | Scales stops and maximum entry chase. |
| ADX | 14, Wilder | >=20 with +DI above -DI for longs, reverse for shorts. |

The entry trigger is a directional candle body closing beyond the previous candle's high (long) or low (short), beyond EMA9, after all other conditions pass. StochRSI long rules: some K in the last four observations <=0.5, a K-over-D crossover during their three transitions, and current D < K <0.9. Shorts apply those rules to 1-K and 1-D.

Stops lie beyond the previous/trigger candle extreme plus 0.1 ATR, with a minimum distance of 1.5 ATR or 0.45% of entry, whichever is larger. Target is 2R. Entry more than 0.5 ATR away from the signal close is refused. Existing order sizing, fee/spread and account limits apply. Protection stays fixed; no partial exits or trailing adjustments.

VWAP uses up to 1,440 one-minute candles and requires the actual midnight candle, continuous history, positive session volume and at least twelve session candles. Midnight resets VWAP, not the other indicators. All other indicators use the most recent 200 one-minute candles. The 300-bar 5m requirement provides a 1,500-minute replay warm-up; 5m prices are not used for signals. Incomplete, non-finite, misaligned and gapped 1m data are rejected. There is no use of forming bars or future pivot labels.

Parameters were chosen to make the user's indicator list explicit and reproducible. They have not been fitted to historical returns. Evaluate multiple coins across chronological base/stressed-cost periods, including the number of trades and drawdown. A high win rate or a favorable training period alone is not evidence of an edge.
