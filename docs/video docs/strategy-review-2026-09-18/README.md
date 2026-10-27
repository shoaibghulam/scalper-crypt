# Review and corrections of the three existing bots

This is the current correction record. It supersedes conflicting claims in the
older video-only notes. Strategy IDs and existing bot names remain unchanged.
These are rule-fidelity and execution corrections, not a measured improvement
in win rate, profit factor or live deployment readiness.

## Evidence reviewed

All three local MP4 files were decoded. Timeline contact sheets sampled the
649-second ORB video every 30 seconds, the 1,290-second Black video every 60
seconds, and the 68-second Legend video every 5 seconds. Relevant rules were
examined at full-resolution timestamps. These are sampled visual reviews, not
claims to have inspected every frame.

Local `faster-whisper==1.2.1`, `base.en`, CPU int8, produced timestamped narration
transcripts for the long videos. The speech environment and downloaded model
are isolated under `var/video-review-20260918/`; the videos were not uploaded.
ASR is imperfect: use timestamps and original video to check uncertain wording.
The Legend ASR was rejected because it contradicted the visible captions;
Legend findings rely on the burned-in captions and chart instead.

| Video | Existing bot | Decisive evidence |
|---|---|---|
| 3 Step BEGINNER Scalping Strategy (500 Trade BACKTEST) | `500-backtest-v1` | 01:22-01:49 hourly bias; 03:28-04:04 hours/range/5m close; 04:54-05:21 retracement and engulfing; 09:09-09:23 edge retest variant |
| The 1 Minute Scalping Strategy (That Actually Works) | `the-minute-black` | 06:18-06:44 wickless pullback; 07:01-08:01 doji; **13:27-13:49 size compared with at least one prior candle**; 15:41-16:03 both wicks |
| The BEST 1 Minute Scalping Strategy | `1-minute-legend-v1` | 00:15-00:45 demand/nearby low/rejection/volume; **00:53-00:55 one ATR**; subsequent recent-high target and break-even captions |

Narration artifacts: [ORB](evidence/orb-transcript.txt),
[Minute Black](evidence/black-transcript.txt). Examples:
[wickless checklist](evidence/black-390.jpg),
[stop checklist](evidence/black-490.jpg),
[one ATR](evidence/legend-54.jpg),
[range and position example](evidence/orb-410.jpg).

## 500 Backtest V1 ? 24/7 crypto adaptation

**Updated at the user's request:** fixed 7 a.m./8 a.m. references, New York
entry hours and weekday restrictions have been removed. The source video's
session rules remain in the evidence above; they are deliberately not the
current bot configuration.

The bot evaluates the same breakout/retest pattern at any time, including weekends:

1. At each completed 5m candle, inspect candidate breakouts from the previous
   30 minutes, oldest eligible first.
2. For each candidate, freeze the 15-minute range immediately before that
   breakout. Form two consecutive 60-minute OHLC blocks immediately before the
   range. These are rolling blocks, not 07:00/08:00 or clock-hour references.
3. Require an engulfing relationship between those two hourly bodies for bias,
   then a completed 5m breakout in that direction.
4. Require a later closed 5m engulfing retest at the broken range edge, with the
   existing 15%-of-range touch tolerance. Do not enter the breakout itself.
5. Reject missing/duplicate reference candles, a close through the opposite
   range edge, or a repeat confirmation for the selected breakout.
6. Keep the executable-price check, half-range chase bound, 20-second signal
   expiry, opposite-range stop, 0.45% stop-distance floor and 2R target.

The scanner/planner retain a 400-minute history request. Candle lengths and
signal expiry define the pattern and freshness; they do not restrict which
hours or days the bot can trade. Signals are still required: 24/7 eligibility
does not mean continuous order placement.

This is an intentional crypto adaptation rather than a literal opening-range
copy. Its profitability has not been established. Tests shift identical long
setups across overnight, daytime, weekday, weekend and date-boundary timestamps
and assert identical entry eligibility and protective levels.

## The Minute Black

Two errors in the previous extraction are corrected:

- "Clean" means **no HA wick**, not a wick up to 10% of range. Only four
  floating-point ULPs of rounding tolerance are allowed.
- The presenter's "high volume" label describes **HA candle height**, explicitly
  compared with **at least one** of the previous two candles. It does not mean
  exchange traded volume greater than both previous volume bars.

Doji detection retains the documented body <=25% of range definition and now
requires both upper and lower wicks. The source gives no numerical body ratio.

The separate prior-20-bar traded-volume average, EMA slope/real-close checks,
2-8 pullback length, 20-second expiry, 0.5 ATR chase bound, real-extreme stop with
0.1 ATR buffer, 0.45% floor and 1.5R target remain **engineering adaptations**.
They are not attributed to the presenter.

Correction to older prose: synthetic HA values need not have traded, but this
alone does not make a stop order at that numerical level invalid. Using real
candle extremes is a design choice. The original video also presents a claimed
32-trade/24-win sample around 18:15; older notes saying it has no sample are
incorrect. That sample does not establish performance for this bot.

## 1 Minute Legend v1

- A rejection wick now excludes the candle body: long wick is
  `min(open, close)-low`; short wick is `high-max(open, close)`.
- A liquidity reference already swept by an intervening bar is refused.
- Stop distance includes closed 1m Wilder ATR14, then widens if needed to cover
  the sweep extreme and the existing 0.45% fee-distance floor.
- Scanner and levels check the **executable price** against the reclaimed level
  and structural target. They refuse less than 1.5R and never replace a failed
  structural target with an invented fixed-ratio target.
- Signals expire after 20 seconds.

**Remaining adaptations:** demand is still represented by a 3-candle FVG proxy;
this exact formula is not specified by the video. Shorts mirror the long-only
example. The activity filter is a heuristic, not a guarantee of exit within
30 minutes. Discretionary break-even management remains unimplemented because
its trigger is unspecified. No claim is made that omitting it improves expectancy.

## Shared safety and evaluation

All three reject non-finite/malformed candle prices, negative volume, invalid
bid/ask and crossed books. The scanner checks before indicator calculations.
The entry planner converts strategy bracket rejection into `PlanRejected` so
continuous bots can skip invalid entries without placing an order.

The offline replay now selects all three existing strategies:

```powershell
# From backend; CSV columns: open_time,open,high,low,close,volume
.venv/Scripts/python.exe -m scalper.backtest candles.csv --strategy the-minute-black --output black.json
.venv/Scripts/python.exe -m scalper.backtest candles.csv --strategy 500-backtest-v1 --output orb.json
.venv/Scripts/python.exe -m scalper.backtest candles.csv --strategy 1-minute-legend-v1 --output legend.json
```

Input uses consecutive UTC Unix-millisecond 1m opens. Supply enough history
before each signal, including the ORB's rolling two-hour bias and pre-breakout range. Replay
uses next-bar real open, configured fees/slippage, conservative stop-first
resolution when both exits are touched, and only completed 5m history. It is
not a live paper broker or an order-book/funding simulation. Invalid execution
candles are rejected rather than silently distorting P&L.

Use separate development and out-of-sample periods, identical costs, and compare
net expectancy, drawdown, trade count and profit factor against the prior version.
No historical performance dataset was supplied or evaluated in this review;
synthetic regression fixtures prove behavior, not predictive accuracy.

## Verification

**The original video-review checks passed. The subsequent 24/7 adaptation is
verified in `var/video-review-20260918/crypto-24h-verification.txt`.**
Two existing dependency deprecation warnings remain. See
`var/video-review-20260918/verification.txt` for the full test/coverage run.
New tests exercise rolling bias, retest sequencing, unrestricted hours/weekends,
wick/body distinction, candle-size comparison, executable-price rejection,
malformed data and causal replay entries for all three strategies. Existing
risk, exchange-recovery and frontend interfaces are retained. No engine was
started, live order submitted or deployment performed during this review.

Backups of pre-review strategy modules and unit tests are under
`var/video-review-20260918/backup/`. Earlier full source backups remain available.
Revalidate on testnet before using these changed entry rules with real funds.
