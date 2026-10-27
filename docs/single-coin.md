# Single Coin — testnet

Open the Single Coin tab, enter a capital budget (default 10 USDT), leverage,
and entry-candle timeframe, then select **Find one coin and trade**.

- **1m:** EMA Trend Pullback with its existing 5m trend confirmation.
- **5m:** Rolling Breakout and Retest, evaluated at five-minute boundaries.

The bot searches up to 20 eligible liquid markets and waits for a confirmed
setup. It tries qualifying candidates in volume-ranked order. It opens at most
one protected position per search, then stops searching. Start another search
after the previous position and its settlement finish. Stop search cancels future
entries; it does not close a position. Settings survive restarts, and recovery of
an uncertain filled entry also consumes the one-entry search.

The budget is a sizing cap, not a deposit, spending target, or guaranteed maximum
loss. Sizing uses the smaller of budget and account equity, the configured
per-trade risk fraction, stop distance, round-trip fee allowance, available
balance and aggregate margin allowance. Quantity rounds down; it is never raised
to an exchange minimum that exceeds those limits. With default controls, a 10
USDT budget allows at most 0.10 USDT planned stop loss including the fee allowance
and at most 5 USDT initial margin. Actual losses can exceed the plan through
slippage, gaps or execution failures.

Some symbols cannot be traded within a small budget. Their minimum-order or
risk rejection appears in the bot's status, and the search can try another setup.
Nothing promises a profitable coin, an immediate entry, or a specific holding time.
Other manually created and Auto Trade bots are separate; this feature does not
stop them. Shared account safety limits still apply. Use the feature on testnet
to evaluate its behavior before considering any separate live deployment.
