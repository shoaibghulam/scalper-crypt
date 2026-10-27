# IVB video review

Source: `new strategies/I Coded Fabio Valentini’s IVB Model — The Results Shocked Me_120.mp4`.

Reviewed on 2026-09-20. Duration: 27:19. Reviewed all 52 contact sheets covering one sampled frame per second, enlarged selected settings screens, and read a locally generated audio transcript. This is a one-second visual review, not an inspection of every original 24-fps frame. Automatic transcription can contain errors; numerical settings below are cross-checked against enlarged screens where stated.

Review artifacts are in `var/ivb-video-review/`: `sheet-001.jpg` through `sheet-052.jpg`, selected `detail-*.jpg` frames, and timestamped `transcript.jsonl`.

## What the model is

The presenter builds an Initial Volatility Breakout bot from a short, explicitly vague Fabio Valentini reel. This is the presenter's interpretation and subsequent optimization, not a complete disclosure of Fabio's actual strategy. The instrument shown in later tests is MNQ (Micro E-mini Nasdaq futures), with 5-minute bars. It is not a crypto backtest.

1. Record the session high and low between 09:30 and 10:00 New York time.
2. Require a sufficiently contracted initial balance (IB).
3. After the range closes, enter a breakout above the high or below the low with volume confirmation. Direction can be both, long only, or short only.
4. Use ATR-based stop and target distances.
5. Limit daily trades; the presenter adds a default of two after inspecting repeated entries.
6. End the entry window and flatten at 16:00 New York time.

New York time must follow daylight saving time. A fixed UTC offset would shift the session for part of the year.

## Initial version versus revised version

| Setting | Initial screen | Later changes |
|---|---|---|
| Opening range | 09:30–10:00 ET | Unchanged in demonstrated model; 15 minutes is only proposed later |
| Entry timeframe | 5 minutes | Unchanged |
| Range filter | Maximum 100 price points; minimum 0 | Replaced by today's IB width divided by a trailing average of prior daily IB widths |
| Contraction bounds | Fixed price points | Initial adaptive defaults: minimum 0.05, maximum 0.50; minimum later set to 0 for optimization |
| IB history | Not applicable | Adaptive settings screen shows 40 trading days; optimization tests 5–25 days |
| Volume | 1.5 × intraday regular-session average | Optimized; discussed examples use 1.1 × the prior 5-bar average |
| Volume lookback | 20 bars | Changed/tested, including 5 bars |
| ATR period | 14 | No subsequent change established |
| Stop | 2 × ATR | Optimized, discussed later examples use 3 × ATR |
| Target | 3 × ATR | Later examples use 2 × ATR |
| Risk | 1% per trade | Later raised for prop-firm simulations; this is not leverage or margin amount |
| Daily trade cap | Initially absent | Added with default 2 |
| Session close | Flatten at 16:00 ET | No subsequent change established |

A concrete row visible at 22:32 has maximum contraction 0.7, IB lookback 25 trading days, volume multiplier 1.1, volume lookback 5 bars, stop 3 ATR and target 2 ATR. It reports 206 trades, 65.05% wins, profit factor 1.34 and return 22.46% in that optimizer. These are source-screen results, not independently reproduced results or expected crypto performance. Another discussed row uses contraction 0.9. There is no single universally validated final parameter set.

The presenter describes later outcomes as a negative reward-to-risk approach: the average loss exceeds the average win. His early spoken description of a “one to two” ratio should not be substituted for the actual stop/target columns.

## Timestamp guide

