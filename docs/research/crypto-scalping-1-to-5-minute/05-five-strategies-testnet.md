# Five research strategies: testnet build

Implemented 2026-09-18. All five choices are registered for **Create Scalp** after
restarting the backend. Original video bots and existing selections remain available.
All operate 24/7. This is a baseline testnet build, not proven live profitability.

| Dashboard choice | Strategy ID | Timeframe | Exit |
| --- | --- | --- | --- |
| EMA Trend Pullback (Research) | `ema-trend-pullback` | 5m trend, 1m entry | 1.5R with pivot room |
| Rolling Breakout Retest (Research) | `rolling-breakout-retest` | 5m | 2R |
| Range VWAP Reversion (Research) | `range-vwap-reversion` | 5m, prior 60m reference | Frozen VWAP |
| Liquidity Sweep Reclaim (Research) | `liquidity-sweep-reclaim` | 5m trend, 1m entry | Existing opposing pivot |
| Order-Flow Pullback (Research) | `order-flow-pullback` | EMA plus depth/trade events | EMA bracket |

The proposed 3m/5m alternatives use the **5m baseline** here. There is no 2m,
3m or 4m selector. Order flow confirms a candle strategy; it is not an independent
market maker or HFT system. Its execution is explicitly testnet-only.

## Rules

### Rolling breakout/retest

Freeze the preceding twenty 5m bars at a close outside their range, excluding
the breakout bar. Enter on the first trend-direction retest close beyond the
broken edge within three subsequent bars. Touch tolerance is 0.1 ATR14. A close
through the far range edge invalidates the setup. An earlier confirmed retest
consumes it. Stop beyond the retest extreme or edge, whichever is farther, plus
0.1 ATR. Target 2R after tick rounding. No hourly engulfing or session restriction.

### Range VWAP reversion

Before excursion/trigger, require 5m ADX14 below 20 and efficiency ratio at most
0.25. The twelve bars before those two bars provide a frozen 60-minute reference.
Use typical price `(high+low+close)/3`, base-volume weighting and volume-weighted
dispersion. These are VWAP bands, not SMA Bollinger bands or trade-derived VWAP.

Half-window VWAP drift must be at most 0.2 ATR14. Require an excursion close
outside two sigma, followed by a reversal-direction close back inside, short of
the mean. Target the frozen mean. Stop beyond excursion/trigger extreme plus
0.1 ATR and the shared floor. Reject less than 1.5R at the actual rounded quote.
Never extend the mean target to rescue a trade. A time-based exit remains a
separate experiment; this baseline uses a fixed bracket.

### Liquidity sweep/reclaim

Require 5m EMA20 direction and its three-bar slope to agree with real 1m price.
Use a two-left/two-right pivot confirmed before the trigger and not previously
penetrated. Trigger must sweep/reclaim it, with rejection wick at least half
the range and volume at least 1.3 times the preceding twenty bars' mean.
Target the most recent eligible opposing pivot, untouched through the trigger.
Stop beyond the sweep extreme plus 0.1 ATR14 and the shared floor. Reject absent
or consumed references and less than 1.5R. No FVG or institutional-order inference.

### Order-flow confirmation

The collector uses routed depth and aggregate-trade WebSockets plus a REST
snapshot. It validates the initial sequence bridge and subsequent previous-update
IDs. Gaps, crossed books, invalid values and lost snapshot coverage invalidate
the sample. The next request starts new connections and a fresh snapshot.
Startup backlog preserves sequence continuity but cannot become signal evidence.
See [Binance's reconstruction rules](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly).

Confirmation requires:

- Five top-20 notional-depth imbalance observations, each at least +0.20 long
  or at most -0.20 short, spanning at least 400ms with no gap above 500ms.
- At least three sequenced aggregate trades and $1,000 notional in a common
  window of at most 1.5 seconds. Aggressive notional imbalance must agree by 0.20.
- Depth/trade receipt age at most 750ms, receipt latency at most 1,000ms, spread
  at most five basis points, and a valid underlying EMA setup.
- Fresh evidence after margin/leverage preflight. A move over five basis points
  from planned entry, invalid frozen bracket or expired signal blocks submission.

Queues, message sizes, book capacity, connection times and sample duration are
bounded. Scanner sampling covers the first three symbols in its volume-ranked
liquidity shortlist; others receive an explicit rejection. A bot planner can
sample its selected symbol directly. There is no stale/REST-only fallback.

Execution uses the existing market-order/bracket engine and minute signal
deduplication. Historical event archives, queue simulation, maker orders and
event-profit replay are not implemented. Candle-only replay explicitly refuses
this strategy rather than fabricating flow history.

## Shared protection

Require consecutive aligned closed candles and causal 5m context. Signal expiry
is 20 seconds after the 1m close. The two 5m-trigger strategies only signal after
a newly closed 5m bar. Entry deviation is at most half the signal ATR; a lost
breakout/reclaim rejects. Shared fees, spread, liquidity, exposure and exchange
reconciliation gates remain active, including the 0.45% minimum stop distance.

Stops round away from entry. Risk-multiple targets account for the rounded stop;
mean/structural targets round toward entry and must still provide 1.5R. EMA targets
must stay inside confirmed price barriers. The engine validates actual fills.

## Supervised testnet run

1. Set `BINANCE_ENV=testnet` and futures testnet credentials in your local `.env`.
   Leave `TRADING_MODE=live` unset. This work did not read/change credentials or bot state.
2. Stop any older engine before restarting. From `backend`, run:

   ```powershell
   .venv/Scripts/python.exe -m scalper
   ```

3. Open `http://127.0.0.1:8000`. Verify **testnet** and existing bot states before
   enabling bots. Select a research choice in Create Scalp, initially one strategy
   and one symbol with a small test allocation.
4. Verify actual entry, exchange-held stop/target, closure, restart reconciliation,
   stop/halt, PANIC and resume on the test account.
5. Record strategy ID, environment, order IDs, timestamps, fills, fees and rejection
   reasons. Compare candidates separately with fixed parameters.

No signal is normal. Sparse testnet activity, low notional, stale data or the
scanner's top-three limit may prevent event readiness. Do not loosen gates just
to force orders. Public connectivity does not prove signed-order acceptance;
testnet fills do not establish live expectancy.

## Replay

The four candle research candidates are supported by the existing CLI:

```powershell
.venv/Scripts/python.exe -m scalper.backtest candles.csv --strategy rolling-breakout-retest --output results.json
```

CSV header: `open_time,open,high,low,close,volume`; consecutive 1m rows, Unix-ms
timestamps. Replay uses next-bar entry, fees, slippage and conservative same-bar
stop resolution. It omits funding, historical books and full exchange-filter
simulation. See the [validation protocol](02-validation-and-bot-mapping.md).

## Verification

- **964 backend tests passed**, combined statement/branch coverage **83.10%**.
  Existing 80% gate retained; two dependency warnings remain.
- Ruff and Mypy passed (60 source files).
- **81 frontend tests passed**; production build and lint passed.
- Tests cover all five through the planner, both directions, causal candle replay,
  consumed setups, frozen targets, tick rounding, invalid data, sequenced streams,
  scanner wiring, preflight rejection and order-flow live-account refusal.
- Public Binance testnet BTCUSDT event collection succeeded. The sample failed
  the trade-notional threshold and correctly withheld confirmation. **Zero orders sent.**

Artifacts: `var/research-suite-verification.txt`,
`var/research-frontend-verification.txt`, `var/research-testnet-public-probe.json`.
Signed order lifecycle acceptance remains for the supervised testnet run.
