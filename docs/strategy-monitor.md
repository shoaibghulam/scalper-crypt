# Strategy Monitor

The main navigation includes Strategy Monitor for all 99 registered strategies.
It uses the complete persisted closed-trade history, not the latest 100 dashboard records.
Wins and losses use verified net results after fees; zero results are break-even.
Unverified records remain stored and are shown separately, excluded from scoring.
The table includes trade counts, win/loss percentages, net result, fees, profit
factor, average net result per trade, and losses in the latest 20 verified trades.

## Experiment controls

A strategy is flagged after at least 15 losses in its latest 20 verified closed
trades. Automatic disabling is optional and off by default. Enable it in the tab
to stop new entries for flagged strategies at the next signal evaluation.
Manual Disable takes effect when the engine accepts the command. Open-position
management and protective orders remain intact. Disabling persists across restarts
and excludes the strategy from subsequent Auto Trade starts and manual entries.
Enabling permits future starts; it does not resume stopped bots automatically.
Turn off the automatic rule before re-enabling a strategy that still meets it.
No strategy implementation or trade history is deleted.

## Persistence and interpretation

The existing environment-specific JSON state remains the recovery authority.
Every history/settings change also transactionally mirrors the complete closed
trade history and monitor settings into SQLite beside that state file:
`var/engine-state.testnet.sqlite` for the default testnet state path.
Tables: `closed_trades` (including full JSON payloads) and `experiment_settings`.
This is a snapshot mirror, not a separate market-data archive. Repeated saves do
not duplicate trades. Persistence failures prevent new entries.

All enabled strategies participate in Auto Trade, subject to account limits.
The full Binance active USDT perpetual universe is screened; liquidity and spread
filters still apply. Win rate alone does not establish profitability. Compare net
results and sample sizes across periods; shared account capacity limits make the
experiments dependent. Testnet outcomes do not establish live profitability.

Account refreshes use elapsed time and run before entry scans, including the first
loop after startup. Long scans no longer delay settlement by accumulating only
heartbeat counts. Closed positions awaiting verified fills remain excluded.

During fleet entry evaluation, account refreshes also run between strategy bots
when due. A post-fill abort warning clears only after the symbol is absent from
managed/pending positions and a verified closed trade exists for that bot. Other
protected positions no longer prevent continuous-bot recovery after settlement.
Post-fill economic checks remain enforced; unfavorable fills may still be closed.

Successful entry summaries report opened symbols and a compact skipped-candidate
count. Routine signal/spread rejections remain in per-symbol notes; genuine
execution/protection failures still produce error summaries.
