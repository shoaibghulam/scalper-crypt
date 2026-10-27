# Fifty scalping research candidates

See also the [review of your 20 requested combinations](20-user-strategies-review.md),
including existing matches, missing filters, data requirements and proposed tests.

Researched 2026-09-19. These are 50 explicit entry-rule hypotheses, not a ranked top-50 list or a claim of profitability. Related candidates deliberately share indicator families. Sources document concepts; parameter combinations, crypto timeframes and protective exits are our adaptations.

The separate [Funded Price Action video candidate](../../video%20docs/found%20action/README.md)
is also available in Research; it uses candle stops rather than the ATR contract below.

## Availability

Select a coin in **Backtest & Research** to download history automatically; CSV
upload remains optional. These candidates can also be selected manually in
**testnet Create Scalp**. They are blocked for real-money execution and included
in testnet Auto Trade. Signal history windows match replay; trading adds spread,
liquidity, cost, exchange-filter and account-risk gates.

## Common experiment contract

- Continuous crypto trading: no stock/forex session restriction.
- Closed 1m or 5m candles only; 5m candles aggregate complete 1m groups.
- Long and short rules mirror each other. No conflicting-direction entries.
- Entry at the next candle open with configured execution costs.
- The 50 candidates in this document use stop distance max(1.5 x Wilder ATR14, 0.45% of entry), target 2R; frozen before simulated fill. These common exits are not the original sources' exit systems.
- No ML, no martingale, no grid doubling and no automatic real-money activation.
- Trend in these recipes means close relative to EMA50 unless specified otherwise. RSI and ATR use Wilder smoothing; Bollinger sigma is population standard deviation.
- Stochastic is raw K14 with SMA3 signal, not slow stochastic. VWAP is a rolling20 typical-price volume-weighted approximation, not session VWAP. MFI uses14 periods; CMF uses20.
- EMA is SMA-seeded. Five-minute contexts use100 bars; one-minute contexts use200 bars. This finite history is part of the experiment definition.

## Implemented rules

Long rules are listed below; short rules use the inverse and symmetric oscillator thresholds (e.g. 30/70, 20/80). IDs do not indicate priority or measured performance.

