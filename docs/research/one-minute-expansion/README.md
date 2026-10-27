# 20 additional one-minute research strategies

Research date: 2026-09-19. Status: experimental, testnet-only. This adds 20 new
IDs (`minute-01` through `minute-20`) to the existing 79, for 99 trading strategies.
The candle backtest catalog contains 98; order-flow requires separate event data.
These numbers identify candidates, not a performance ranking.

## What the sources establish

We read original educator articles and indicator documentation, including
[Adam Grimes on snap pullbacks](https://www.adamhgrimes.com/selective-intraday-trades-a-deep-dive-into-the-snap-pullback/),
[Al Brooks on price-action patterns](https://www.brookstradingcourse.com/price-action/10-best-price-action-trading-patterns/),
[Alan Hull's HMA explanation](https://alanhull.com/the-hull-moving-average/),
and the specific ChartSchool, Fidelity and Schwab references linked below.
They establish pattern concepts and indicator definitions. They do not validate
these exact one-minute crypto algorithms, parameters, costs or returns. We found
no basis for calling these the 20 most profitable scalping systems.

Grimes explicitly notes the difficulty of programming every variation of a
snap pullback. Our implementations therefore test narrow, fixed sequences rather
than claim to reproduce his discretionary judgment. Detailed source-to-code
mappings and formula choices are in [indicator research](indicator-research.md)
and [price-action research](pattern-research.md).

## Library integration

- **Create Scalp:** each new strategy is selectable on testnet.
- **Auto Trade:** all enabled candidates are included on the next explicit fleet
  start. A running fleet is not silently expanded. Stop entries, then start again
  to include new candidates; existing positions and trade records stay managed.
- **Strategy Monitor:** all new IDs appear automatically with separate records;
  existing manual/automatic disable rules apply.
- **Single Coin:** new candidates must earn the same 20 verified trades,
  positive net profit and eligibility checks before ranking.
- **Backtest & Research:** choose a coin and select **Compare 20 additional
  one-minute strategies**. One downloaded dataset is reused for every candidate.
  The individual IDs and full-library comparison are also available.

## Common execution rules and explicit adaptations

All signals use completed one-minute candles. The engine revalidates a current
signal before execution; replay uses its existing next-bar execution convention.
The wrapper requires 200 consecutive one-minute bars and the pipeline's 100
five-minute context bars. The new signal predicates use only the one-minute bars.

Shared bracket: stop distance = max(1.5 times Wilder ATR14, 0.45% of entry);
target distance = twice the stop distance, before costs. This shared experimental
bracket replaces any source-specific exit suggestions. No indicator-cross exit,
structural-stop exit, or fixed 12-minute exit is added by this change. Candle
interval does not impose trade duration. Existing fee, spread, liquidity,
slippage, position-size and account limits remain in force.

Long and short entries are defined separately or by an explicitly symmetric
price transformation. A pattern requiring later confirmation is not stamped at
an earlier pivot. Invalid candles, gaps, insufficient warm-up and ambiguous
simultaneous directions do not produce entries. Broad teacher concepts have been
translated into bounded, deterministic sequences with fixed parameters; any
simplification is documented alongside the implemented rule.

## Avoiding misleading comparisons

These are 20 different predicates, not 20 independent sources of return.
TRIX overlaps conceptually with MACD; CMO is closely related to an unsmoothed RSI;
several price patterns share trend and reversal exposure. Distinct IDs preserve
results without treating correlated winners as independent evidence.

Use identical symbols, dates and transaction-cost assumptions. Compare out-of-
sample net returns, drawdowns, trade counts and cost-stress results. Preserve
losing trials as well as winners. Do not tune parameters on the same period used
to select winners. Twenty trades is an operational review threshold, not proof
of an edge. Rare patterns should remain inactive until their conditions occur;
we do not relax thresholds merely to generate more trades.

## Research method and exclusions

Existing recipes were inventoried first to avoid simply renaming EMA/VWAP/BB,
RSI, MACD, Donchian, NR4/NR7 and basic candle entries. Parallel source reviews
covered indicator formulas and trader-authored price patterns. The built-in web
browser was used because Exa/Firecrawl connectors were not available. Source
links and exact implemented hypotheses are retained in the two research notes.

DPO was considered and excluded: its usual displaced plot is not a current-time
momentum signal, making naive historical triggering vulnerable to look-ahead.
[ChartSchool DPO](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/detrended-price-oscillator-dpo).
Unread or inaccessible material was not used as evidence. No paid course content
or third-party claimed win rate is treated as a verified crypto result.

## Added candidates

All use 1m entry candles. Exact predicates are in the two research notes.

| ID | Candidate | Source |
| --- | --- | --- |
| `minute-01` | Aroon consolidation release | [Article](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/aroon) |
| `minute-02` | Vortex threshold transition | [Article](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/vortex-indicator) |
| `minute-03` | True Strength reacceleration | [Article](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/true-strength-index) |
| `minute-04` | TRIX momentum transition | [Article](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/trix) |
| `minute-05` | Ultimate confirmed divergence | [Article](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/ultimate-oscillator) |
| `minute-06` | Chande extreme recovery | [Article](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/cmo) |
| `minute-07` | Fisher turn confirmation | [Article](https://toslc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/E-F/FisherTransform) |
| `minute-08` | Hull turning point | [Article](https://alanhull.com/the-hull-moving-average/) |
| `minute-09` | Elder Force correction | [Article](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/force-index) |
| `minute-10` | Mass reversal bulge | [Article](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/mass-index) |
| `minute-11` | Microchannel first pullback | [Article](https://onlinelibrary.wiley.com/doi/10.1002/9781119202592.ch16) |
| `minute-12` | High-2 second attempt | [Article](https://www.brookstradingcourse.com/price-action/10-best-price-action-trading-patterns/) |
| `minute-13` | Snap Anti pullback | [Article](https://www.adamhgrimes.com/selective-intraday-trades-a-deep-dive-into-the-snap-pullback/) |
| `minute-14` | Nested impulse pause | [Article](https://www.adamhgrimes.com/patterns-within-patterns/) |
| `minute-15` | Three-push second entry | [Article](https://www.brookstradingcourse.com/price-action/10-best-price-action-trading-patterns/) |
| `minute-16` | Broken-trend extreme retest | [Article](https://www.brookstradingcourse.com/price-action/10-best-price-action-trading-patterns/) |
| `minute-17` | Final flag failure | [Article](https://www.brookstradingcourse.com/price-action/10-best-price-action-trading-patterns/) |
| `minute-18` | Body-gap retest | [Article](https://www.brookstradingcourse.com/price-action-trading-terms-glossary/) |
| `minute-19` | Failed-failure continuation | [Article](https://www.brookstradingcourse.com/price-action-trading-terms-glossary/) |
| `minute-20` | Delayed failure-test hold | [Article](https://www.adamhgrimes.com/failure-test-2/) |

## Verification performed

- All 20 candidates have reachable long and short synthetic trigger fixtures.
- Tests exercise missing confirmation, flat/invalid inputs, warm-up, direction
  ambiguity, causal pivot timing, history gaps and inherited bracket geometry.
- Synthetic 1,400-bar replay smoke tests completed for each of the 20 IDs;
  some rare predicates had zero trades, which is not evidence of profitability
  or failure. This was an execution check, not a strategy selection exercise.
- A read-only testnet scanner check completed 99 strategies over 528 active
  contracts (111 passed liquidity/spread checks) in 20.77 seconds on this machine.
  It submitted no orders. Counts and elapsed time vary with market conditions.
- Existing fees, execution checks, testnet-only registration and Strategy Monitor
  history IDs are preserved. Full test results are reported with delivery.
