# One-minute price-action research candidates

Reviewed primary educational sources on 2026-09-19. These entries are narrow,
deterministic adaptations of discretionary concepts. Their parameters and
performance have not been validated on crypto. Source win rates, market examples,
and assertions of an edge are not evidence for this implementation.

## Data and execution contract

`minute_patterns.signals` consumes at least 200 completed one-minute candles and
uses the latest 200. A is Wilder ATR(14) over the first 160 of those candles,
excluding the final 40 bars so every pattern has a pre-pattern volatility scale.
It is recomputed at each assessment, not persisted as a trade's ATR. A zero ATR,
nonfinite/nonpositive OHLC, or impossible candle geometry produces no signals.
The wrapper owns closed-candle selection, continuity and freshness checks.

All decisions occur on the last supplied closed candle. Every long trigger also
requires close > open. Short rules mirror prices, including swapping highs and
lows; no special short parameter set exists. There are no future pivots, live
candles, five-minute signal inputs, learned parameters or session filters. The
eight-bar trend predicate means net open-to-last-close progress >=1.5A with at
least five of its seven close changes in the required direction. All sequence
lengths below are exact, not flexible discretionary searches.

These are entry predicates only. The common wrapper retains its stop at
max(1.5 current ATR14, 0.45% of entry), 2R target, and existing execution/cost/risk
gates. Structural stops, 12-bar exits, and extra next-open gap rejection discussed
during research are **not implemented** by this module. A closed-bar observation
does not establish that its closing price is executable; the existing execution
and backtest machinery determines fills.

## Implemented rules in registry order

