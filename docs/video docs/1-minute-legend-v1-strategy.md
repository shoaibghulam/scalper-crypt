> Superseded where conflicting: [video and narration re-review, corrected bot rules](strategy-review-2026-09-18/README.md). Earlier extraction missed important narrated rules; do not treat the historical analysis below as the current specification.

# 1 Minute Legend v1

Source: `docs/video docs/The BEST 1 Minute Scalping Strategy.mp4` — 68 seconds,
360x640 vertical short, burned-in captions. Extracted at 1 fps; the full
caption track was recovered from the frames and is transcribed below verbatim.

Screenshots: `docs/video docs/screenshots-legend/`

---

## 1. What the video actually says

Transcribed from the burned-in captions, in order. Bracketed words are audio
that the caption track clipped; everything unbracketed is on screen.

| Time | Caption |
|---|---|
| 0-2s | THIS IS THE BEST TRADING STRATEGY |
| 3-4s | THE FIRST THING IS IDENTIFY THE TREND |
| 5-8s | CLEARLY HERE WE'RE MAKING HIGHER HIGHS SO THE TREND IS UP |
| 9-11s | THE [next thing you] WANNA DO IS IDENTIFY A DEMAND LEVEL [in] AN UPTREND |
| 12-14s | WE HAVE AN IMBALANCE RIGHT HERE |
| 15-17s | A PAUSE BEFORE IT CAN CONTINUATION HIGHER |
| 18-21s | [orders] WERE NOT FILLED COMPLETELY AT THIS AREA DUE TO HOW FAST PRICE ACCELERATED TO THE UPSIDE |
| 22-25s | [you] WANNA IDENTIFY A CLEAR LOW THAT REST JUST [below the area] OF DEMAND |
| 26-30s | WHICH MEANS THAT [orders are] RESTING BELOW THAT AREA AND OVERLAPPING WAS PREVIOUSLY UNFILLED ORDERS |
| 31-34s | [at the] DEMAND LEVEL [there is an] OVERWHELMING AMOUNT OF ORDERS RESTING IN [it] |
| 35-38s | WE SHOULD SEE A MOVE INTO [it and] A SHARP REJECTION |
| 39-40s | [if not] GET OUT RIGHT AWAY |
| 41-45s | WHEN [price comes] DOWN INTO THAT AREA [I] GET A NICE REJECTION CANDLE HERE |
| 46-49s | TO SEE AN INCREASE OF VOLUME AS WELL WHICH CAN CONFIRMS ORDERS SITTING THERE |
| 50-53s | ONCE I GET [that] I'LL ENTER THE POSITION AND KEEP [it] SUPER SMALL |
| 54-55s | USING JUST ONE [candle] AS MY STOP LOSS |
| 56-58s | AND BECAUSE WE'RE [in an uptrend] I WILL ALWAYS TARGET THE MOST [recent high] |
| 59-61s | ONCE PRICE MOVES IN MY FAVOUR [I] IMMEDIATELY PUT MY [stop to] BREAK EVEN |
| 62-63s | [so I] HAVE ZERO RISK |
| 64-66s | AND NOW LET'S WATCH WHAT HAPPENS BOOM |
| 67-68s | [check out my] ORDER ZONES AND CHECK OUT [the link] |

### The six steps, as stated

1. **Identify the trend.** Higher highs means the trend is up.
2. **Identify a demand level** — an *imbalance*: a pause where price accelerated
   away so fast that resting orders were never filled.
3. **Identify a clear low that sits just below that demand area.** The stated
   reason is that stop orders rest below an obvious low, and those overlap the
   previously unfilled orders.
4. **Wait for price to move into the area and reject sharply.** If the rejection
   does not come, "get out right away."
5. **Confirm with an increase in volume**, which he says confirms orders sitting
   there.
6. **Enter on the rejection candle**, stop one candle away, target the most
   recent high, and move the stop to break-even as soon as price moves in favour.

---

## 2. The number that decides everything

At 51-53 seconds the order ticket is legible on screen:

> **`Stop: 40.75 (0.18%) 163, Amount: 1980`**

A second ticket a moment later reads `Stop: 12.50 (0.05%) 50, Amount: 1980`.

His stop distance is **0.18%** of price, and in one case **0.05%**.

That is the whole problem, and it is not a small one. Binance USDⓈ-M charges
VIP 0 taker 0.0500% per side. A market-in, market-out round trip costs 0.090%
of notional before any slippage.

