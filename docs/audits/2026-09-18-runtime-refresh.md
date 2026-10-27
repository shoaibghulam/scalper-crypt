# Runtime refresh: missing strategies and orphan protection

The API and engine were still running the older build started at 20:06.
The live API returned four strategies, while current source registered nine.
Current `Engine` also already contained `_protect_orphan`, including a regression
test for attaching a missing stop without replacing existing protection.

Changes performed:

- Rebuilt the frontend and changed strategy discovery from an infinite cache
  to a thirty-second refresh, so an open updated dashboard detects backend upgrades.
- Stopped the identified old supervisor and its API/engine processes, then
  restarted the application from `backend` with the updated code.
- Backed up `var/engine-state.json` to
  `var/runtime-upgrade-20260918/engine-state.before-restart.json`.
- Preserved legacy records in `var/engine-state.testnet.json`, with
  `control.halted=true` to prevent new entries during account review.

Verification:

- Running `/api/strategies` returns all nine choices, including the five research IDs.
- Running state reports testnet, connected engine and halted trading; the old
  missing-method action error is absent.
- Current exchange reconciliation no longer reports ENAUSDT as an orphan.
- An older BTCUSDT record remains pending verified settlement and is excluded
  from performance statistics. It was not discarded to hide the warning.
- 43 targeted backend guard/integration tests and 81 frontend tests passed.
  Frontend build and lint passed.

No adoption/protection command or order was submitted during this repair.
Refresh the browser once to load the rebuilt bundle. Review the settlement
warning and account state before resuming trading. Registered strategy types
are choices for creating bots; this repair did not create funded bot instances.

## Follow-up: settlement recovered and testnet resumed

The legacy BTCUSDT record had `opened_at=0` and no entry order ID, preventing
normal settlement. Read-only exchange history contained a unique BUY matching
its exact entry price and quantity and a subsequent full SELL of the same
quantity. Both commissions were denominated in USDT; no active BTCUSDT algo
orders remained.

With the engine stopped and state backed up, only the missing entry order ID
and timestamp were restored. Normal engine settlement then consumed the exchange
fills and cleared the pending closure. No guessed P&L or settlement bypass was used.

The normal Resume command completed fresh account reconciliation successfully:
`mode=testnet`, `halted=false`, `trading_enabled=true`, `alerts=[]`.
Evidence and the pre-repair backup are in `var/runtime-upgrade-20260918/`:
`btc-settlement-fills.json`, `btc-settlement-repair.json`, and
`engine-state.before-settlement-repair.json`.