| ID | Candidate | Entry candles | Long trigger | Source concept |
|---|---|---|---|---|
| lab-01 | EMA cross | 1m | EMA9 crosses above EMA21. | [Reference](https://www.tradingview.com/support/solutions/43000502589-moving-averages/) |
| lab-02 | EMA trend reclaim | 1m | Close crosses EMA20 with close above EMA50. | [Reference](https://www.tradingview.com/support/solutions/43000502589-moving-averages/) |
| lab-03 | EMA ribbon alignment | 1m | EMA9 > EMA21 > EMA50 becomes newly aligned. | [Reference](https://www.tradingview.com/support/solutions/43000502589-moving-averages/) |
| lab-04 | EMA slope breakout | 5m | Rising EMA20 and close crosses prior bar high. | [Reference](https://www.tradingview.com/support/solutions/43000502589-moving-averages/) |
| lab-05 | SMA cross | 5m | SMA10 crosses above SMA30. | [Reference](https://www.tradingview.com/support/solutions/43000502589-moving-averages/) |
| lab-06 | VWMA reclaim | 1m | Close crosses volume-weighted close MA20 above EMA50. | [Reference](https://www.tradingview.com/support/solutions/43000502589-moving-averages/) |
| lab-07 | MACD signal cross | 1m | MACD12/26 crosses its EMA9 signal. | [Reference](https://www.tradingview.com/support/solutions/43000502344-moving-average-convergence-divergence-macd-indicator/) |
| lab-08 | MACD zero cross | 5m | MACD12/26 crosses zero. | [Reference](https://www.tradingview.com/support/solutions/43000502344-moving-average-convergence-divergence-macd-indicator/) |
| lab-09 | MACD histogram turn | 1m | Negative histogram turns upward above EMA50. | [Reference](https://www.tradingview.com/support/solutions/43000502344-moving-average-convergence-divergence-macd-indicator/) |
| lab-10 | RSI midpoint trend | 1m | RSI14 crosses 50 above EMA50. | [Reference](https://www.tradingview.com/support/solutions/43000502338-relative-strength-index-rsi/) |
| lab-11 | RSI extreme recovery | 5m | RSI14 crosses back above 30. | [Reference](https://www.tradingview.com/support/solutions/43000502338-relative-strength-index-rsi/) |
| lab-12 | RSI2 trend recovery | 1m | RSI2 crosses back above 10 above EMA50. | [Reference](https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/rsi-2) |
| lab-13 | RSI trend pullback | 1m | RSI14 crosses back above 40 above EMA50. | [Reference](https://www.tradingview.com/support/solutions/43000502338-relative-strength-index-rsi/) |
| lab-14 | RSI momentum expansion | 5m | RSI14 crosses above 60 with a prior-high breakout. | [Reference](https://www.tradingview.com/support/solutions/43000502338-relative-strength-index-rsi/) |
| lab-15 | Stochastic extreme recovery | 1m | Raw stochastic K14 crosses back above 20. | [Reference](https://www.tradingview.com/support/solutions/43000502332-stochastic-stoch/) |
| lab-16 | Stochastic signal cross | 1m | K14 crosses its SMA3 while previous K <20. | [Reference](https://www.tradingview.com/support/solutions/43000502332-stochastic-stoch/) |
| lab-17 | Stochastic midpoint trend | 5m | K14 crosses 50 above EMA50. | [Reference](https://www.tradingview.com/support/solutions/43000502332-stochastic-stoch/) |
| lab-18 | CCI momentum break | 5m | CCI20 crosses above +100. | [Reference](https://www.tradingview.com/support/solutions/43000502001-commodity-channel-index-cci/) |
| lab-19 | CCI zero trend | 1m | CCI20 crosses zero above EMA50. | [Reference](https://www.tradingview.com/support/solutions/43000502001-commodity-channel-index-cci/) |
| lab-20 | CCI extreme recovery | 5m | CCI20 crosses back above -100. | [Reference](https://www.tradingview.com/support/solutions/43000502001-commodity-channel-index-cci/) |
| lab-21 | ROC zero cross | 1m | ROC10 crosses zero. | [Reference](https://www.tradingview.com/support/solutions/43000502343-rate-of-change-roc/) |
| lab-22 | ROC acceleration breakout | 1m | Positive ROC5 accelerates with prior-high breakout. | [Reference](https://www.tradingview.com/support/solutions/43000502343-rate-of-change-roc/) |
| lab-23 | Donchian20 breakout | 5m | Close exceeds prior 20 highs. | [Reference](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/price-channels) |
| lab-24 | Donchian10 volume breakout | 1m | Close exceeds prior 10 highs; volume >1.5x prior20 mean. | [Reference](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/price-channels) |
| lab-25 | Donchian midpoint reclaim | 5m | Close crosses prior20 range midpoint above EMA50. | [Reference](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/price-channels) |
| lab-26 | Donchian failed breakdown | 1m | Low sweeps prior20 low; bullish close reclaims it. | [Reference](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/price-channels) |
| lab-27 | Bollinger continuation | 5m | Close crosses above upper SMA20 +2 sigma band. | [Reference](https://www.tradingview.com/support/solutions/43000501840-bollinger-bands-bb/) |
| lab-28 | Bollinger re-entry | 1m | Close crosses back inside lower SMA20 -2 sigma band. | [Reference](https://www.tradingview.com/support/solutions/43000501840-bollinger-bands-bb/) |
| lab-29 | Bollinger midpoint trend | 1m | Close crosses SMA20 above EMA50. | [Reference](https://www.tradingview.com/support/solutions/43000501840-bollinger-bands-bb/) |
| lab-30 | Bollinger contraction breakout | 5m | Prior band width <1% and close exceeds prior20 highs. | [Reference](https://www.tradingview.com/support/solutions/43000501840-bollinger-bands-bb/) |
| lab-31 | Keltner continuation | 5m | Close crosses above EMA20 +1.5 ATR14. | [Reference](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/keltner-channels) |
| lab-32 | Keltner re-entry | 1m | Close crosses back inside EMA20 -1.5 ATR14. | [Reference](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/keltner-channels) |
| lab-33 | Keltner center bounce | 1m | Low touches EMA20; bullish close above EMA20 above EMA50. | [Reference](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/keltner-channels) |
| lab-34 | BB KC squeeze release | 5m | BB20 leaves KC20 after containment; close exceeds prior high. | [Reference](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/ttm-squeeze) |
| lab-35 | NR4 breakout | 1m | Previous bar is narrowest of four; close breaks its high. | [Reference](https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/narrow-range-day-nr7) |
| lab-36 | NR7 breakout | 5m | Previous bar is narrowest of seven; close breaks its high. | [Reference](https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/narrow-range-day-nr7) |
| lab-37 | Inside bar breakout | 1m | Previous bar inside mother bar; close breaks mother high. | [Reference](https://chartschool.stockcharts.com/table-of-contents/chart-analysis/candlestick-charts/candlestick-pattern-dictionary) |
| lab-38 | Double inside breakout | 5m | Two nested inside bars; close breaks original mother high. | [Reference](https://chartschool.stockcharts.com/table-of-contents/chart-analysis/candlestick-charts/candlestick-pattern-dictionary) |
| lab-39 | Outside bar follow-through | 1m | Previous bullish bar engulfs prior range; close breaks its high. | [Reference](https://chartschool.stockcharts.com/table-of-contents/chart-analysis/candlestick-charts/candlestick-pattern-dictionary) |
| lab-40 | Body engulfing trend | 5m | Bullish body engulfs previous bearish body above EMA50. | [Reference](https://chartschool.stockcharts.com/table-of-contents/chart-analysis/candlestick-charts/candlestick-pattern-dictionary) |
| lab-41 | Rolling VWAP reclaim | 1m | Close crosses rolling20 typical-price VWAP. | [Reference](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/volume-weighted-average-price-vwap) |
| lab-42 | Rolling VWAP bounce | 1m | Low touches VWAP20; bullish close above VWAP20 above EMA50. | [Reference](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/volume-weighted-average-price-vwap) |
| lab-43 | VWAP band re-entry | 5m | Close re-enters rolling20 VWAP -2 weighted sigma band. | [Reference](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/volume-weighted-average-price-vwap) |
| lab-44 | VWAP volume breakout | 1m | Prior-high breakout above VWAP20; volume >1.5x prior20 mean. | [Reference](https://www.tradingview.com/support/solutions/43000591617-volume/) |
| lab-45 | OBV channel break | 5m | OBV exceeds its prior20 maximum; bullish candle. | [Reference](https://www.tradingview.com/support/solutions/43000502593-on-balance-volume-obv/) |
| lab-46 | OBV price confirmation | 1m | Price and OBV both exceed prior10 maxima. | [Reference](https://www.tradingview.com/support/solutions/43000502593-on-balance-volume-obv/) |
| lab-47 | MFI extreme recovery | 5m | MFI14 crosses back above 20. | [Reference](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/money-flow-index-mfi) |
| lab-48 | MFI midpoint trend | 1m | MFI14 crosses 50 above EMA50. | [Reference](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/money-flow-index-mfi) |
| lab-49 | Chaikin flow trend | 5m | CMF20 crosses zero above EMA50. | [Reference](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/chaikin-money-flow-cmf) |
| lab-50 | ATR expansion breakout | 1m | Range >1.5 prior ATR14; close breaks prior high above EMA50. | [Reference](https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/narrow-range-day-nr7) |

## Research reading and design decisions

The original readings include daily stock strategies and indicator definitions. Moving to crypto minute bars changes the experiment and invalidates any transfer of published performance. Sources were used to define testable concepts, not to endorse profit claims.

| Reading | What informed the library | Our limitation/adaptation |
|---|---|---|
| [ma](https://www.tradingview.com/support/solutions/43000502589-moving-averages/) | Trend, price/average and fast/slow average crosses. | Periods9/21/50 and10/30 are fixed hypotheses; no optimization. |
| [macd](https://www.tradingview.com/support/solutions/43000502344-moving-average-convergence-divergence-macd-indicator/) | Fast/slow averages and signal/histogram momentum. | 12/26/9, closed-bar cross or turn; no divergence prediction. |
| [rsi](https://www.tradingview.com/support/solutions/43000502338-relative-strength-index-rsi/) | Bounded momentum and threshold changes. | Fixed14-period thresholds, not assumed universal. |
| [rsi2](https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/rsi-2) | Short oscillator pullbacks within trends. | EMA50 replaces daily SMA200; reversal confirmation and mandatory stop retained. |
| [stoch](https://www.tradingview.com/support/solutions/43000502332-stochastic-stoch/) | Closing location within recent price range. | Raw14-period K and3-period D; related extreme/midpoint tests. |
| [cci](https://www.tradingview.com/support/solutions/43000502001-commodity-channel-index-cci/) | Typical-price deviation normalized by mean deviation. | Fixed20-period thresholds; no claim every extreme reverses. |
| [roc](https://www.tradingview.com/support/solutions/43000502343-rate-of-change-roc/) | Percentage change over a specified lag. | 5/10-period variants are experimental scalping adaptations. |
| [channel](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/price-channels) | Prior-bar high/low channels and midpoint. | Exclude trigger candle from all breakout boundaries. |
| [bb](https://www.tradingview.com/support/solutions/43000501840-bollinger-bands-bb/) | SMA bands widened by price standard deviation. | 20/2 fixed; compare continuation versus re-entry separately. |
| [kc](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/keltner-channels) | EMA plus ATR channel. | EMA20 plus/minus1.5 ATR14; fixed baseline, not original full strategy. |
| [squeeze](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/ttm-squeeze) | Volatility compression using Bollinger/Keltner containment. | Our release plus prior-bar break replaces proprietary momentum and exits. |
| [nr](https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/narrow-range-day-nr7) | Narrow range followed by expansion. | Adapt daily NR4/NR7 to minute bars; strict narrowest rule. |
| [candle](https://chartschool.stockcharts.com/table-of-contents/chart-analysis/candlestick-charts/candlestick-pattern-dictionary) | Inside, outside and engulfing structures. | Explicit body/range comparisons replace subjective chart identification. |
| [vwap](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/volume-weighted-average-price-vwap) | Volume-weighted typical-price reference. | Rolling20 rather than exchange session or discretionary anchor. |
| [obv](https://www.tradingview.com/support/solutions/43000502593-on-balance-volume-obv/) | Cumulative signed volume for confirmation. | Only past-window extrema; not true bid/ask order flow. |
| [mfi](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/money-flow-index-mfi) | Typical-price direction weighted by volume. | 14-period crossing tests; no divergence extrapolation. |
| [cmf](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/chaikin-money-flow-cmf) | Close location weighted by volume. | 20-period midpoint cross plus EMA50 trend. |
| [volume](https://www.tradingview.com/support/solutions/43000591617-volume/) | Participation can confirm or exhaust price moves. | 1.5x prior20 mean is a hypothesis; volume alone never triggers. |

## How to compare

Open Backtest & Research, upload one coin's closed-candle CSV, verify quantity step/order minimums, enter realistic costs, then choose **Compare all 50 new candidates**. To include existing candle strategies choose **Compare library including existing strategies** (77 entries, including the19 requested combinations). Order-flow is excluded because candle data cannot reproduce it.

Runs execute sequentially with visible progress. Stop ends the queue after the current experiment. A failure stops the queue and preserves completed rows. Download all experiments before leaving the tab. Export includes planned IDs, execution status, each report, source specification, costs, data fingerprint and trade ledger. No automatic server experiment database exists.

Every candidate receives the same three chronological periods, initial capital, fees, spread and slippage; each also runs doubled spread/slippage. Comparisons are independent accounts and cannot be summed as simultaneous portfolio returns. With a10USDT account, zero fills may reflect exchange minimums rather than strategy quality.

## Selection protocol before real funds

1. Treat current comparisons as exploration. Record every trial, including rejected candidates and parameter changes.
2. Lock candidate rules and costs before testing new unseen dates. Test multiple coins and trend/range/volatile regimes.
3. Require positive net expectancy across sufficient independent observations, tolerable drawdown, and resilience to costs. No fixed trade count guarantees confidence.
4. Inspect losses, execution assumptions and parameter sensitivity. Do not select by win rate alone or delete failed experiments.
5. Validate remaining candidates in forward shadow execution and testnet operational tests. Testnet fills are not evidence of live liquidity.
6. Promote only through an explicit future implementation/review; the lab registry cannot place orders.

Testing many alternatives raises selection bias: see [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf). The UI does not currently calculate a multiple-testing-adjusted confidence measure and does not award pass/fail profitability badges.

## Important limits

Maximum10,000 one-minute candles per run is under seven days. These are short experiments, not multi-regime validation. Funding, liquidation, tick rounding, historical exchange-rule changes, partial fills, latency, real depth and the full live scanner are not reproduced. Drawdown is closed-equity only. Entry families share a common ATR/2R exit to isolate trigger differences; results are not reproductions of the cited original strategies. The existing eight baselines retain their own exits and history needs, so their comparison is across complete implemented patterns rather than entry-only differences.

No strategy has been declared profitable by this implementation. Software tests verify behavior, not market edge.
