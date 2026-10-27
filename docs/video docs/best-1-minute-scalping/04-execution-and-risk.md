# Execution and risk model

This file contains proposed engineering requirements. The video supplies a one-ATR stop and recent-high target, but supplies no position-sizing or exchange-integration specification. Numbers below are simulation inputs, not validated live recommendations.

## Price and quantity model

For a linear quote-margined instrument, let:

- `E` = actual weighted entry price.
- `A` = frozen signal ATR.
- `S` = initial stop trigger, `floor_to_tick(E - A)`.
- `T` = recent-high target, rounded down to a valid tick.
- `D` = `E - S`, required to be positive.
- `B` = configured quote-currency risk budget.
- `c_loss` = estimated fees and adverse execution allowance per base unit for the stop scenario.

The stop rounding can increase the distance slightly beyond one ATR. Size using the rounded distance, not the unrounded ATR. Quantity is `floor_to_step(B / (D + c_loss))`, limited further by available margin, exposure caps, and venue limits. Reject a quantity below minimum size/notional; do not round upward to force a trade.

Pre-trade sizing uses an adverse permissible entry estimate. Recalculate exposure on fills. Never add quantity to compensate for an unexpectedly worse fill. Inverse contracts or contracts with multipliers need a different P&L model and are outside this baseline.

## Costs and economics

Load fee assumptions and instrument rules from versioned inputs. Do not copy a rate or API endpoint from an old document without checking it during implementation. Account for entry/exit fees, spread, slippage, and funding when applicable. Avoid counting spread twice if the simulator already fills buys at ask and sells at bid.

Estimated target net profit per unit is `T - E - c_target`; estimated stop loss per unit is `E - S + c_loss`. Require positive estimated net target profit in addition to the gross reward/risk gate in the specification. Report both gross and net results.

For example, invented prices `E=100`, `A=0.20`, `S=99.80`, and `T=100.40` give gross reward/risk of 2. If estimated round-trip cost is 0.08 per unit for either outcome, the estimated target profit is 0.32 and stop loss is 0.28, a net ratio of about 1.14. This arithmetic is illustrative, not a price forecast or an assumed exchange fee.

## Order lifecycle

1. Check symbol eligibility, continuous data, fresh bid/ask, permitted trading mode, risk caps, and signal age.
2. Persist a unique intent before order submission. Derive the client order ID from strategy version, symbol, and signal timestamp.
3. If submission times out, query/reconcile by client ID before retrying. An unknown response is not proof that the order failed.
4. Track partial fills individually and protect the filled quantity promptly. Cancel the unfilled remainder when its permitted lifetime ends.
5. Confirm exchange-held protective stop and target orders with the correct closing direction and position side. Use reduce-only/close-position semantics supported by the venue.
6. If protection fails, enter recovery, stop new entries, and attempt to close the exposed quantity. Record and surface the failure.
7. When one exit fills, cancel/reconcile its sibling. A cancellation race must not create a reverse position.
8. Persist final fills, costs, and remaining position quantity. Return to searching only after flatness is confirmed.

The diagram's `OPEN` state assumes protection is confirmed; a partial or unprotected fill remains an execution/recovery responsibility even before that state.

## Breakeven behavior

The baseline trigger is a move of 1 initial R, an added definition for the video's “moves in my favour.” Move the stop to entry only after an accepted amendment/replacement. Use the venue's designated trigger price source consistently in live trading and replay; executable bid-based triggering is the initial simulation convention for a long position.

A stop at entry is gross-price breakeven. A cost-adjusted stop would be a distinct variant and may be impossible to place at the current price. Keep the old protection active until the replacement is confirmed where supported. If atomic amendment is unavailable, explicitly handle the overlap or unprotected interval; never claim the change succeeded from a request alone.

## Proposed simulation controls

| Control | Baseline |
|---|---|
| Mode | Paper/simulation |
| Risk budget per new trade | 0.25% of current simulated equity |
| Concurrent positions | One across this strategy |
| Total allocated initial risk | At most 0.5% of equity |
| Daily loss halt | 1% of UTC day-start equity |
| Entry quote freshness | No older than two seconds |
| Entry drift | At most 0.1 signal ATR |
| Re-entry | Never on the same zone |

Daily-loss monitoring includes realized net P&L and unrealized marked P&L. Reaching the limit stops new entries and requests orderly closure of existing strategy exposure; hard protective orders remain active. Persist the halt until the next UTC day. Data outages block new entries but do not cancel protective exits.

Leverage is not a substitute for risk sizing. A venue adapter must check available margin and liquidation constraints before any live deployment; this document does not prescribe leverage.
