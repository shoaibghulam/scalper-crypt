# Advanced five-minute PDF: implementation and data requirements

Source: [10_advanced_5_minute_crypto_strategies.pdf](../../../new%20strategies/10_advanced_5_minute_crypto_strategies.pdf), September 2026, 15 pages.

Seven candle-based strategies from pages 3–9 are implemented as **Advanced 5m:** entries. The three remaining setups are listed in the dashboard with their missing data/simulation requirements. They are not selectable, registered for trading, or replaced by candle proxies.

These are deterministic adaptations of research templates. Neither the source nor this implementation establishes profitability.

## Implemented entries

| PDF / ID | Conditions | Exit choice |
|---|---|---|
| 1 / `advanced5-ema-adx` | Closed 15m EMA50/200 trend; 5m EMA20/50; ADX >20; EMA20 pullback/reclaim; RVOL >1.2 | Full 2R target |
| 2 / `advanced5-donchian-rvol` | Close outside prior 20-bar high/low and beyond EMA50; ATR above five bars ago; RVOL >=1.5; close in directional outer quartile | Full 1.5R target |
| 3 / `advanced5-squeeze` | Three prior BB(20,2) envelopes inside KC(EMA20,1.5 ATR); close beyond both current envelopes and session VWAP; rising ATR; RVOL >=1.3 | Full 2R target |
| 4 / `advanced5-stochrsi` | Closed 15m price vs EMA200; 5m VWAP/EMA50 alignment; StochRSI reset and K/D cross; prior-bar high/low breakout | Full 2R target |
| 5 / `advanced5-macd-vwap` | MACD line keeps its sign; histogram contracts twice then expands; VWAP/EMA20 holds; three-bar micro-breakout; RVOL >=1.2 | Full 2R target |
| 6 / `advanced5-ichimoku` | Price beyond visible cloud; aligned Tenkan/Kijun and projected cloud; closed 15m cloud/TK confirmation; Kijun pullback/reclaim; volume above median | Full 2R target |
| 7 / `advanced5-sweep-reclaim` | Prior 20-bar range sweep and close back inside; later first micro-structure break; VWAP reclaimed; RVOL >=1.2 | Frozen range midpoint |

## Causal data and indicator rules

- Signals use completed 5-minute bars. Execution uses the next executable price, including modeled costs; entry-price validation uses that price.
- Fifteen-minute context is reconstructed only from complete, contiguous UTC-aligned groups of three 5-minute candles. Partial groups are discarded.
- Setups 1 and 4 load **615 five-minute bars / 3,075 one-minute warm-up candles**. This accommodates EMA200, slope checks, and alignment losses. Research request validation now permits up to 5,000 warm-up minutes. The 10,000-candle detailed-upload limit remains unchanged; provide at least warm-up +700 candles.
- VWAP uses complete UTC-day history, volume-weighted 5-minute typical prices, and resets at midnight. It is not rolling VWAP or trade-level VWAP. Setups that compare multiple VWAP observations require enough candles after the reset for all observations.
- StochRSI(14,14,3,3): Wilder RSI14, rolling 14-RSI min/max normalization, SMA3 %K and SMA3 %D, on a 0–1 scale. A flat normalization window is neutral 0.5.
- MACD(12,26,9): correctly aligned EMA12/26 difference and EMA9 signal.
- Ichimoku(9,26,52): Tenkan/Kijun are high/low midpoints. The visible cloud is computed from the prefix ending **26 bars before the current bar**; the projected cloud uses current completed history. No future candles or backward-plotted Chikou values are used as signals. At least 78 completed bars are required on either timeframe.
- RVOL uses trigger volume divided by the **preceding** 20 completed bars' mean, excluding the trigger. Ichimoku uses their median, also excluding the trigger.
- ATR14 and ADX14 use existing Wilder indicator implementations. Both long and short directions are supported.

## Explicit discretionary choices

The PDF leaves numerical definitions and alternative exits open. These choices were fixed before any profitability evaluation:

1. EMA/ADX: higher-timeframe EMA50 must change by >0.1 higher-timeframe ATR over three candles, and EMA200 must move in the same direction. Reject more than two EMA20/50 direction flips across ten transitions. The previous candle touches within 0.1 ATR of EMA20 and closes no more than 0.25 ATR beyond it, while staying on the trend side of EMA50. Trigger closes directionally across EMA20. Structural stop reference is the last four candles; minimum ATR distance is 1.2 ATR.
2. Donchian: use prior highs/lows, not closes; outer-quartile close location is mandatory. Stop beyond the trigger's opposite extreme with an ATR buffer and at least one ATR distance. Optional 15m EMA filter is omitted.
3. Squeeze: require exactly the preceding three candles to have both Bollinger bands inside Keltner; release must exceed both current envelopes. ATR must exceed the previous value, and trigger high-low range must not exceed two current ATR. Structural reference is the last four candles.
4. StochRSI: require at least one %K reading below 0.20 / above 0.80 in the preceding three oscillator observations, followed by a current K/D cross. Higher-timeframe direction comes from its last close relative to EMA200. Structural reference is the last five candles.
5. MACD: all four recent MACD-line values share direction. The three preceding histogram magnitudes contract but remain on the trend side of zero; current magnitude increases. Previous two closes and trigger remain beyond their own VWAP/EMA20. VWAP's three-bar change must exceed 0.05 ATR in direction, and entry signal close must be within three ATR of VWAP. Structure uses the three pre-trigger bars. Do not compare a momentum leg across a VWAP reset.
6. Ichimoku: visible cloud thickness must be >=0.1 ATR. Projected span A must move by >0.05 ATR in direction over three bars; no more than two TK flips across ten transitions. Previous candle intersects its Kijun within 0.1 ATR; trigger has a directional body and closes beyond current Kijun. Mandatory 15m confirmation uses price beyond its visible cloud and aligned TK; the optional 15m EMA200 is omitted.
7. Sweep: freeze the 20 candles before the sweep as the range. Sweep must occur one to three bars before the trigger and close inside the range. Following bars cannot close outside the swept boundary or exceed the sweep extreme. Micro structure is the extreme of three bars immediately preceding the sweep; trigger is its first subsequent close break. This implementation requires VWAP reclaim (the PDF allows VWAP or boundary) and RVOL >=1.2. Target remains the frozen range midpoint, and the stop references the sweep extreme plus 0.1 ATR. No invented target extension to make a trade pass.

## Shared platform differences and exit limitations

The existing 0.45% minimum stop and 1.5R minimum reward/risk apply. A mean target with insufficient room is rejected. Entry displacement greater than 0.5 ATR is rejected. Stops use a 0.1 ATR structural buffer and at least one ATR distance except EMA/ADX (1.2 ATR) and sweep (structural distance only, plus platform floor). Fixed targets round toward entry; tick-rounded targets must still satisfy minimum reward/risk.

The platform currently supports a single full stop/target bracket, not partial profit-taking, trailing protection, or strategy-specific dynamic exits. Therefore these entries use the static exit choices above. The PDF's partial exits, MACD VWAP-loss exit, squeeze re-entry exit, oscillator exit, and trailing alternatives are **not implemented**. Treat these as static-exit variants of the source, not exact full-system replicas.

The source's daily stop after three losses / 1.5R–2R, news filters, discretionary 1h obstacle detection, funding, synchronized historical depth, latency stress, walk-forward optimization and full 200–300-trade validation protocol are also not added by this change. Existing testnet risk controls and backtest costs remain in place. Use the risk field to select the PDF's suggested 0.25%–0.50% rather than assuming the default changed.

## Setups that require additional infrastructure

| PDF page | Setup | Missing requirement |
|---|---|---|
| 10 | Order-book imbalance + micro-breakout | Historical top-5/10 L2 updates and aggressive trades with timestamps/sequence integrity; event-level persistence, latency and fill simulation. The existing live top-20 sampler is not this historical dataset. |
| 11 | Price + open interest + volume breakout | Timestamp-aligned OI observations at/around breakout; no OI supplied by the current candle dataset. Volume is not OI. |
| 12 | BTC–ETH pair spread mean reversion | Separate two-asset simulator and execution contract; past-only hedge estimation and stationarity tests; both-leg fees, slippage and funding; synchronized exits. It cannot be represented as a directional single-coin bot. |

These requirements are published by `/api/research/capabilities` and shown under the advanced-PDF notice in Strategy Lab. Direct candle backtest requests for these unimplemented IDs are rejected. No placeholder strategy can enter a trade.

## Verification

Tests verify catalog/discovery, longer warm-up support, unsupported-data rejection, both entry directions for all seven sets of predicates, negative confirmation controls, independent StochRSI math, Ichimoku displacement, completed-15m alignment, stop/target semantics and candle-data guards. Mocked feature fixtures isolate some signal predicates and are not historical profitability evidence.
