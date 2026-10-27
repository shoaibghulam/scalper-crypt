> Follow-up: [Minute Black upgrade](2026-09-18-minute-black-upgrade.md) supersedes several limitations below, including entry recovery, PANIC, continuous mode, risk governors and 500-trade retention.

# System audit and fixes ? 18 September 2026

## Result and scope

Audited the backend trading path, strategy registry, scanner, exchange boundary, recovery and persistence, API command path, and frontend state/creation/history/protection displays. Implemented the fixes below and added regression coverage. This is a code audit with mocked exchange tests, not a live exchange acceptance test or a profitability certification. It does not establish that every possible defect has been found.

Claude Code was stopped during the changes. No exchange orders were sent, no bot processes were started, and `.env` was not edited. Original source/test copies are in `var/audit-backup-20260918/`; this checkout has no Git repository. The backup is source only, not a trading-state backup.

## Strategy identity

The implemented **The Minute Black** strategy has registry ID `the-minute-black` and lives in `backend/src/scalper/strategies/black.py`. It detects Heikin Ashi pullbacks, EMA100 direction and a high-volume doji. Its documented crypto adaptations include a minimum stop distance and minimum reward/risk.

The folder `docs/video docs/best-1-minute-scalping/` describes a **different** video: demand structure, liquidity sweep, rejection, an ATR stop and the latest high target. Those documents do not describe the current Minute Black implementation. This audit preserves the implemented strategy's identity; it does not silently replace it with the other video's rules.

## Fixed issues

| Area | Previous problem | Change |
| --- | --- | --- |
| Strategy execution | Selected strategy was ignored; entries used a generic long-only ATR plan | Re-evaluate the selected strategy on fresh closed candles, require a ready signal, honor long/short direction and strategy levels |
| Data quality | No entry-time continuity/freshness requirement | Refuse stale, unfinished, missing or discontinuous candles and missing liquidity/book data |
| Execution economics | Generic scan distances and maker-fee assumptions could misrepresent a market entry | Check selected strategy levels and all-taker fees without assuming a BNB discount |
| Actual fill | Estimated entry could substitute for missing actual fill | Read position price/quantity back; preserve planned levels, revalidate against fill and attempt reduce-only emergency close when invalid |
| Unknown entry outcome | A timeout/restart could erase uncertainty | Persist an entry-pending marker before the attempt; uncertain outcomes keep new trading disabled across restart |
| Exchange write guard | PAPER routed to live REST and mutations bypassed live arming | Every signed mutation rejects PAPER and checks live arming before HTTP |
| Commands | Engine trusted API validation; duplicate symbols and concurrent commands were possible | Shared bounded validation, duplicate/overlap checks, serialized account mutations and expired-command refusal |
| Queued commands | Unbounded queue and stale link acceptance | Bounded queue, live-link requirement and command timestamps |
| Position monitoring | Two account snapshots could disagree; malformed exposure was silently dropped | One position snapshot and fail-closed parsing, including refusal of unsupported hedge-mode exposure |
| Reconciliation | Orphans/read errors/resizes could leave trading enabled | Latch safe mode; reverify exchange protection at startup and refresh |
| Protection | Wrong-side or undersized orders could count as protection | Require correct closing side, active status and close-all or sufficient reduce-only quantity |
| Orphan protection | UI action called a missing method | Implement stop-only protection of a fresh, unchanged orphan position; verify it through adoption |
| Profit-lock stops | Stop validation assumed entry was the current price | Validate against mark when available; distinguish a verified target from a display-only derived target |
| Closed P&L | Last sampled mark was reported as realized exit; nearest level was counted as TP/SL | Settle from deduplicated exchange fills and actual commissions; leave uncertain attribution pending and exit reason unknown |
| Cleanup | Closed-position cleanup cancelled every order on the symbol | Cancel only recorded protective client IDs |
| Pending settlement | Unverified closure could disappear with its bot | Persist pending closures, retain the bot, prevent dismissal and symbol reuse, show an alert |
| Historical estimates | Old estimated trades looked verified | Preserve legacy rows with an unverified label, exclude them from verified summaries |
| Daily metrics | Lifetime realized totals were labeled today | Compute dashboard daily totals from verified retained trades using UTC day boundaries |
| Persistence | Refresh changes were not consistently saved; errors could still look durable | Persist refresh transitions and propagate save failure into safe mode/history status |
| Environments | Live and testnet used the same default state filename | Separate default state files by environment; preserve the old file untouched |
| IDs | Display names and sanitization could collide | Use stable bot IDs; hash lossy client-ID normalization |
| API exposure | Local unauthenticated services could bind publicly; browser-origin mutations were unchecked | Require loopback binding; reject untrusted Host/cross-origin commands and browser Origin on engine WS |
| Malformed input | Invalid state/WS payload shapes could crash processing | Validate object/list boundaries and preserve last valid snapshot |
| Exchange errors | -4067 was treated as successful configuration | Refuse the rejected position-mode change |
| Indicators | Percentile rank could exceed 100 | Clamp percentile to valid bounds |
| Frontend creation | Old strategy scan results and waiting candidates could be submitted | Enforce fresh matching scan, ready candidates, 1?5 symbols, valid margin and trading capability |
| Frontend status | Stale health could keep engine green; failed dismissal was silent | Mark status unavailable when API is stale; display dismissal failures and pending states |

