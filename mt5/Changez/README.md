# Changez — M1 and M5 ICT confluence

Two fixed-timeframe MT5 Expert Advisors, based on the supplied ICT_Confluence_EA:

| EA | Signal timeframe | Default magic |
|---|---|---|
| Changez_1M | 1 minute | 26092101 |
| Changez_5M | 5 minutes | 26092105 |

The chart timeframe does not override the EA's fixed timeframe. Both wrappers
use ChangezCore.mqh to keep the signal implementation identical.

## Preserved rules

- Signals evaluated once per completed bar, entered at the next available tick.
- A swing pivot needs two completed bars to its right and two to its left.
- Long: close crosses above the latest swing high, remains below the midpoint
  of the closed 100-bar range, and a bullish FVG occurred on shift 1 or 2.
- Short: inverse market-structure break, above the range midpoint, bearish FVG.
- FVG requires displacement of its middle candle. Body average includes that
  candle, as in the supplied code: five bodies, multiplier 1.5 by default.
- Stop at the closed ten-bar local low/high; target defaults to 5 times risk.
- Default New York sessions: 02:00–05:00 and 08:30–11:00.

These strict combined conditions may produce few trades. The labels do not
establish profitability or institutional participation. No parameters have been
optimized and no historical performance claim is made.

## Changes to execution

Demo accounts / Strategy Tester only, and EnableEntries=false by default.
Original balance tiers (30 / 80 / 120 in account currency at below 1000 /
1000–1499 / 1500+) are retained, but actual risk is capped at RiskCapPercent
of equity (default 1%). Costs and adverse execution can exceed this estimate.
Broker-aware OrderCalcProfit replaces generic tick-value sizing. Volume rounds
down; an unaffordable minimum lot is skipped rather than rounded up.

Protective prices use tick size. An invalid broker stop distance causes a skip,
not an automatic widening of the swing stop. Free-margin and spread checks apply.
Server order result codes are checked; unconfirmed entry results block entries
until you inspect the account and reattach the EA. No blind entry retries.

Any position or pending order on the symbol blocks new entries, including manual
positions and the other Changez timeframe. Run each version separately on its
own demo account/test to compare them fairly. Different terminals are not locked
against one another. No opposite-position closing or averaging is performed.

## Session setup — required

BrokerUTCOffsetHours defaults to 99 (unset). With useTimeFilter=true, set it to
the broker candle timestamps' UTC offset, for example 2 only if your broker's
server actually uses UTC+2 for the test dates. Otherwise initialization refuses
to start with an explanatory Experts/Journal message.

New York US daylight-saving transitions are calculated from each historical date.
The broker offset is fixed per run: split tests at broker DST changes and set the
correct offset for each segment. Do not infer historical offset from the current
computer time. Alternatively set useTimeFilter=false for an explicitly unfiltered
experiment, which differs from the original session-restricted strategy.

## Testing

1. MT5 Navigator → Expert Advisors → Refresh → TheScalper → Changez.
2. Ctrl+R: choose Changez_1M or Changez_5M and the exact broker symbol.
3. Select real ticks where available, dates, demo-equivalent deposit and leverage.
4. Set EnableEntries=true and the correct BrokerUTCOffsetHours (or explicitly
   disable the session filter).
5. Run each version on the same dates, costs and capital. Save both reports.
6. Inspect Journal for skipped entries, order errors and minimum-lot constraints.

MaxSpreadPoints defaults to 20 broker points, not 20 pips. Adjust for the symbol.
EA names shown in the Navigator use underscores; order comments use
"Changez 1 Minute" and "Changez 5 Minute".

Both EAs compile with zero errors and zero warnings. Broker backtests and demo
fills have not been executed. Installation does not attach or activate either EA.
No connection to the crypto dashboard or its database is implemented here.
