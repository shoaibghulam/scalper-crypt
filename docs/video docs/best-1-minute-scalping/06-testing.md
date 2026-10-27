# Backtesting and acceptance tests

No backtest has been run for this strategy as part of creating these documents. The video's example is a qualitative illustration, not a performance dataset.

## Research protocol

Freeze the v0.1 rules and parameter version before evaluating results. Use chronological development, validation, and untouched test periods, with boundaries chosen before tuning. Report instrument coverage, dates, bar sources, missing-data handling, fees, slippage, funding, and fill assumptions. Evaluate different volatility and trend conditions rather than selecting only attractive charts.

Run the same feature/setup logic in replay and live simulation. Restrict all pivots to their confirmation times, zones to their displacement-close availability, and targets to information known before the rejection bar. Never assume an entry at the signal bar's earlier low or exact close.

Use the next available quote/trade for execution. For candle-only research, entry is the next bar open plus the chosen execution-cost model; explain that this cannot faithfully test a five-second submission window or two-second quote freshness.

## Intrabar ambiguity

If stop and target are both inside a candle, OHLC alone cannot determine the first fill. Prefer finer event data. Otherwise classify the trade as ambiguous and report a pessimistic stop-first result plus a separate sensitivity result; do not silently choose target first.

If a bar contains both the breakeven activation level and a possible stop touch, the path is also unknown. With only OHLC, apply management changes on the next bar as a documented alternate simulator convention. Do not present those results as equivalent to event-driven execution. Protective orders already active at bar open are still simulated during that bar.

Price touching a limit target does not guarantee a fill. State any optimistic touch-fill convention and test a stricter fill model. Include adverse gap fills beyond stops and latency before protection/amendment acknowledgment.

## Required test cases

| Area | Test | Expected result |
|---|---|---|
| Pivots | A high has only one right-side candle | Not available yet |
| Pivots | Equal highs or lows | Strict pivot rejected |
| Trend | Rising highs but falling lows | No bullish structure |
| Availability | Zone base exists but displacement has not closed | No zone available |
| Zone selection | Multiple valid base lengths | Longest qualifying base chosen |
| Liquidity | Eligible pivot exists only after arming | Cannot retrofit it |
| Sweep | Low equals reference, without one-tick penetration | No sweep |
| Rejection | Sweep occurs but close remains inside zone | No entry |
| Volume | Signal bar is very large | Excluded from its own 20-bar baseline |
| First touch | First touch fails, second touch looks good | Zone remains consumed |
| Invalidation | Close below frozen structural low | Armed setup invalidated |
| Target | High is confirmed during signal bar | Ineligible under pre-signal target rule |
| Sizing | Rounded size is below minimum | Skip; do not increase size |
| ATR | Hand-calculated seed and update | Exact expected ATR within chosen precision |
| Entry | Quote drifts beyond 0.1 ATR | Reject before submitting |
| Fill | Partial entry and protection rejection | Protect/close filled amount; enter recovery |
| Idempotency | Entry response times out after fill | Reconcile, no duplicate entry |
| Breakeven | Amendment rejected | Old stop remains recorded as active |
| Execution | Exit fills during sibling cancellation | Flatness reconciled; no reverse position |
| Replay | Both stop and target touched | Ambiguity flagged; conservative treatment |
| Restart | Existing position survives process restart | Restore original R and protection |
| Data | Missing or duplicate bar | No duplicate signal; gap blocks entries |
| Limits | Daily halt active | No new entries |

## Numerical fixture

Use invented values `zone=[99.70,99.90]`, `P=99.92`, tick `0.01`, and rejection candle `O=99.96, H=100.10, L=99.85, C=100.08`. The candle touches demand, sweeps P, and reclaims both P and the zone top. Its close location is `0.23/0.25=0.92`; its lower-wick ratio is `0.11/0.25=0.44`. If volume is 180 against a prior mean of 100, the volume ratio is 1.8. With ATR 0.20 and target 100.50, reference gross reward/risk is 2.1.

This is only a predicate fixture. Supply valid prior pivots, arming context, freshness, and execution inputs before treating it as a complete eligible trade. A failure fixture can keep every value the same but reduce volume to 100; expected result is no entry.

## Reports and gates

Report trade count, net expectancy, win/loss/breakeven counts, average win/loss, profit factor, drawdown, exposure time, losing streaks, actual reward/risk distribution, and costs as a proportion of gross profit. Separate gross-price breakeven exits from net-profitable exits. Include rejected setup counts by reason and uncertainty around estimates.

Implementation acceptance requires deterministic replay, passing failure/recovery cases, and no known future-data access. Performance acceptance needs separately specified evaluation thresholds and enough data to assess uncertainty; do not invent a passing win rate from the clip. Successful tests do not establish profitability.

After research, compare paper events against replay over the same market period. Investigate signal, fill, and protection differences before considering any live release.
