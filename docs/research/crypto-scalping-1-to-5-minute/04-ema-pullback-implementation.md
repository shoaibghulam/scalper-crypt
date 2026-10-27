# EMA Trend Pullback — research implementation

Built on 2026-09-18. Strategy ID: `ema-trend-pullback`. Dashboard name:
**EMA Trend Pullback (Research)**. Select it in the existing Create Scalp strategy
selector after restarting the backend. Existing strategy selections are preserved.

This is the first candidate from the [research shortlist](README.md), not a
verified most-profitable strategy. It is available 24/7, including weekends.

## Exact implemented rules

- Use real, closed candles. Require 60 consecutive aligned 5m bars and 60 1m bars.
  Reject invalid OHLC, non-finite values, invalid quotes, missing bars and a trend
  candle from the future or at least five minutes behind the trigger.
- Indicators use fixed trailing 60-bar windows so replay and live collection
  lengths do not change the calculation. EMA uses the existing SMA-seeded formula.
- Long: 5m EMA20 > EMA50, EMA50 above its value three bars earlier, and the
  latest 1m close above EMA20. Short: reverse each comparison.
- Exactly 2–5 consecutive opposite-body 1m candles precede the trigger.
  Trigger must have positive volume, a trend-direction body, and close above
  the previous high (long) or below the previous low (short).
- Use the actual ask for long entry and bid for short. Refuse an entry more
  than 0.5 of 1m ATR14 away from trigger close or one that lost the breakout.
- Stop beyond the extreme of the pullback plus trigger, buffered by 0.1 ATR14.
  Enforce the shared 0.45% minimum stop distance. Target is 1.5 times stop distance.
- Require a confirmed opposing pivot with enough room for the target. Pivot
  search uses the preceding 59 minutes, excluding the trigger, and two strictly
  lower highs / higher lows on each side. Both confirmation bars must precede
  the trigger. The closest pivot beyond entry must permit at least 1.5R.
  If none exists, skip the trade. These are historical price barriers, not an
  order-book liquidity estimate or a guarantee that the level will hold.
- Engine enforces a 20-second signal age, shared fees/spread/liquidity gates,
  exchange rounding, sizing, exposure limits and position reconciliation.

All numbers above are explicit research defaults. They have not been optimized
or shown to be profitable. There is no hourly session restriction, additional
RSI/ADX filter, volume multiplier, trailing stop or break-even rule in this candidate.

## Replay and validation

From `backend`, using a compatible closed 1m CSV:

```powershell
.venv/Scripts/python.exe -m scalper.backtest path/to/candles.csv --strategy ema-trend-pullback --output path/to/results.json
```

CSV header: `open_time,open,high,low,close,volume`. Supply `open_time` in Unix
milliseconds, aligned to the minute, with consecutive rows and real OHLC prices. The replay applies next-bar entry, configured adverse slippage,
fees, and conservative stop-first resolution when a bar touches both exits.
It does not simulate funding, historical order books, exchange outages or every
live scanner gate. Synthetic unit tests check behavior, not trading returns.

Added tests cover long and short entries, bracket direction and 1.5R, chase and
lost-breakout rejection, insufficient/malformed/gapped/stale data, absent trend,
incorrect pullback lengths, missing volume and insufficient pivot room. A real
strategy replay fixture verifies next-bar entry and positive transaction costs.

Before live capital: run the [validation protocol](02-validation-and-bot-mapping.md)
with untouched out-of-sample periods, actual exchange fees, stressed slippage,
multiple liquid symbols and market regimes, followed by testnet operational
acceptance. No live orders or deployment were performed while building this.

## Files

- `backend/src/scalper/strategies/ema_pullback.py`: signal and bracket logic.
- `backend/src/scalper/strategies/__init__.py`: registry discovery.
- `backend/src/scalper/backtest.py`: CLI strategy selection.
- `backend/tests/unit/test_ema_pullback.py`: behavior and causal replay tests.

Frontend discovery already uses the strategy API, so no UI code change is needed.