| Stop distance | Fees as a share of the stop | Break-even win rate at 2R | at 1R |
|---|---|---|---|
| 0.05% (his second ticket) | **200%** | **100.0%** | 150.0% |
| 0.18% (his first ticket) | **56%** | **51.9%** | 77.8% |
| 0.45% (this platform's floor) | 22% | 40.7% | 61.1% |

At a 0.05% stop the round trip costs twice what the trade risks. There is no
win rate that makes it profitable — 100% at 2R means every single trade must
reach target and none may lose, and even then the edge is zero. That trade is
not a marginal one; it is arithmetically dead on arrival.

At 0.18% it is merely very hard: better than a coin flip is required at 2R,
against a pattern that his own video says to abandon ("get out right away") when
it fails, which caps the winners while leaving the losers intact.

### Why the video can show it working anyway

Three reasons, none of which transfer.

- **He is not on Binance perpetuals.** The chart is a TradingView layout with
  point values (40.75 points = 0.18%) consistent with an index or FX instrument,
  where per-side commission is a fixed ticket fee rather than a percentage of
  notional. A fixed fee on a 1980-unit position behaves completely differently
  from 0.05% of notional.
- **One trade is not evidence.** The video shows a single winning example. At a
  52% required win rate, a run of one proves nothing at all.
- **Break-even stops flatter the win rate and destroy the expectancy.** Moving
  the stop to entry as soon as price ticks in your favour converts would-be
  winners into scratches. It makes the win-rate column look better and the
  profit-factor column worse, which is exactly the trade the History tab was
  built to expose.

**This is why the bot is not a copy of the video.** The structure is kept; the
stop sizing is replaced with something that can clear its own costs.

---

## 3. What is actually good here

Stripped of the marketing, the pattern is a recognised one: a **liquidity sweep
into an unfilled imbalance, with a rejection close**. That is a real structure
and the reasoning behind it is coherent:

- An imbalance (a fair-value gap) marks a price band that was crossed too fast
  for resting limit orders to fill. Those orders, if they are still wanted, are
  still there.
- An obvious swing low collects stop orders beneath it. Those stops are sell
  orders that fire on the way down.
- When price sweeps that low, the stop-driven selling is absorbed by the unfilled
  demand. If that absorption is real, price rejects hard and closes back above
  the low.
- A rejection with elevated volume is more likely to be absorption than drift.

The failure mode is equally clear and the video names it: if the sharp rejection
does not come, the level did not hold, and the trade is wrong immediately. That
is a genuine, testable invalidation, and it is what makes the pattern worth
implementing rather than the confidence with which it is delivered.

---

## 4. The adaptation, decision by decision

Every place the implementation departs from the video is listed here, with the
reason. Nothing is changed silently.

| # | Video says | Bot does | Why |
|---|---|---|---|
| A1 | Stop one candle away (0.18%, 0.05% observed) | Stop at the sweep extreme, floored at **0.45%** and sized from 5m ATR | At 0.18% fees take 56% of the risk; at 0.05% they take 200%. A stop under the floor is refused outright rather than traded at a loss-making cost ratio. |
| A2 | Target "the most recent high" | The most recent swing high, but **only if it is at least 1.5R away** | A target nearer than 1.5R cannot clear fees at any plausible win rate. If the recent high is too close, there is no trade rather than a bad one. |
| A3 | Move stop to break-even as soon as price moves in favour | **Not implemented in v1** | It raises win rate and lowers expectancy. It is a real technique but it needs measuring before it is trusted, and this platform judges a strategy on profit factor. Revisit with data from the History tab. |
| A4 | "Identify the trend" by eye | Higher highs **and** higher lows over a fixed lookback, on closed bars only | "Clearly here we're making higher highs" is not a rule a machine can apply. Requiring both makes it one. |
| A5 | "Imbalance" identified visually | 3-bar fair-value gap: `bar1.high < bar3.low`, gap unfilled since | The standard mechanical definition of the thing he is pointing at. |
| A6 | "A clear low that rests just below" | A swing low within a bounded distance below the gap | "Just below" is unquantified. Bounded so an unrelated low fifty bars away does not qualify. |
| A7 | "Sharp rejection" | Close back above the swept low, with the lower wick a majority of the candle range | "Sharp" needs a number. The wick is what makes a rejection visible. |
| A8 | "Increase of volume" | Volume above a rolling median of recent bars | Relative, not absolute, so it works across symbols of different sizes. |
| A9 | Longs only (his example is an uptrend) | Both directions, mirrored | The logic is symmetric; restricting to longs would halve the sample for no stated reason. |
| A10 | No spread or liquidity filter | Inherits the platform's economic gates | A 1-minute pattern on an illiquid symbol is a spread-harvesting machine pointed at yourself. |

---

## 5. Honest expectations

This bot is **not** "100% profitable", and no bot is. What can be said:

- The structure is real and mechanically testable.
- The version in the video, traded on Binance perpetual fees at the stop
  distances it shows, loses money. That is arithmetic, not opinion.
- The version implemented here has its costs cleared by construction: the stop
  floor and the 25% fee-to-stop cap mean no trade is taken that cannot pay for
  itself at a plausible win rate.
- Whether the remaining edge is positive is an empirical question that needs
  several hundred out-of-sample trades to answer. The platform's own validation
  gate is net profit factor above 1.3 over 500+ trades across 15+ symbols, and
  this strategy has not passed it yet because it has not been run yet.

The single most useful thing to watch after switching it on is the **profit
factor and expectancy on the History tab, not the win rate.** A high win rate
on this pattern is the expected symptom of break-even stops and early exits, and
it is compatible with losing money.

---

## 6. Unknowns the video does not resolve

Listed so nobody later mistakes our choice for the source's.

1. **What instrument.** Point values suggest an index or FX product, not a
   crypto perpetual. Fee structure therefore differs fundamentally.
2. **What "just below" means** in distance terms for the swing low.
3. **How long a sweep may take.** One candle? Three? The video shows one.
4. **Whether the rejection close must clear the swept low or the whole zone.**
5. **What happens when the recent high is very close.** He says "always target
   the most recent high" with no minimum distance.
6. **Any sample size at all.** No backtest, no trade count, no win rate. The
   claim rests on one illustrated trade.

---

## 7. What happened when it was switched on

First live scan against Binance testnet, 528 USDT perpetuals:

| | |
|---|---|
| Universe | 528 symbols |
| Passed liquidity gates | 47 |
| Economically tradeable | 41 |
| Setups firing | **0** |
| Scan duration | 4.6s |

Zero is the expected and correct answer for a specific structural pattern on a
single snapshot. What matters is the distribution of *why* nothing fired, and
it is spread sensibly across the stages rather than piling up on one:

| Count | Reason |
|---|---|
| 15 | the level has not been swept yet |
| 13 | no trend: highs and lows are not both rising |
| 9 | no clear low resting just beyond the imbalance |
| 4 | no unfilled imbalance to defend the level |

A pattern that fired on 20 of 41 symbols at any given minute would not be a
pattern; it would be a noise detector. Every rejection reason is rendered in
the Create Scalp table, so the scanner can be tuned against evidence rather
than guesswork.

### Three bugs this found in the rest of the platform

Switching it on surfaced failures that had nothing to do with the strategy and
everything to do with running real orders:

1. **Every order was being rejected with `-1100 Illegal characters in
   newClientOrderId`.** Client order ids are derived from the bot name so that
   the exchange refuses a duplicate after an ambiguous timeout. Giving bots
   readable names put spaces into that id, which Binance rejects outright. Now
   sanitised — and replaced rather than dropped, so "Scalp A" and "ScalpA"
   cannot collide on one id.
2. **`-4028 Leverage 50 is not valid`.** Binance caps leverage per symbol and
   per notional tier; INJUSDT allows 25x, not the 50x requested. With the
   operator ceiling now at 100x this is routine, so leverage is clamped to the
   symbol's published bracket and the clamp is logged rather than silently
   killing the bot.
3. **The clamped leverage was not carried through re-validation.** The plan was
   re-checked against the leverage the operator asked for rather than the one
   actually in force, which would have run the liquidation-headroom guard at
   the wrong number.

### Two bugs an adversarial review found in the strategy itself

Both were reproduced against the real code before being fixed, and both are
now pinned by regression tests.

1. **A long could be given a take-profit BELOW its own stop-loss.** The target
   is the most recent swing high, and pivots are detected with a strict
   comparison - so two adjacent bars sharing a high, which happens constantly
   on a tick grid, disqualify the rally top. The scan then walked back and
   returned an older, *lower* high. The reward-to-risk gate did not catch it
   because the reward was measured with `abs()`, so a target on the wrong side
   scored as a reward, and scored better the further wrong it was. Fixed by
   requiring the target to be beyond the entry and by making the reward signed.
   An invariant test now asserts the bracket straddles the entry on every
   series that fires.
2. **The rejection bar could close through the imbalance and still count it as
   unfilled.** The fill check ran over every bar except the trigger bar, whose
   close is the entry price - so a trade could enter on a bar that had just
   spent the zone it claimed to be defending. On the first live scan after the
   fix, 4 of 42 tradeable symbols were rejected for exactly this, so it was not
   a theoretical case.

## 8. What would make this trustworthy

Not yet done, in order of value:

1. **A backtester.** Everything above is reasoning; none of it is evidence.
   The gate is net profit factor above 1.3 over 500+ out-of-sample trades
   across 15+ symbols.
2. **Rank the universe by 1-minute realised volatility** and trade the top
   cohort. Time to touch a barrier scales with the square of distance over
   volatility, so this is the only filter with a quadratic payoff.
3. **Session gating.** Volatility and volume periodicity in crypto is
   documented and strengthening; the 13:30-16:30 UTC window and the funding
   stamps at 00:00/08:00/16:00 are real anchors.
4. **Model slippage in the cost figure.** One basis point of unmodelled cost
   raises the required win rate by about 1.1 points at 1R. The 25%
   fee-to-stop gate should be computed against all-in cost, not exchange fees.
5. **Maker entries**, which would cut the round trip from 0.090% to 0.063%.
   Only for the reversion side: a resting buy fills when sellers arrive, which
   is precisely when a breakout thesis is failing.
