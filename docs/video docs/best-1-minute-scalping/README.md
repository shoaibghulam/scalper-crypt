# The BEST 1 Minute Scalping Strategy — bot documentation

Source: [The BEST 1 Minute Scalping Strategy.mp4](../The%20BEST%201%20Minute%20Scalping%20Strategy.mp4)

This documentation describes the supplied 68-second video and proposes a reproducible bot specification for its discretionary setup. The strategy is an **uptrend demand-zone retest with a nearby liquidity low, rejection, and volume confirmation**. The presenter uses a **one-ATR stop**, targets the **most recent high**, and moves the stop to entry after favourable movement.

See also the separately implemented [Funded Price Action video strategy](../found%20action/README.md)
in Backtest & Research (5m entries, 1h context, candle stops and 2R targets).

## Read in this order

For the 50 new backtest-only candidates, source references, and comparison workflow,
see the [scalping research library](../../research/scalping-library-50/README.md).

For the implemented five-strategy research suite, see the
[testnet guide and verification results](../../research/crypto-scalping-1-to-5-minute/05-five-strategies-testnet.md).
The video specification below remains separate from those research candidates.

1. [Video review and timestamp evidence](01-video-review.md)
2. [Strategy rules and unresolved details](02-strategy-rules.md)
3. [Deterministic bot specification](03-bot-specification.md)
4. [Execution and risk model](04-execution-and-risk.md)
5. [Architecture and data contracts](05-architecture.md)
6. [Backtesting and acceptance tests](06-testing.md)
7. [Implementation checklist and configuration](07-implementation-plan.md)

## Evidence and scope

The complete clip was reviewed through sequential chart frames and burned-in captions sampled twice per second. This is a visual/caption review, not an independently verified audio transcript. The source is 360 × 640 pixels; exact chart prices, symbol, exchange, and small position-tool labels are not reliably recoverable. Approximate timestamps refer to video playback time, not market time.

The [evidence folder](evidence/) contains three overview sheets and nine detailed sheets. Read each sheet left to right, then top to bottom. Detailed sheets cover eight seconds each; the last sheet contains the end of the clip and blank padding.

Throughout these files:

- **Observed** means visible in the video or its captions.
- **Interpretation** means a reasonable reading of the drawings or explanation.
- **Proposed** means an engineering decision added to make the bot testable. It is not a setting supplied by the presenter.

The proposed baseline is long-only, uses standard one-minute OHLCV, and begins in simulation. A mirrored short strategy would be a separate extension. No bot code, exchange settings, credentials, or existing strategy documents were changed. No profitability or backtest result is claimed.

## Important distinctions

This source is different from the longer video documented in `the-minute-black-strategy.md`. Do not import its Heikin Ashi, 100 EMA, doji, or wick-based stop rules into this strategy.

The presenter explains demand using presumed unfilled orders and liquidity below a low. Candle charts do not establish that those orders actually exist; the bot specification therefore detects price/volume patterns, not hidden orders.

The video calls a move to breakeven “zero risk.” A stop at entry does not remove fees, slippage, gaps, or execution failure. Here, “breakeven” describes the stop price only.
