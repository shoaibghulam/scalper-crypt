# Five setups from the supplied 5-minute PDF

Source: [5_minute_crypto_trading_strategies.pdf](../../../new%20strategies/5_minute_crypto_trading_strategies.pdf), September 2026, pages 3–6.

These are deterministic research adaptations, not reproductions of proven returns.
They appear under **PDF 5m:** in the backtest catalog and testnet strategy registry.
No bots are created or started by adding these definitions.

| ID | PDF setup | Signal | Exit |
|---|---|---|---|
| `pdf5-ema-pullback` | EMA trend pullback, p. 3 | EMA9 > EMA21 > rising EMA50; pullback and rejection; inverse for shorts | 1.5R |
| `pdf5-vwap-continuation` | VWAP trend continuation, pp. 3–4 | Rising session VWAP and EMA9/21; rejection of VWAP or EMA zone | Prior 20-bar extreme, frozen at entry |
| `pdf5-vwap-reversion` | VWAP mean reversion, pp. 4–5 | Flat session VWAP, RSI extreme, close back inside 2-sigma band | Session VWAP at signal, frozen |
| `pdf5-bb-rsi-reversion` | Bollinger + RSI mean reversion, p. 5 | BB(20,2) re-entry, RSI extreme, low-trend regime | Middle band at signal, frozen |
| `pdf5-volume-breakout` | Breakout + volume + ATR, p. 6 | 20-bar compression, close breakout with volume and ATR expansion, first retest | 2R |

## Decisions needed to automate discretionary language

All parameters below are engineering choices, not optimized on the user's results.
Both directions mirror the same rules. All inputs are closed candles.

- Entry timing: assess at a newly closed 5-minute candle, then enter at the next available executable price. Reject entry movement greater than 0.5 of the signal's ATR.
- ATR: Wilder ATR(14) on 5-minute candles. RSI: Wilder RSI(14) on 5-minute closes.
- Rejection for the trend strategies: directional body and either a rejection wick at least as long as the body, or engulfing the previous opposite-color body.
- EMA pullback: EMA50 moves by more than 0.1 ATR over three bars; no more than two EMA9/21 direction flips across the last ten transitions. The immediately preceding bar intersects the prior EMA9/21 zone; the preceding three closes stay on the trend side of their EMA50. Confirmation closes beyond EMA9 with volume at least the prior 20-bar mean. Stop reference is the four-bar pullback extreme.
- VWAP: reset at **00:00 UTC**, use volume-weighted 5-minute typical prices `(high + low + close)/3` and their weighted population deviation. This is a candle approximation to session VWAP, not trade-level VWAP. Require the complete session from midnight and at least twelve session candles for every VWAP value used. Three-bar slope checks therefore wait until at least the fifteenth session candle. Load 300 five-minute bars to retain a full day; never substitute rolling VWAP for an incomplete session.
- VWAP continuation: directional VWAP slope greater than 0.1 ATR over three bars; close within two ATR of VWAP; EMA9/21 aligned. Previous bar touches VWAP or the EMA zone. A rejection closes beyond EMA9 with volume at least the preceding 20-bar mean. Target is the previous 20-bar high/low excluding the pullback and trigger, used as a deterministic prior-swing proxy. If that target has insufficient room, reject the setup rather than invent a farther target.
- Both mean reversions: ADX(14) <20 and absolute three-bar EMA50 change <=0.2 ATR. The excursion candle closes outside its own 2-sigma band with RSI <=35 / >=65. The next candle has a directional body and closes inside its own band, still on the excursion side of the mean. This selects the explicit RSI-threshold branch, not optional divergence.
- VWAP reversion: three-bar VWAP drift <=0.2 ATR; no comparison across UTC midnight. A complete UTC-aligned 15-minute close breaking the prior four complete 15-minute candles against the proposed trade, with volume >1.5 times their mean, vetoes entry.
- Bollinger reversion: population standard deviation of 20 closes, multiplier 2. Reject when current deviation exceeds the previous deviation by more than 20%. Target the middle band; no opposite-band extension.
- Breakout: freeze the preceding 20-bar high/low. Compression means width <=6 pre-breakout ATR and pre-breakout ATR <=ATR twenty bars earlier. Breakout must close outside the range, have volume >=1.5 times the prior 20-bar mean, and increase ATR. Enter on the first retest within the next three bars, closing back outside the range with a directional body. Retest may miss the boundary by at most 0.1 ATR or penetrate by at most 0.5 ATR. An earlier intervening touch invalidates a later retest.
- Structural stop: 0.1 ATR beyond the rejection/excursion extreme, with at least 1 ATR distance for setups 1–4, or 1.25 ATR for breakout. The existing platform 0.45% minimum stop distance also applies. Structure can make the stop wider than the PDF's approximate ATR ranges; it is never moved inside the invalidation swing merely to fit a range.
- Existing platform minimum reward/risk is 1.5R. Fixed mean/swing targets that fail this constraint are rejected, not extended. Tick rounding preserves this rule and rounds fixed targets toward entry. This can make mean-reversion signals infrequent.

## Explicitly omitted optional or unsupported behavior

The PDF offers alternatives rather than a single executable specification. This implementation selects a single full target exit; it does not scale out, trail EMA stops, trade RSI divergence, use volume profile, apply the optional 15-minute trend filter to trend setups, identify scheduled news, or infer historical order-book liquidity. The VWAP setup's positive-momentum alternative uses its volume-confirmation branch instead of MACD. Manual support/resistance confluence is not inferred for the Bollinger strategy. Reversion uses low ADX, slope and band filters instead of an additional discretionary band-walking detector.

The PDF's research protocol (walk-forward optimization, 200–300 trades, funding, MAE/MFE, latency stress and a dedicated daily -R stop) is not implemented merely by registering these strategies. Existing research reporting still uses three chronological independent-capital periods, base/stressed spread and slippage, and its existing sample thresholds. Do not mistake those reports for completion of the PDF's full validation protocol.

## Risk and replay correction

The long-range form now exposes **Risk per trade (%)**. The existing 1% default is preserved; choose 0.25% or 0.50% to follow the PDF's suggested research range. Leverage, capital and costs remain user/platform assumptions; funding is still absent.

The audit's entry-price bug was also corrected: replay now calls entry validation and bracket construction with the simulated fill, not the previous close. Signals still use only previously closed data. Rejected entry-level checks count as rejected entries. This correction changes the model fingerprint and can change new results for existing strategies. Old saved reports are preserved. This does not establish complete live-execution parity: historical ticks, scanner gates, funding, liquidity and partial fills remain limitations.

## Verification

Tests cover all five catalog entries; long/short rules; incomplete, gapped and unclosed data; UTC session resets and volume weighting; entry chase limits; fixed targets and rounding; stop floors; and actual-price replay validation. Synthetic fixtures prove code paths are reachable, not that the strategies are profitable.
