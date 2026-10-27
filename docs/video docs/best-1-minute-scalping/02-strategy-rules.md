# Strategy rules

## Observed discretionary workflow

1. Identify an uptrend with higher highs and higher lows.
2. Mark a demand area at a pause preceding strong upward continuation.
3. Identify a clear nearby low with presumed liquidity below it, near the demand area.
4. Wait for price to return to that area and reject sharply.
5. Look for increased volume around the rejection.
6. Enter long after confirmation.
7. Use a one-ATR stop distance.
8. Target the most recent high.
9. Move the stop to entry once price moves favourably.
10. Abandon a response that fails to reject as expected; the timing is discretionary in the clip.

Evidence: [timestamped review](01-video-review.md).

## Terms used in these documents

| Term | Meaning |
|---|---|
| Demand zone | A horizontal interval around a base preceding a bullish departure |
| Base | A brief pause or compact group of candles |
| Displacement | A sufficiently strong upward departure from the base |
| Liquidity reference | A previously visible low associated with the demand area |
| Sweep | A later traded low below that reference, followed by recovery |
| Rejection | Price enters the area and finishes back above a defined reference |
| ATR | Average true range; used here to express stop distance |
| Recent high | A pre-entry swing high selected as the profit objective |
| R | Initial entry-to-stop distance per unit, fixed for trade reporting |

“Imbalance” in this video does not establish a mandatory three-candle fair-value-gap formula. “Demand” also does not establish a specific order-block indicator or body-only boundary rule.

## Decisions needed for automation

| Missing detail | Proposed baseline | Status |
|---|---|---|
| Swing detection | Strict pivots with two bars on each side | Proposed |
| Candle type | Standard OHLC candles | Proposed; no Heikin Ashi instruction |
| Base | One to three compact candles before displacement | Proposed |
| Zone boundaries | Full wick range of the base | Proposed |
| Displacement | Bullish body at least 1.5 times prior ATR | Proposed |
| Liquidity relationship | Prior confirmed low near the zone's upper edge | Interpretation formalized as a proposal |
| Mandatory sweep | Require sweep and reclaim on the rejection bar | Proposed stricter interpretation |
| Rejection shape | Bullish close, close in top 35%, lower wick at least 25% | Proposed |
| Volume increase | At least 1.5 times mean of previous 20 bars | Proposed |
| ATR settings | 14-period Wilder ATR on one-minute candles | Proposed |
| Entry timing | First executable price after confirmation bar closes | Proposed |
| Stop anchor | Actual entry minus frozen signal-bar ATR | Proposed; one ATR is observed |
| Target selection | Latest eligible confirmed pivot high known before rejection | Proposed; recent high is observed |
| Breakeven trigger | Favourable move of 1 initial R | Proposed |
| Failed reaction | Close back below reclaimed low, or time failure | Proposed |
| Short trades | Disabled | Only long setup documented in source |

These choices define a research baseline, not a recovered proprietary formula. A different choice changes the strategy and must receive a distinct configuration/version.

## When a setup should not become an entry

Under the proposed baseline, skip if structure is not bullish at arming, no eligible demand/liquidity pair exists, the zone has already been used, the first touch fails confirmation, the target is below entry, volume or ATR history is missing, or the order would violate configured execution limits. Reaching demand alone is insufficient.

Use the [deterministic specification](03-bot-specification.md) as the authoritative definition of these added rules.
