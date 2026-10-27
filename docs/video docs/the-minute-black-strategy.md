> Superseded where conflicting: [video and narration re-review, corrected bot rules](strategy-review-2026-09-18/README.md). Earlier extraction missed important narrated rules; do not treat the historical analysis below as the current specification.

> Implementation upgrade (18 September 2026): see [current rules and operating controls](the-minute-black-upgrade.md). The source-video analysis below remains historical context.

# The Minute Black

Source: `docs/video docs/The 1 Minute Scalping Strategy (That Actually Works)_120.mp4`
â€” 21m30s, 1920x1080, "The Edge Funder". No subtitle track and no burned-in
captions, so the strategy was recovered from the on-screen checklist the
presenter displays at 3:15â€“4:20, cross-checked against the live chart he trades.

Screenshots: `docs/video docs/screenshots-black/`

---

## 1. What the video says

At 2:00 the claims slide reads: **PROFITABLE Â· REPEATABLE Â· BASIC PRICE ACTION Â·
ONE TIMEFRAME Â· SIMPLE CHECKLIST.**

At 3:15 he puts the whole strategy on screen as a six-point checklist. Point 1
is sharp; points 2â€“6 are deliberately blurred by the editor, but the blur is
mild enough to read at magnification and the wording was confirmed against the
trades he then demonstrates.

> **1. Chart Setup**
> - Heikin Ashi candles
> - 100 EMA on chart
>
> **2. Market Structure**
> - Only take buys if price is above the 100 EMA
> - Only take sells if price is below the 100 EMA
>
> **3. Clean Pullback**
> - At least 2 clean opposite colour candles in pullback direction
> - Clean = no wicks on top for buys / no wicks on bottom for sells
>
> **4. Entry Signal**
> - Enter on a high volume doji (as soon as candle closes)
> - High volume = larger or same size as last 1-2 candles
>
> **5. Stop Loss (SL)**
> - Sell, place SL above doji wick
> - Buy, place SL below doji wick
>
> **6. Take Profit (TP)**
> - 1:1 risk to reward ratio minimum

The EMA settings panel is visible at 3:40: **Length 100, Source Close, no
smoothing.** The chart header reads **E-mini Dow Jones ($5) Futures Â· 1 Â· CBOT
(YM1!)**.

---

## 2. Two things that do not transfer

### 2.1 The instrument, and therefore the stop

He trades **YM1!, the E-mini Dow future**, at about 47,980 with $5 per point.
A stop placed beyond a 1-minute Heikin Ashi doji wick on that chart is roughly
**15 points â€” 0.031% of price.**

| | His instrument | Same stop on Binance USDâ“ˆ-M |
|---|---|---|
| Stop distance | 15 pts = **0.031%** | 0.031% |
| Cost of a round trip | **$4** flat commission | **0.090%** of notional |
| Cost as a share of the risk | **5.3%** | **288%** |
| Break-even win rate at 1:1 | ~53% | **194% â€” impossible** |

That is the whole difference. A futures commission is a fixed ticket fee, so a
tiny stop is cheap to trade. A percentage fee is not: at a 0.031% stop the round
trip costs nearly three times what the trade risks, and no win rate recovers it.

His 1:1 minimum compounds it. Even at this platform's 0.45% stop floor, 1:1
needs a **60% net win rate**. At 1.5:1 it needs 48%.

### 2.2 Heikin Ashi prices are not prices

This is the subtler problem and the more dangerous one.

A Heikin Ashi candle is synthetic:

```
HA_close = (open + high + low + close) / 4
HA_open  = (previous HA_open + previous HA_close) / 2
HA_high  = max(high, HA_open, HA_close)
HA_low   = min(low, HA_open, HA_close)
```

`HA_open` is an average of two previous averages. **It is a number that the
market never traded at.** The candle you see is a smoothed derivative of price,
not price.

So point 5 â€” "place SL below doji wick" â€” cannot be executed literally. The HA
low is `min(real low, HA_open, HA_close)`, and when the HA body is what sets it,
the level is fictional. An order there would sit at a price no trade ever
printed, and its distance from your real fill is unrelated to the risk you think
you are taking.

**The bot therefore detects on Heikin Ashi and executes on real prices.** Every
pattern rule â€” colour, clean wicks, doji â€” is evaluated on HA candles exactly as
the video specifies. Every price that reaches the exchange is a real one.

---

## 3. What is actually good here

Stripped of the claims, this is a **trend-pullback continuation** entry, and the
components are individually sensible:

