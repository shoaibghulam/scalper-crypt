> Superseded where conflicting: [video and narration re-review, corrected bot rules](strategy-review-2026-09-18/README.md). Earlier extraction missed important narrated rules; do not treat the historical analysis below as the current specification.

# The Minute Black upgrade ? 18 September 2026

The strategy remains **The Minute Black**, registry ID `the-minute-black`. It has not been replaced with the separate demand/sweep strategy in `best-1-minute-scalping/`.

## Signal rules now enforced

1. Read Heikin Ashi candles on the closed one-minute series and EMA100 on HA close.
2. Require the signal to be on the correct side of EMA100, the real close to agree, and EMA100 to slope in the same direction over the preceding three bars.
3. Require 2?8 consecutive clean opposite-color pullback candles. A longer pullback is rejected; a zero-body candle is not an opposite-color candle.
4. Require an HA doji with a body at most 25% of its range. Its real volume must be positive, at least each preceding two bars, and at least the preceding 20-bar mean.
5. An entry must reach submission within **20 seconds** of the signal candle close. Freshness is checked again after account preflight. Expired scan results are disabled in the dashboard.
6. Refuse an entry that crosses the real doji extreme or chases more than **0.5 ATR(14)** beyond the real signal close in the trade direction.
7. Place the stop beyond the real doji extreme by **0.1 ATR(14)**, subject to the existing 0.45% minimum stop distance. Target remains at least **1.5R**. Invalid/no-signal geometry raises a refusal; it no longer invents a fallback bracket.
8. Record each attempted symbol/candle before submission, preventing the same bot from re-entering the same signal after restart.

Slope, baseline volume, buffers and chase limits are engineering adaptations. They are not claims from the video or evidence that returns improve. They need validation against representative historical data and forward testnet behavior.

## Account controls

These defaults are configurable in `.env`; fractions are decimal fractions, not whole percentages. They are ceilings, not recommended capital allocations.

| Setting | Default | Behavior |
| --- | --- | --- |
| `MAX_POSITIONS` | 5 | Refuse entry if the account already has this many positions |
| `MAX_LEVERAGE` | 10 | Refuse greater effective leverage |
| `MAX_TRADE_RISK` | 0.01 | Planned stop loss plus an undiscounted round-trip fee allowance must fit this share of current equity |
| `MAX_MARGIN_FRACTION` | 0.50 | Existing initial margin plus the new position margin must fit this share of equity |
| `DAILY_LOSS_FRACTION` | 0.03 | Latch refusal after equity falls this fraction below the first entry-preflight equity recorded for that UTC day |

The daily baseline and lock survive restart. A rebound does not clear a loss lock that day; a new UTC day initializes a new baseline. Withdrawals can conservatively trigger it. This is an entry governor, not a guaranteed maximum loss or automatic liquidation of existing positions. Fees and slippage can exceed estimates.

Production preflight accepts **isolated margin only**, reserves 5% of available margin, checks exchange maintenance tiers and rejects symbols with existing regular or conditional orders. Market lot steps and maximum quantities are also checked. API errors or missing critical account data prevent entry.

## Continuous mode

Continuous mode is opt-in on Create Scalp. Existing bots retain one-shot behavior. A running continuous bot evaluates once per minute and attempts new entries only when it holds no positions, has no pending closure, and has no uncertain entry. The selected strategy and all normal account checks run again. A stopped, failed, or halted bot does not re-enter.

`Stop entries` stops that bot's future entries while preserving its open positions and exchange protection. There is no automatic strategy-switching or parameter optimization.

## Recovery and emergency controls

The full entry plan, deterministic order ID, signal timestamp and opening boundary are persisted before order submission. Recovery queries the original order rather than resending the entry. An active or partial entry is cancelled, then reread; proven fills are managed/protected or moved to pending settlement if already flat. A filled order plus a flat position snapshot requires balanced closing-fill evidence before its entry intent is cleared. A crossed stop or invalid recovered bracket triggers a reduce-only exit, whose flatness is checked on the next recovery pass.

An unknown order, ambiguous/manual exposure, transport failure, or legacy marker without a plan remains blocked. Automatic recovery cannot manufacture evidence the exchange no longer retains.

PANIC requires a second click. It halts future entries, stops bots, cancels known pending entries and attempts reduce-only closes of **managed symbols**. Unrelated/orphan positions are not silently closed. The engine rereads exposure; an HTTP 202 only means the command was queued. Check the engine result and the exchange. If disk persistence fails, emergency closes are still attempted and the failure is reported.

`Reconcile and resume` requires a fresh successful account reconciliation and no pending entry/settlement or unprotected managed position. Stopped bots remain stopped. A daily loss lock is not bypassed by resume.

Saved state is tied to a SHA256 fingerprint of the configured API key after credential verification. Changing keys requires a separate or explicitly migrated state file; it is not a safe way to reuse another account's position records. Preserve the environment-separated state files introduced in the earlier audit.

## Accounting

History no longer silently drops records after 500 trades. The UI still returns a bounded recent list. Exchange fills are fetched in seven-day windows, with full windows split recursively to avoid timestamp-boundary omissions; a 64-request budget prevents runaway retrieval. Unsupported retention or a saturated single millisecond stays pending. These boundaries follow the [Binance USD-M Account Trade List documentation](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade#account-trade-list-user_data).

The current JSON ledger is appropriate for this single-account local application; archive it and monitor its growth. It is not a multi-account accounting database. Funding and non-USDT commissions remain unsupported for automatic attribution. Unverifiable records are excluded from verified performance.

## Offline replay

A credential-free replay tool exercises the upgraded pattern on consecutive closed one-minute bars. CSV columns: `open_time,open,high,low,close,volume`, where open_time is aligned Unix milliseconds. Run from `backend`:

```powershell
.venv/Scripts/python.exe -m scalper.backtest candles.csv --output replay.json --slippage-bps 2
```

It constructs only complete five-minute candles, uses closed history, enters on the next bar open, charges fees and adverse slippage on both sides, and resolves ambiguous same-bar stop/target touches as a stop. Gaps through stops use the worse opening price. It reports closed-equity drawdown and per-trade results. Defaults model 1% planned risk, at most 50% margin at 5x leverage, and 0.05% fees per side.

This is **pattern research**, not a full exchange simulator: no historical book/liquidity scanner, funding, queueing, exchange rounding or partial-fill model is simulated. It cannot establish production profitability. No historical market dataset or performance claim accompanies this change.

## Release checks and remaining launch gates

Use the Windows/Python 3.13 dependency snapshot in `backend/requirements-windows.lock` and `npm ci` with the frontend lockfile. A Windows CI workflow runs backend tests/coverage, Ruff, mypy, frontend build, lint and component tests. CI has been added locally; no remote workflow or deployment was run.

Local verification results are recorded in `docs/audits/2026-09-18-minute-black-upgrade.md`.

Before real-money use, a supervised **testnet** session must verify actual fill/partial-fill behavior, conditional-order readback, timeout recovery, restart with an open position, PANIC, and rejected resume. No live orders were sent in this upgrade. PAPER execution remains disabled; the offline replay tool is not a live paper broker. Remote multi-user authentication and cross-machine coordination are not implemented; deployment remains loopback-only and single-account. Strategy profitability is unverified.

Back up current state before deploying or rolling back. Stop the old engine first, keep exactly one process per account, and never roll back by restoring stale live-position state. Preserve any pending intents until exchange reconciliation proves their outcome.
