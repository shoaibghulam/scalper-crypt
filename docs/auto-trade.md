# Auto Trade (testnet)

Enter **margin per position** and leverage, then click **Start Auto Trade**.
The engine confirms the request on this page. A queued request is not a filled order.

While running:

- **Sync strategies** with unchanged settings adds missing enabled strategy bots without duplicates.
- **Update all strategies** applies a changed amount or leverage to future entries across the fleet.
- Existing bot identities, counters, history, open positions and pending settlements are preserved.
- Individually stopped or errored bots retain their state during updates. A stopped fleet can be restarted explicitly.
- Strategies disabled in Strategy Monitor remain excluded.

The current catalog contains 99 strategies. Each scan checks all active Binance USDT
perpetuals; there is no top-20 or top-60 coin cap. New coins are discovered automatically,
subject to liquidity, spread and exchange eligibility checks. Manual bot selections are unchanged.
Entry priority rotates between strategies. Each strategy keeps its own candle confirmation
schedule. Two-minute candles are not supported; candle timeframe is not holding duration.

The amount is **per position**, not a total budget. All account-wide position, leverage,
margin, ownership, daily-loss and trade-risk limits continue to apply. Updates do not alter
existing position sizes, leverage or protective orders. Open or unsettled trades block only
that bot's next entry until settlement. No entry or profit is guaranteed.

**Stop Auto Trade entries** stops new Auto Trade entries; existing positions remain managed.
Settings persist across engine restarts. Trading alerts must be resolved before starting or
updating a fleet. Stop remains available while new entries are blocked.

## Market-data rate limits

Entry revalidation requests a single symbol's fresh ticker and book instead of
reloading whole-market quotes for every candidate. Public clients share weighted
request pacing within each process. HTTP 429/418 starts a shared cooldown that
honors Retry-After and an explicit IP-ban deadline. Private protection management
is not queued behind the public-data limiter. Separate processes or other
applications on the same IP still contribute to Binance's total request weight.

Continuous bots defer known pre-entry rate-limit failures instead of becoming
permanently errored. Uncertain entries remain blocked for reconciliation.
Legacy HTTP failures in active Auto Trade bots are rechecked once when no position,
entry intent or settlement is pending; disabled/stopped bots are not resumed.