Binance client IDs are unique among **open** orders, not a permanent deduplication guarantee. The Account Trade List endpoint limits time windows to seven days. These constraints informed the conservative retry and settlement behavior. See [Binance USD-M trading API documentation](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade).

## Validation

Run from `backend`:

```powershell
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m mypy src
```

Run from `frontend`:

```powershell
npm run build
npm run lint
npx vitest run
```

The final validation counts are recorded below after the last run. Tests use isolated state paths and mocked exchange clients. Existing tests which asserted guessed P&L, nearest-price exit attribution or non-closing protection were updated to assert the corrected behavior; new tests cover the replacement safeguards. Two backend dependency deprecation warnings concern Starlette/httpx/AnyIO.

## Restart and recovery notes

1. Stop the old engine/supervisor before starting this build. The default lock directory is now anchored under the project `var/data` rather than the shell's working directory. Do not run old and new builds against the same account concurrently.
2. New defaults are `var/engine-state.testnet.json`, `var/engine-state.live.json` and `var/engine-state.paper.json`. The old `var/engine-state.json` is preserved and is **not automatically imported**, because it has no trustworthy environment identity. Existing exchange positions will appear as orphans and require reconciliation/adoption. Preserve the old file as an archive. `SCALPER_STATE_FILE` still explicitly overrides the path and must not be shared between accounts/environments.
3. A pending-entry flag is deliberate: inspect the exchange order and trade history before resolving it. There is not yet an automatic unknown-order recovery workflow. Do not clear saved uncertainty merely to enable the Create button.
4. Pending closures are excluded from P&L until fills and owned-order cleanup are verified. Adopted positions with unknown entry time/order cannot be attributed automatically.
5. PAPER is read-only. Its old apparent execution path was unsafe; a simulator has not been implemented.

## Remaining limitations and follow-up work

These are explicit limits of this delivery, not passing acceptance criteria:

- **Live integration unverified:** no authenticated testnet lifecycle, partial-fill chaos test, actual browser end-to-end session or real-money trial was run. Local mocks cannot establish exchange behavior under every failure.
- **Autonomous trading lifecycle incomplete:** creation evaluates and attempts entries immediately. There is no complete continuous signal/re-entry scheduler, account-level daily-loss governor, durable order/event journal or user-data-stream recovery system.
- **Unknown entry recovery is conservative:** a persisted pending-entry marker stops trading but still needs exchange inspection and recovery tooling. Client IDs alone do not solve exactly-once execution.
- **Settlement is intentionally bounded:** no automatic retrieval beyond the endpoint's seven-day window/1,000-row response, no BNB commission conversion, no funding attribution, no automatic attribution of adopted/externally added exposure. These cases stay pending instead of inventing P&L.
- **History retains 500 trades:** dashboard summaries and UTC daily totals cover retained verified history, not an unlimited accounting ledger. A durable full ledger is needed for larger daily volumes and exports.
- **Risk sizing remains margin-based:** quantity follows user margin times leverage; this is not fixed-account-risk sizing. Current liquidation checks use an approximate maintenance margin rate, not full exchange tier calculations. An account-wide margin/exposure reservation and all market-order quantity/max-notional filters need further integration.
- **Existing/manual orders:** owned protective cleanup is narrow, but a complete policy for unrelated pre-existing orders, external position changes and multiple API clients remains necessary. Use a dedicated account; environment-separated files are not account authentication or cross-machine locking.
- **Emergency controls:** the PANIC button remains disabled and explicitly says emergency close is not implemented. Exchange-native controls remain required. Stops cannot guarantee a fill price during gaps.
- **Remote access:** intentionally refused until authentication/authorization is implemented. Do not expose the local service through an unauthenticated proxy.
- **Strategy validation:** no backtest, out-of-sample evaluation, slippage/funding model or proof of profitability was produced. The demand/sweep video strategy remains documentation, not this bot's implementation.

## Final check results

Backend: **775 tests passed**; Ruff passed; strict mypy passed across **54 source files**.

Frontend: **78 tests passed** across six files; production build and lint passed.

No live exchange orders or deployment were performed.