- The 100 EMA is a slow filter on a fast chart. It does not try to time
  anything; it decides which side is permitted at all.
- Heikin Ashi is genuinely useful for *this* job. Its smoothing suppresses the
  single-bar noise that makes raw 1-minute candles nearly unreadable, and the
  "no wick" condition is a real HA idiom: an HA candle with no upper wick means
  the period never traded above its own smoothed open, which is a coherent
  definition of one-directional pressure.
- Requiring two clean pullback candles before entry means waiting for the
  counter-move to show structure rather than buying the first red bar.
- A doji is a genuine indecision bar, and requiring volume at least as large as
  the preceding bars distinguishes a pause where participants are active from
  one where everybody has gone home.

The weakness is that none of it is evidenced. The video shows selected replay
examples, no trade count, no win rate, no sample. "PROFITABLE" and "REPEATABLE"
are asserted on the title card and never measured.

---

## 4. The adaptation, decision by decision

| # | Video says | Bot does | Why |
|---|---|---|---|
| B1 | Heikin Ashi candles | HA for **signals**, real OHLC for **orders** | `HA_open` is an average of averages â€” a price that never traded. A stop placed there is at a fictional level, and its distance from the real fill is unrelated to the intended risk. |
| B2 | SL beyond the doji wick (~0.031%) | Stop from the doji's **real** extreme, floored at **0.45%** | At 0.031% the round trip costs 288% of the risk. On his futures contract a flat $4 commission is 5.3% of it; the two are not comparable. |
| B3 | TP at 1:1 minimum | **1.5:1 minimum** | At the 0.45% floor, 1:1 needs a 60% net win rate. 1.5:1 needs 48%. The platform already refuses anything under 1.5R. |
| B4 | 100 EMA, source close | 100 EMA on the **HA close** | What TradingView plots on an HA chart, so it matches what he sees. Stated because on a real-price chart the same setting gives a different line. |
| B5 | "2 clean opposite colour candles" | Exactly 2 or more consecutive, immediately before the doji | "At least 2" is explicit; the adjacency is not, but a gap would make it two separate pullbacks. |
| B6 | "Clean = no wicks on top for buys" | Wick â‰¤ 10% of the candle range, not exactly zero | A literal zero almost never occurs on a tick grid. The tolerance is the assumption; the intent is a decisive candle. |
| B7 | "High volume = larger or same size as last 1-2 candles" | Volume â‰¥ the max of the previous 2 candles | The wording admits either reading; the stricter one is taken. |
| B8 | One timeframe (1m only) | 1m for everything, 5m only for the **volatility gate** | Not a signal input. It answers whether a 0.45% stop is reachable at all on this symbol, which his instrument never had to ask. |
| B9 | No liquidity or spread filter | Inherits the platform's economic gates | A 1-minute pattern on a thin symbol is a spread-harvesting machine pointed at yourself. |
| B10 | Dow futures, RTH session | Binance USDT perpetuals, 24/7 | There is no opening bell. Session effects are real in crypto but are not implemented here; noted as future work. |

---

## 5. Honest expectations

The name asks for a bot; it does not make one profitable. What can be said:

- The pattern is mechanically well specified â€” more so than most, because the
  presenter wrote it down as a checklist rather than gesturing at a chart.
- Traded as shown on Binance perpetual fees it **loses money by arithmetic**,
  because a 0.031% stop cannot pay a 0.090% round trip.
- The version here has its costs cleared by construction: nothing is taken that
  cannot pay for itself at a plausible win rate.
- Whether the remaining edge is positive is unmeasured. The platform's gate is
  net profit factor above 1.3 over 500+ out-of-sample trades across 15+ symbols,
  and this strategy has not been run.

Watch **profit factor and expectancy** on the History tab, not win rate. A 1:1
target flatters win rate specifically â€” it is the easiest target to reach and
the one least able to pay for the losses.

---

## 6. Unknowns the video does not resolve

1. **Sample size.** No backtest, no trade count, no win rate. "PROFITABLE" is a
   title card.
2. **What counts as a doji** in body-to-range terms. Shown, never defined.
3. **Whether "last 1-2 candles"** means either or both.
4. **How far the pullback may travel** before the setup is void.
5. **Whether the EMA is on HA close or real close** â€” he never says; the chart
   implies HA close.
6. **Session.** He trades US cash-open hours on an index future. Crypto has no
   equivalent, and whether the pattern survives at 04:00 UTC is untested.
