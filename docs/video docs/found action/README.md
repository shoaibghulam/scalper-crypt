# Funded Price Action — video review and implementation

Source: `The Scalping Strategy Used By Funded Traders (PRICE ACTION).mp4`,
11m58s, reviewed from its audio transcript and chart frames on 2026-09-19.
The examples show AUDUSD and gold. The presenter also claims applicability to
crypto; the examples are not a measured crypto performance record.

## Where to find it

Open **Backtest & Research → Strategy → funded-price-action · Funded Price
Action (Video Research)**. It is also included in **Compare library including
existing strategies**. Select **Automatic coin history**, or upload closed **1-minute** OHLCV candles;
the system builds closed 5-minute candles and complete UTC hours.

Minimum: **1,960 rows**, including **1,260 warm-up rows**. Maximum: 10,000 rows.
Comparisons containing this strategy use the same longer warm-up for all
strategies, preserving identical evaluation periods. Fees, spread, slippage,
quantity step and minimum notional are the research form's assumptions.

This strategy is available for offline evaluation and manual **testnet Create
Scalp** bot creation. Real-money creation/execution is blocked. It is included in testnet Auto Trade. Single Coin retains its original two strategies. Adding it does not create bots or place orders.
Shared execution gates can reject setups accepted by the simplified replay.
The library contains 70 research candidates and 8 candle baselines.

## Rules observed in the video

| Video time | Observed rule |
|---|---|
| 00:22–01:09 | Enter on 5m; inspect recent 1h trend and structure first. No indicators. |
| 01:09–02:37 | Higher/lower highs and lows, breaks of structure, recent support/resistance. |
| 02:37–04:01 | Mark recent 5m high/low; break high and retest for buy, or break low and retest for sell. |
| 04:11–05:00 | Do not enter the initial break. Wait for retest plus long rejection wick **or** engulfing candle. |
| 05:11–05:48 | Short stop above confirming candle; target twice stop distance (2R). |
| 05:58–07:53 | Redraw structure after a trade. At hourly support, 5m break/retest and bullish confirmation; long stop below confirmation, target 2R. |
| 08:16–09:54 | Gold example: possible 1h bullish reversal, then a 5m downside break/retest and short wick-confirmation trade. |
| 10:04–11:29 | Wait through consolidation for confirmation. Stop beyond wick, target 2R. Presenter acknowledges one trade was intraday and retrospectively identifies an earlier entry. |

The narrative starts with hourly trend direction but explicitly permits both
5m breakout directions and shows reversals. The implementation records hourly
higher-high/higher-low or lower-high/lower-low context without imposing a
mandatory alignment filter. It does **not** reproduce discretionary hourly
support drawing or visual judgments such as whether a candle looks “nice.”

## Exact automation choices (not specified numerically by the video)

1. Require 252 closed 5m bars, 60 closed 1m bars and at least 20 complete UTC
   hours. Never use forming candles or incomplete/gapped hourly groups.
2. A swing is a strict high/low above/below its two neighbors on each side.
   Both right-side candles must close **before** the breakout candle. Equal
   highs/lows do not form a swing. Use the latest swing of each type, no older
   than 36 five-minute bars at breakout.
3. A breakout is a close crossing beyond that swing. Freeze its level; do not
   move it to a later swing while waiting. A newly detected breakout
   replaces the pending setup. Each swing can start only one setup.
4. On one of the next 12 candles, require a retest intersecting a band of
   ±0.10% around the frozen level and a close beyond it in the breakout
   direction. A close more than 0.10% through the level against the setup
   invalidates it. Initial breakout and retest must be different candles.
5. That retest candle must have either a directional rejection wick occupying
   at least half its range, closing in the directional half, **or** a body
   engulfing the previous opposite-direction body. Only the first qualifying
   confirmation signals; later candles cannot reuse the same setup.
6. Long stop = confirmation low × 0.9999; short stop = confirmation high ×
   1.0001. The 0.01% buffer is a research approximation, **not exchange tick
   rounding**. Target = signal close ± twice the signal-close-to-stop distance.
   No ATR stop or artificial widening is substituted.
7. Replay enters at the next 1m open after the 5m confirmation, adjusted for
   spread and adverse slippage. Frozen stop/target remain based on the signal
   close, so realized reward/risk can be below 2R after gaps and costs. Invalid
   brackets and undersized orders are rejected by replay.
8. No fixed session hours or holding duration. A 5m entry candle does not mean
   the position must close in 5 minutes. No hindsight entry replacement.

## Evaluation and limitations

The standard replay uses three separate chronological periods with equal
starting capital, plus doubled-spread/slippage stress runs. It assumes 1%
planned risk and caps margin; it cannot establish historical liquidity, funding,
liquidation, queue priority or actual fills. A candle hitting both exits is
resolved stop-first. Positions still open at a period boundary are closed.

Tests verify both directions, confirmation alternatives, causal pivots and
hourly aggregation, setup expiry/invalidation, one signal per setup, candle
stops and 2R targets, next-bar replay, research isolation and shared comparison
periods. Synthetic test profits are plumbing checks, not profitability evidence.
Evaluate multiple coins/dates after costs, retain losing trials, and validate
shortlists on new data before considering real-money use; testnet trading is available for further checks.

Implementation: `backend/src/scalper/strategies/funded_price_action.py`.
Tests: `backend/tests/unit/test_funded_price_action.py`.
