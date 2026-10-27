# Auto Trade: full universe and strategy testing

Auto Trade is testnet-only and starts only when the operator clicks Start.
The fleet now includes all 99 registered strategies, including the 90 research
candidates. Existing running fleets gain newly added strategies on their next
explicit stop/start; updating code does not silently create more bots.

## Coins and execution

Each scan obtains Binance's active USDT perpetual contracts, all-market tickers
and bid/ask quotes. There is no top-20 or top-60 ranking cap. All symbols are
checked against liquidity/spread requirements; every passing symbol receives
candle analysis. Unsupported contracts, unavailable quotes and symbols failing
the economic gates are not traded. This does not mean opening all coins at once.
Account position, leverage, margin and risk limits still apply.

Candle requests are shared at the largest required history size per interval,
with bounded download concurrency. Strategies use their own history windows.
Screening has a 45-second deadline; an incomplete cycle produces an explicit
retry message and does not submit orders from incomplete results. Signals are
revalidated before orders. Candle strategy entry priority rotates each minute;
slow order-flow confirmation remains last. The dashboard reports the universe
size, liquidity-eligible count and whether the latest screening completed.

## Stop/start and settlement

Stopping disables entries and preserves exchange protection. Starting again
reuses existing bot IDs, counters and persisted history, and creates only missing
strategy bots. A bot with an open position, pending entry or unsettled close is
excluded from new-entry screening until it settles. Other strategy bots can run.
New amount/leverage settings apply to subsequent entries; restart does not change
protection or leverage on an existing exchange position. Account-wide safe-mode
alerts still prevent starting; this change does not bypass reconciliation.

## Comparing strategies later

Closed-trade history remains persisted with strategy IDs and fees/net results;
restart no longer discards settled fleet bot records or their cumulative counters.
Stopping a strategy does not erase its trades. Use net results, drawdown, sample
size and fees across different periods when deciding what to retain. Shared
capital and position caps mean these bots are not independent controlled trials.
Strategy Monitor now provides manual disabling and an optional automatic loss rule.
See [Strategy Monitor](strategy-monitor.md).

## Entry-stop responsiveness

Stop Auto Trade, Stop Single Coin and Stop entries are local entry-disable commands.
They bypass the engine account lock held by scanning, persist the stopped state,
and leave existing exchange orders/positions managed. Entry submission checks the
bot's stopped state again after asynchronous preflight; an order already sent is
not cancelled mid-operation. The dashboard initially shows four bots and four
symbols per bot with expansion controls.
