# Requested combinations: implementation guide

Added 19 candle-based research strategies. Requested #18 remains the existing `order-flow-pullback`, which requires synchronized event data and is not included in candle replay. These additions are research-only; they are not enabled in Auto Trade or the live strategy registry.

Open **Backtest & Research**, choose **Compare 19 requested candle combinations**, upload at least1,480 aligned one-minute candles, and set costs and instrument sizing. Every candidate receives780 warm-up candles in this comparison so all evaluation periods match. Comparing the original50 alone still uses500. The complete library now has77 candle strategies (50 original,19 combinations,8 existing baselines).

All new combinations use the common laboratory stop max(1.5 ATR14,0.45% entry) and2R target. The sweep combination reuses the existing sweep ENTRY checks; its exit is the common laboratory baseline. VWAP is rolling20, not session VWAP. Shorts mirror long rules. This is not proof of profitable performance.

| ID | Name | Closed-bar rule |
|---|---|---|
| user-01 | VWAP + EMA9/21 | EMA9/21 cross aligned with rolling VWAP20. |
| user-02 | EMA9/21 pullback | Previous candle touches EMA9/21 zone; trend continuation close. |
| user-03 | VWAP reclaim with volume | Previous VWAP reclaim; next close holds with 1.5x volume. |
| user-04 | EMA9 + RSI | Price reclaims EMA9 with RSI14 above50 (below50 for shorts). |
| user-05 | EMA20/50 1m pullback | 1m EMA20/50 trend; previous EMA20 touch and continuation. |
| user-06 | VWAP + RSI | VWAP20 reclaim with RSI14 midpoint confirmation. |
| user-07 | Bollinger reversal + RSI | Band re-entry with improving RSI below50 for longs. |
| user-08 | Bollinger squeeze + volume | Prior width bottom20% of120 widths; band/20-bar break and volume. |
| user-09 | EMA9 + Bollinger pullback | Previous outer-band touch; EMA9 reclaim with aligned EMA9 slope. |
| user-10 | MACD histogram acceleration | Two histogram increases above zero and prior-high breakout. |
| user-11 | VWAP + MACD | Histogram zero cross with VWAP20 bias. |
| user-12 | Confirmed RSI divergence | Two confirmed price pivots diverge from RSI; later structure break. |
| user-13 | Stochastic reversal + EMA | K14/D3 extreme cross with EMA50 bias. |
| user-14 | ADX + EMA momentum | EMA9/21 cross, ADX14 >=25 and aligned DI. |
| user-15 | Volume structure breakout | Prior20 high/low break with volume >=1.5x prior20 mean. |
| user-16 | 5m level rejection | Confirmed 5m pivot touch within0.1 ATR; 50% wick and volume. |
| user-17 | Liquidity sweep + reversal | Existing confirmed swing sweep/volume/5m context entry. |
| user-19 | 24/7 participation momentum | VWAP bias, prior20 breakout, volume >=2x; no session clock gate. |
| user-20 | 15m/5m bias + 1m entry | Closed15m and5m EMA20/50 align; 1m EMA9 reclaim. |

## Timing and limitations

- RSI divergence uses the last two confirmed2-left/2-right pivots, separated by3?40 bars, with the second at most10 bars old. Entry requires a first subsequent break of the fixed3-bar confirmation range. Recognition is never backdated.
- The15m rule aggregates only complete aligned groups of three closed5m bars; at least50 complete15m bars are required. The replay now retrieves enough5m history for this rule.
- Bollinger contraction compares the previous width against the bottom20% of120 earlier observations. This threshold is experimental.
- The participation strategy operates24/7 and uses relative volume rather than mandatory opening-session hours.
- Existing CSV replay omissions (funding, full order book, partial fills, liquidation, all live scanner gates) still apply.

Sources and the earlier gap analysis: [20-strategy research review](20-user-strategies-review.md). Keep all trials and validate finalists on unseen data before any decision about real funds.
