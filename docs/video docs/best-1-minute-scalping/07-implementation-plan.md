# Implementation plan and configuration

## Proposed baseline configuration

These are research defaults, not extracted video settings. The only numeric trading parameter explicitly recovered from the captions is the **one-ATR stop multiplier**.

| Parameter | Value |
|---|---|
| Strategy ID/version | `demand_sweep_1m` / `0.1` |
| Side / candle interval | Long only / 1 minute |
| Mode | Simulation |
| Warmup | 200 contiguous bars |
| Pivot left/right | 2 / 2 |
| ATR period / smoothing | 14 / Wilder |
| Base length | 1–3 candles, longest valid |
| Maximum base range | 1 prior ATR |
| Minimum displacement body | 1.5 prior ATR |
| Minimum displacement close location | 0.75 |
| Liquidity-pivot age | At most 30 bars at arming |
| Liquidity proximity to zone top | -0.25 to +0.5 prior ATR |
| Zone lifetime | 60 bars after displacement |
| Zone attempts | First touch only |
| Sweep depth | At least 1 tick |
| Minimum rejection close location | 0.65 |
| Minimum lower-wick fraction | 0.25 |
| Volume lookback / multiplier | 20 preceding bars / 1.5 |
| Stop multiplier | 1.0 signal ATR — observed multiplier |
| Target | Latest eligible confirmed pre-signal pivot high |
| Minimum gross reward/risk | 1.5 |
| Maximum signal submission age | 5 seconds |
| Maximum quote age | 2 seconds |
| Maximum entry drift | 0.1 signal ATR |
| Breakeven activation | 1 initial R |
| Failed-reaction timeout | 3 full bars without a close reaching +0.5R |
| Maximum holding period | 15 full bars |
| Simulated trade risk | 0.25% of equity |
| Simulated open-risk cap | 0.5% of equity |
| Concurrent strategy positions | 1 |
| Daily-loss halt | 1% of UTC day-start equity |

The formulas, timing, and rounding definitions in [the specification](03-bot-specification.md) and [execution model](04-execution-and-risk.md) take precedence over abbreviated labels in this table.

## Build sequence

### Phase 1 — source and specification

- [x] Review the full clip's sequential visuals and captions.
- [x] Record timestamp evidence and source limitations.
- [x] Separate observed rules from proposed thresholds.
- [x] Specify entry, exit, invalidation, and test cases.

### Phase 2 — pure strategy module

- [ ] Inspect existing strategy interfaces and shared data types.
- [ ] Introduce a separate strategy identifier without changing other strategies.
- [ ] Implement ATR, confirmed pivots, structure, and base/displacement detection.
- [ ] Implement frozen zone/reference lifecycle and first-touch confirmation.
- [ ] Return structured signals/no-trade reasons with provenance.
- [ ] Test boundary and future-data cases from the acceptance matrix.

### Phase 3 — research runner

- [ ] Supply versioned historical candles, instrument metadata, and cost assumptions.
- [ ] Implement conservative fills and explicit ambiguous-bar reporting.
- [ ] Test the baseline before tuning variants.
- [ ] Save all configuration, data-window, and result identifiers.
- [ ] Publish results including rejected signals and negative outcomes.

### Phase 4 — paper execution

- [ ] Integrate sizing, intents, order reconciliation, and protective orders.
- [ ] Implement breakeven acknowledgment, early failure, and holding timeout.
- [ ] Test partial fills, timeouts, restart recovery, and protection failures.
- [ ] Display zone, reference low, signal evidence, and rejection reasons.
- [ ] Compare paper behavior with replay on the same data.

### Phase 5 — release decision

- [ ] Verify current venue documentation and actual adapter behavior.
- [ ] Define measurable performance and operational acceptance thresholds.
- [ ] Review untouched-period results and execution failure evidence.
- [ ] Keep live trading disabled until a separate deployment decision.

## Research variants to keep separate

Useful comparisons include volume confirmation on the sweep candle versus rejection candle, one-bar versus multi-bar reclaim, wick-range versus body-range demand zones, different pivot lengths, and different breakeven triggers. Change one family at a time and preserve an untouched evaluation period.

A mirrored supply-zone short strategy, fixed reward-multiple target, EMA filter, wick-based stop, or higher-timeframe trend filter would change this baseline. None should be described as a rule recovered from this clip.

## Definition of completion

The documentation deliverable is complete when the video account, assumptions, formulas, execution behavior, and acceptance tests are available and linked. A working bot is a later deliverable requiring implementation and validation. These documents make that work concrete without claiming the 68-second clip contains a complete automated system.