- **00:40–00:55:** Original reel is described as vague.
- **02:27–03:06:** Presenter explains that exact rules are unavailable and the implementation needs verification.
- **03:14–03:45:** Opening balance, breakout and volume rules.
- **05:35–06:45:** Session, direction, volume, ATR and exit settings.
- **06:56–10:01:** First grid search; profitable settings often have too few trades.
- **10:41–12:04:** Fixed-point range replaced by adaptive contraction ratio.
- **12:20–14:00:** Visual replay and addition of a daily trade cap.
- **16:39–17:30:** Meaning of contraction bounds and prior-day lookback.
- **18:10–18:31:** Out-of-sample validation removed to speed up the search; presenter proposes doing it later.
- **19:28–22:21:** Optimization results; looser filters and wider stops discussed.
- **22:28–23:05:** Selected configuration rerun; negative reward-to-risk outcomes discussed.
- **23:18–23:57:** Regime/month analysis; five consecutive losing months noted.
- **24:11–25:49:** Prop-firm simulations with changing risk.
- **25:49–26:19:** Presenter explicitly says this differs from Fabio's original model and discusses weaker volume filtering.
- **26:59–27:09:** Presenter says out-of-sample validation is still needed.

The changing results in the video do not establish a backtester bug. They involve changing settings, optimization selections and reruns. The brief overlapping text is a UI issue, not proof of incorrect calculations.

## Requirements for an honest crypto implementation

Status: **implemented for backtesting** as `ivb-crypto-opening-range`, displayed as **IVB 5m: New York Opening Range (Crypto)**. It is excluded from the execution registry, Create Scalp and automated trading. Favorites remain available, with a Backtest only status and no trading action. The video's 2/3R bracket conflicts with the application's 1.5R execution minimum; that account rule has not been weakened.

The implementation uses the documented 0.7 contraction / 25 prior days / 1.1 volume multiplier / 5 prior bars / ATR14 / 3 ATR stop / 2 ATR target example. It runs seven days a week with New York DST. A fresh closed-bar crossing is required, entries occur at the next 1m open with configured costs, and at most two filled entries are allowed per coin/session within each independent replay period. Rejected entries do not consume the cap. ATR is computed on the last 100 completed 5m bars and brackets are frozen relative to the actual fill. In replay, any remaining position exits at the 16:00 bar's open before that bar's price path. Session-end exits are reported separately from stop, target and period-end exits.

Select the strategy in the long-range **Backtest & Research** form and select the desired coins. The download automatically includes 38,880 one-minute warm-up candles (27 days) before the scored duration. The small CSV research form's 10,000-candle limit cannot accommodate this model. Initial-balance averages exclude today and require all 25 prior daily opening ranges; gaps are not silently replaced. Parameters are a fixed catalog preset, not fitted to the user's data.

A crypto port must be labeled “IVB — video-inspired crypto adaptation.” Crypto has no Nasdaq cash opening session, and neither the source's performance nor its claimed index directional bias transfers automatically.

Implementation decisions and remaining execution requirements:

- Whether the adaptation runs Monday–Friday or all seven days, and how holidays are treated. Prior-session averages must use completed eligible sessions only, excluding today's range.
- Enough stored/fetched history to calculate 5–40 prior session ranges. The existing strategy context normally truncates history; a short rolling candle window cannot stand in for daily opening ranges.
- Exact breakout event: fresh closed-bar cross versus repeated closes outside the range; entry must occur after the signal with executable prices.
- Volume averaging excluding the breakout candle and using only eligible intraday bars. The complete source code is not available to settle every indexing detail.
- Daily cap counted from executed entries, with per-symbol/session state and restart recovery. Counting calls to `assess()` would be incorrect.
- A scheduled 16:00 ET exit in both replay and execution, including weekends/holidays policy and missing candle handling. The current base `Strategy` interface exposes entry assessment and protective levels, but no general scheduled-exit hook.
- Whether ATR is frozen at signal time, exact smoothing, and whether exits move afterward; these details are not fully specified by the presentation.
- Crypto fees, slippage, funding, minimum order sizes and position sizing. Source risk-percent sizing must not silently become margin-percent sizing.
- Separate untouched validation periods, baseline comparisons and sensitivity checks. The 9,900-run full-history search is model selection, not independent validation.

No profitability claim is established by this review, and no orders or bots were started.
