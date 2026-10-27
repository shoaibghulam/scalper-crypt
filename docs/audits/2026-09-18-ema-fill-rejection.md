# EMA fill rejection and recovery

The GUSDT short filled at 0.0074520. Its frozen stop and target then provided
only 1.48R, below the configured 1.5R minimum. Post-fill validation correctly
submitted a reduce-only emergency exit, but the continuous bot stayed in ERROR
after the exchange position was flat and settlement completed. Verified history
records -0.22082976 USDT including 0.10005876 USDT fees; it must not be erased.

EMA Trend Pullback now submits an immediate-or-cancel LIMIT entry at the validated
entry price. A buy cannot fill above that limit, and a sell cannot fill below it.
An unfilled terminal response is ordinary waiting, without a position record or
emergency exit. Partial fills use the actual position quantity for protection.
Missing, malformed or nonterminal responses retain the unknown-entry safety path.
Other strategies retain their existing entry types.

The client requests RESULT for IOC LIMIT orders. This follows the
[Binance USD-M order API documentation](https://binance.github.io/binance-connector-js/classes/_binance_derivatives-trading-usds-futures.DerivativesTradingUsdsFuturesRestAPI.TradeApi.html).

Post-fill validation failures still exit rather than widening stops or lowering
the minimum reward/risk. A persisted recovery flag permits a continuous bot to
resume only after verified flatness, fill settlement, order cleanup and clean
reconciliation. Unknown order outcomes are not covered by this automatic retry.
Explicitly stopping a bot cancels its recovery flag. Attempted signals remain
recorded, preventing same-candle re-entry.

The nested symbol list no longer inherits the outer bot-card background styles.
The ratio message now describes a configured threshold rather than making an
unsupported claim about achievable profitability.
