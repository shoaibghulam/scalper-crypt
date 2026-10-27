# Watched symbols not entering — testnet investigation

## Confirmed causes

- The Range VWAP bot watched four symbols; that is one bot, not four immediate positions.
- At 22:39:29 local time, DASHUSDT's public ticker request raised `httpx.ConnectTimeout`. The generic exception handler permanently marked the continuous bot as `error`. Later symbols overwrote the error with expired-signal messages, concealing why future cycles stopped.
- Order-flow acquisition ran before candle bots and before checking its own candle setup. Slow event collection consumed the short entry window for later bots.
- Five-minute strategies ran every minute, and each symbol loaded candle history separately.
- Both research bots had saved leverage of 20x while the configured account cap was 10x. The create form allowed this invalid configuration.
- A public-data assessment found DASHUSDT, AVAXUSDT and RAYSOLUSDT outside the low-trend range regime, and ONEUSDT without a flat VWAP reference. These observations are time-specific, not permanent symbol exclusions.

## Changes

- Temporary transport failures before an entry intent retry on a subsequent signal cycle. Uncertain entries, managed exposure, and persistence failures retain conservative blocking behavior.
- Fatal errors stop that bot's attempt immediately, preserving their original cause.
- Candle bots run before the order-flow bot. The latter checks its EMA candle setup before acquiring event data.
- Candle histories are prefetched concurrently and cached only within the current minute. Executable quotes are not cached.
- Five-minute strategies run at five-minute boundaries. Scheduled checks start two seconds after the minute boundary to allow closed bars to become available.
- Overview lists every watched symbol, its individual result, and the last check time.
- The engine publishes its leverage cap, the form disables choices above it, creation rejects invalid leverage, and existing invalid configurations display an explicit blocking reason.

## Validation and runtime

- Backend: 975 tests passed; coverage 83.76%. One initial run hit the existing timing-sensitive Heikin Ashi scaling test; a full rerun passed.
- Frontend: 86 tests passed; production build and lint passed.
- Ruff and mypy passed.
- Regression tests cover four-symbol transient failures, preservation of uncertain entry intent, order-flow scheduling, five-minute cadence, cache expiry, and leverage restrictions.
- Local testnet services were restarted after confirming no open positions, pending entries, or pending settlements. Saved state was backed up under `var/runtime-upgrade-20260918`.
- The Range bot was dismissed during the investigation; it was not recreated automatically. Existing leverage was not changed without the requested approval.

These checks verify application behavior. They do not establish strategy profitability or guarantee a trade on every candle.