1. **Microchannel first pullback.** Source: persistent no-pullback movement and its
   first correction, from the accessible publisher summary of
   [Brooks's Micro Channels chapter, Wiley](https://onlinelibrary.wiley.com/doi/10.1002/9781119202592.ch16).
   Only that summary was read; the full chapter was not accessible or used.
   Adaptation: six strictly rising lows, >=4 bullish bodies, net rise >=1.5A.
   The seventh candle undercuts the sixth low but holds the midpoint of the run's
   full low-to-high span. The eighth closes above the seventh high. Exactly one
   correction candle is allowed. There is no generic moving-average trigger.

2. **High-2 second attempt.** Source: two countertrend attempts and resumption,
   from [Brooks's High-2 explanation](https://www.brookstradingcourse.com/price-action/10-best-price-action-trading-patterns/).
   Adaptation: eight-bar uptrend, then four candles: lower high than trend end,
   H1 high above previous high, another lower high, and final close above previous
   high. All four correction lows exceed the trend's first low. This is a fixed
   four-bar version of bar counting, not the full discretionary pattern.

3. **Snap Anti pullback.** Source: first reluctant correction after a forceful
   countertrend shock, from [Grimes's intraday snap-pullback article](https://www.adamhgrimes.com/selective-intraday-trades-a-deep-dive-into-the-snap-pullback/).
   Adaptation: eight-bar downtrend, two-bar bullish shock gaining >=2A and closing
   above all eight trend highs. Two correction candles retrace 20-50% of the
   shock's first-low-to-high span. Their combined ranges are <=70% of the shock's
   combined ranges; both closes hold its midpoint. Last candle closes above both
   correction highs. The source itself notes discretion and programming limits.

4. **Nested impulse pause.** Source: brief pause within a larger move, from
   [Grimes, Patterns within patterns](https://www.adamhgrimes.com/patterns-within-patterns/).
   Adaptation: eight-bar uptrend, three-bar impulse gaining >=1.5A, three-bar pause
   with total high-low span <=0.8A holding above the impulse midpoint, then close
   above pause high. All layers use one-minute bars; the original higher-timeframe
   interpretation and measured-move target are not implemented.

5. **Three-push second entry.** Source: three-push wedge and waiting for a second
   reversal entry, from [Brooks's wedge discussion](https://www.brookstradingcourse.com/price-action/10-best-price-action-trading-patterns/)
   and [wedge-reversal glossary](https://www.brookstradingcourse.com/price-action-trading-terms-glossary/).
   Adaptation: nine candles arranged as left neighbor, first low, rebound, second
   low, rebound, third low, reversal, retest, confirmation. Each of the three lows
   is below its two immediate neighbors; descending extensions shrink. The two
   rebound highs are >=0.5A above their preceding lows. Reversal closes above the
   second rebound high. Retest undercuts reversal low but holds above third low;
   confirmation closes above retest high. The intermediate extrema are fully
   observed before entry; no future-bar labels are used.

6. **Broken-trend extreme retest.** Source: channel disruption then retest of the
   old extreme, from [Brooks's major-trend-reversal discussion](https://www.brookstradingcourse.com/price-action/10-best-price-action-trading-patterns/).
   Adaptation: eight-bar downtrend, bullish close above its last three highs,
   three-bar retest with minimum low within +/-0.35A of old trend low and all
   closes at/above it, then close above all retest highs. Breaking three highs is
   our reproducible proxy for a channel break; no fitted trendline is claimed.

7. **Final flag failure.** Source: a late continuation flag that reverses, from
   [Brooks's final-flag discussion](https://www.brookstradingcourse.com/price-action/10-best-price-action-trading-patterns/).
   Adaptation: fifteen-bar decline >=3A with >=10 lower closes, three-bar flag
   spanning <=1A and first/last close difference <=0.25A, close below flag low,
   then bullish close above flag midpoint. No external support/magnet condition
   is implemented; the source's richer contextual requirements are simplified.

8. **Body-gap retest.** Source: overlapping wicks with separated bodies, from
   [Brooks's body-gap glossary entry](https://www.brookstradingcourse.com/price-action-trading-terms-glossary/).
   Adaptation: select highest-high candle in fifteen bars before a breakout (first
   occurrence on ties). Bull breakout closes above that high and spans >=1.2A.
   The next five candle bodies remain strictly above the selected candle's body
   top. The fifth candle's low retests its high but stays no more than 0.3A below
   its body top. Final candle closes above retest high. This is a rolling-range
   anchor, not a retrospectively identified pivot.

9. **Failed-failure continuation.** Source: reversal failure returning to original
   breakout direction, from [Brooks's failed-failure glossary entry](https://www.brookstradingcourse.com/price-action-trading-terms-glossary/).
   Adaptation: freeze prior fifteen-bar high; bullish breakout close exceeds it by
   >0.15A. Next candle is bearish, next closes below that bearish candle's low,
   and last bullish close exceeds both its high and the original level. Failure
   candles' lows stay within 1A below the original level. Three successive closed
   events are required; a mere breakout or engulfing candle cannot substitute.

10. **Delayed failure-test hold.** Source: failure beyond a known level, from
    [Grimes, The Failure Test](https://www.adamhgrimes.com/failure-test-2/).
    Adaptation: twenty-bar rolling low, then four candles: close 0.1-0.5A below
    level, close above level, retest low within +/-0.15A with close above level
    and low above original excursion low, final close above retest high. The
    delayed hold and fixed four-event timing are ours; the source describes
    faster failure tests. This cannot trigger on a single-bar sweep.

[Grimes's fundamental-patterns overview](https://www.adamhgrimes.com/fundamental-trading-patterns/)
also supplies useful taxonomy separating simple/complex pullbacks, failure tests
and first corrections after a potential trend change. The primary-source set is
seven URLs. Raschke's official candlestick PDF was discovered but could not be
retrieved, so its content is not used as evidence.

## Validation and limits

Each candidate has deterministic reachable bullish and mirrored bearish fixtures,
a wick-only failed-close confirmation fixture in both directions, and a valid-OHLC
structural near-miss fixture in both directions. Tests additionally cover short
history, flat/zero-range data, malformed OHLC, nonfinite values, 200-bar truncation,
and prefix independence. These tests prove rule execution, not economic merit.

These ten predicates are not ten independent edges: several share pullback or
reversal structure and may signal on the same event. The scanner's existing
selection and risk rules handle trade admission. Fixed-length motifs can miss
visually compelling instances, and may be sparse. No trade-frequency guarantee,
backtest return, win rate, Sharpe ratio, or profitability claim is made.
