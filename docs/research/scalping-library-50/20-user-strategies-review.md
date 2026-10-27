# Review of the 20 requested scalping approaches

**Implementation update:** 19 candle combinations are now available under `user-XX` IDs.
See the [implementation guide](20-user-strategies-implementation.md). The status table
below records the pre-implementation audit, not the current availability.

Research date: 2026-09-19. Scope: source review, comparison with current code,
and proposed test definitions. This document does not add or enable trading bots.
None of these combinations has been established here as profitable on crypto 1m data.
The sources explain concepts; the numerical experiment definitions below are ours.

## Definitions for a fair comparison

Use closed 1m entry candles, next-bar execution and identical costs/capital. Short
rules mirror long rules unless an explicit asymmetry is separately tested. Preserve
existing baseline IDs: changing a recipe silently would invalidate old comparisons.

For proposed VWAP combinations, start with the existing rolling 20-bar typical-price
VWAP and label it **rolling VWAP**. A UTC-day VWAP must be a separate experiment
with full day-to-date history. It is not equivalent to a rolling calculation.
Session VWAP resets at a defined session boundary; this distinction is described
in [StockCharts' VWAP documentation][vwap].

Suggested relative volume baseline: trigger volume / mean of the preceding 20
closed bars, excluding the trigger. A 1.5 threshold is a hypothesis, not a proven
optimum. Compare each added filter against its unfiltered baseline; do not infer
that more indicators necessarily improve net returns.

## All 20 approaches: implementation status and proposed experiments

| # | Requested approach | Current implementation relationship | Concrete long-side experiment to test | Source concept |
|---|---|---|---|---|
| 1 | VWAP + EMA 9/21 | Components in `lab-01`, `lab-41`; combined rule absent. | EMA9 crosses above EMA21 while close is above rolling VWAP; compare to EMA cross alone. | [MA][ma], [VWAP][vwap] |
| 2 | EMA 9/21 Pullback | `lab-01` is a cross, not a pullback. `ema-trend-pullback` uses different periods/timeframes. | EMA9 > EMA21; previous candle touches EMA9â€“21 zone; bullish trigger closes above previous high and EMA9. | [Moving Momentum][momentum] |
| 3 | VWAP Reclaim + volume | `lab-41` crosses VWAP without volume/hold confirmation; `lab-44` is a different volume breakout. | Previous close crosses VWAP upward; next candle holds above it, closes bullish and meets relative volume >=1.5. | [VWAP][vwap] |
| 4 | EMA 9 + RSI | `lab-10/13` use EMA50, not EMA9. | Price reclaims EMA9 with RSI14 >50; compare against the same price trigger without RSI. | [MA][ma], [RSI][rsi] |
| 5 | EMA 20/50 Trend Scalping | Related full implementation: `ema-trend-pullback`, using 5m trend and 1m entries. `lab-02` is a simpler reclaim. | Use existing bot as baseline; test an explicitly separate all-1m EMA20/50 pullback variant. | [Moving Momentum][momentum] |
| 6 | VWAP + RSI | `lab-41` supplies the price trigger; combined rule absent. | Rolling VWAP upward reclaim with RSI14 >50; compare to reclaim alone. | [VWAP][vwap], [RSI][rsi] |
| 7 | Bollinger Band Reversal + RSI | `lab-28` is band re-entry without RSI. | Previous close below lower BB20/2; trigger closes back inside and RSI14 increases while still below50. | [Bollinger][bb], [RSI][rsi] |
| 8 | Bollinger Squeeze Breakout + volume | `lab-30` has a fixed width threshold; `lab-34` uses BB/KC containment. Neither is the exact requested volume combination. | Prior BB width in bottom20% of previous120 width observations; close breaks upper band and prior20 high with relative volume >=1.5. | [Squeeze][squeeze] |
| 9 | EMA + Bollinger Pullback | `lab-29` uses SMA20 reclaim/EMA50, not EMA9 plus BB pullback. | Rising EMA9; previous low touches lower BB; bullish trigger reclaims EMA9. | [MA][ma], [Bollinger][bb] |
| 10 | MACD Histogram Momentum | Related: `lab-09` negative-histogram turn with EMA50 trend; `lab-07` histogram zero cross. | Separately test positive histogram increasing for two closed bars plus prior-high breakout; do not rename the existing turn rule. | [MACD][macd] |
| 11 | VWAP + MACD | Components in `lab-07` and `lab-41`; combined rule absent. | MACD histogram crosses zero upward while close is above rolling VWAP. | [MACD][macd], [VWAP][vwap] |
| 12 | RSI Divergence | Not implemented in the 50-candidate library. | Two confirmed price swing lows: second price lower, RSI14 at second pivot higher; enter only after confirmation plus a subsequent prior-high break. | [RSI divergences][rsi] |
| 13 | Stochastic Reversal + EMA | `lab-15/16` are reversal rules without EMA; `lab-17` is an EMA-filtered midpoint cross. | K14 crosses above D3 from previous K<20 while close is above EMA50. Distinguish raw K from slow stochastic. | [Stochastic][stoch] |
| 14 | ADX + EMA Momentum | No dedicated ADX+EMA laboratory rule; scanner regime information is not the same thing. | EMA9/21 bullish cross with ADX14 >=25 and +DI > -DI; compare to `lab-01` without this filter. | [ADX/DMI][adx] |
| 15 | Volume Breakout | Implemented family: `lab-24` prior10 high breakout plus relative volume >1.5; `lab-44` adds VWAP bias to prior-bar breakout. | Test those existing definitions first, before another duplicate breakout entry. | [Price channels][channels], [Volume][volume] |
| 16 | Support/Resistance Rejection | Related: `liquidity-sweep-reclaim` and `lab-26`; neither covers all rejection patterns. | Use most recent confirmed 5m swing low; 1m low comes within0.1 ATR, lower wick >=50% of range, closes above level with relative volume >=1.5. | [Candlestick structures][candles], [Price channels][channels] |
| 17 | Liquidity Sweep + Reversal | Existing `liquidity-sweep-reclaim`: confirmed swings, wick/volume conditions and 5m trend. `lab-26` is a simpler failed-breakdown rule. | Test existing implementation first; candle sweeps are price patterns, not proof of stop hunting or hidden liquidity. | [Price channels][channels], [Candlestick structures][candles] |
| 18 | Order-Flow Scalping | Existing `order-flow-pullback` uses synchronized depth and aggressive trades; not a full footprint strategy and excluded from candle replay. | Keep a separate event-replay project with depth snapshots/deltas, trades, timing and execution assumptions; do not substitute OHLC volume. | [Binance depth][depth], [Binance trades][trades], [Footprints][footprint] |
| 19 | Opening/Session Momentum | No fixed exchange-opening restriction needed for this crypto system. Related event-driven participation rule: `lab-44`. | Respect the requested 24/7 operation: rolling VWAP bias, relative volume >=2, prior20 structure break. Record UTC hour for analysis; no clock gate. | [VWAP][vwap], [Volume][volume] |
| 20 | Multi-Timeframe Scalping | Existing EMA pullback and sweep use closed5m context/1m entry. A combined15m context is not implemented. | Compare existing5m/1m baseline to an added closed15m EMA20/50 alignment condition using sufficient earlier history. | [Moving Momentum][momentum], [MA][ma] |

## Findings that affect implementation

### Divergence requires delayed confirmation

An RSI divergence is not simply RSI being low. Fix pivot rules (for example two
bars on each side), spacing and expiry before testing. A pivot at time t is only
known after its right-side candles close; neither its recognition nor an entry
may be backdated. Strong trends can continue despite divergences, so divergence
alone does not establish a reversal. [RSI reference][rsi]

### ADX is strength, not direction

ADX can stay high during both upward and downward trends. EMA direction or the
directional indicators must supply the bias. A threshold such as25 is a starting
definition, not a universally profitable setting. [ADX reference][adx]

### A squeeze does not predict the breakout direction

Separate contraction detection from a closed-bar directional trigger. A volume
filter and a structure break are candidates to test, not guaranteed fixes for false
breakouts. Relative width percentiles also need a longer warm-up than the current
fixed-width laboratory rule. [Bollinger squeeze reference][squeeze]

### Order-flow data quality is a separate requirement

Some charting delta indicators infer signed volume from lower-timeframe price
movement rather than actual aggressor-side executions. They are not interchangeable
with exchange trade flags. [TradingView Volume Delta][delta]

Binance documents how to build a synchronized local depth book and recover from
sequence discontinuities. Aggregate trades expose maker-side information for
classifying aggressor direction. Recording both does not by itself reproduce queue
position, fills or latency. [Depth maintenance][depth], [aggregate trades][trades]

The current strategy checks fresh short-window depth/trade evidence. That does not
mean we have historical event replay, footprint-by-price tests or validated CVD
divergence. Candle CSV comparisons must continue to refuse that strategy.

## Recommended research order

This order reflects implementation readiness and testability, not expected profit:

1. Evaluate existing versions of5,10,15,17,20 to establish cost-adjusted baselines.
2. Add separate experimental IDs for1,2,3,4,6,7,8,9,11,13,14 and the 24/7 variant19.
   Test each added filter by itself against its baseline; report trade count and
   exposure, not only win rate.
3. Implement12,16 and the15m extension of20 with confirmed pivots and sufficient
   historical context. Higher timeframe periods must be fully closed.
4. Validate18 on recorded events independently of candle-based results.

Do not call these 20 independent sources of edge: many are correlated combinations
of the same price series. Selecting the highest historical result after many trials
can select noise. Keep experiment history, lock rules before unseen-date evaluation,
stress costs, then forward-test finalists. No candidate is approved for real funds
by this source review. [Backtest overfitting research][overfit]

## Sources

[ma]: https://www.tradingview.com/support/solutions/43000502589-moving-averages/
[vwap]: https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/volume-weighted-average-price-vwap
[momentum]: https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/moving-momentum
[rsi]: https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi
[bb]: https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/bollinger-bands
[squeeze]: https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/bollinger-band-squeeze
[macd]: https://www.tradingview.com/support/solutions/43000502344-moving-average-convergence-divergence-macd-indicator/
[stoch]: https://www.tradingview.com/support/solutions/43000502332-stochastic-stoch/
[adx]: https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-directional-index-adx
[channels]: https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/price-channels
[volume]: https://www.tradingview.com/support/solutions/43000591617-volume/
[candles]: https://chartschool.stockcharts.com/table-of-contents/chart-analysis/candlestick-charts/candlestick-pattern-dictionary
[depth]: https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly
[trades]: https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Aggregate-Trade-Streams
[footprint]: https://www.tradingview.com/support/solutions/43000726164-volume-footprint-charts-a-complete-guide/
[delta]: https://www.tradingview.com/support/solutions/43000725057-volume-delta/
[overfit]: https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
