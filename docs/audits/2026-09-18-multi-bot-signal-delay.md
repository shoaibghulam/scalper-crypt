# Multi-bot signal delays

The previous per-bot candle prefetch did not eliminate the main remaining cost:
every waiting symbol still requested signed positions and leverage before checking
its strategy. Each bot also repeated whole-market requests. With six active bots,
later checks could start after the 20-second entry window.

## Fix

- Screen all due bots using one shared public exchange-info, ticker, and book snapshot.
- Deduplicate candle requests by symbol, timeframe, and requested history length.
- Run only read-only screening concurrently; order execution remains serialized
  under the account lock, with fresh strategy, quote, ownership and risk checks.
- Bound screening to 12 seconds. This timeout never wraps order submission.
- Check the setup before signed account/leverage requests, including immediate entries.
- Check signal expiry after confirming that a setup exists, so an absent setup is
  not misleadingly described as an expired signal.
- Show normal rejections in the individual symbol notes, without red failure banners.
- Show spread and volume values and their thresholds, and remove duplicate symbol prefixes.
- Preserve signal-age limits, stop protection, risk limits and unknown-entry blocking.

## Observed testnet result

A read-only check of the six active bots completed in 2.31 seconds. No entries
were confirmed. Observed reasons included spreads over the configured 5 bps cap,
missing breakout confirmation, and symbols absent from the current tradeable
USDT-perpetual exchange response. Market conditions can change each cycle.

Tests cover shared reads across eight bots, expired versus absent setups,
confirmed-candidate selection, and refusal to fetch signed account data or place
orders when no setup exists. Runtime state and financial settings are preserved.

Validation: 978 backend tests passed with 83.65% coverage; 86 frontend tests,
production build, lint, Ruff and mypy passed. The existing Heikin Ashi timing
test now takes the median of five interleaved samples rather than relying on
one measurement; its original scaling bound is unchanged.

The updated local testnet service reported a live engine, trading enabled,
no reconciliation alerts, and distinct waiting reasons for each watched symbol.
