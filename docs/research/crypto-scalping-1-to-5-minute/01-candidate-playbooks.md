# Candidate playbooks ? research specifications, not proven settings

Every threshold below is a **proposed baseline**. The linked sources motivate
concepts; they do not endorse these exact crypto parameters. Long/short symmetry
is a hypothesis to test separately. All inputs must be closed, real candles;
orders use executable bid/ask. Keep existing account/risk limits.

## 1. Trend pullback

Fidelity describes using EMA direction and pullbacks as a trend-following
approach, while noting its lag and sensitivity. That supports the concept,
not a specific profitable cryptocurrency setting.
[EMA guide](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/ema).

Proposed benchmark: EMA20 above EMA50 on closed 5m bars and EMA50 rising over
three bars. On 1m or 3m, wait for 2?5 counter-direction candles, then a bullish
close above the preceding candle's high. Enter at the next executable price.
Place the stop below the pullback low with a small ATR buffer; reject if the
nearest pre-existing resistance leaves less than the required net reward.
Compare a fixed 1.5R exit with the existing bot's frozen bracket, as separate
logged trials. Do not add RSI, MACD and stochastic simultaneously by default.

Failure hypotheses: sideways chop, trend exhaustion, chasing and fees consuming
small moves. Compare ordinary candles with the current HA signal rather than
assuming smoothing helps. Retain real OHLC fills for HA comparisons.
[TradingView HA execution](https://www.tradingview.com/support/solutions/43000481029-strategy-produces-unrealistic-results-on-non-standard-chart-types-heikin-ashi-renko-etc/).

## 2. Rolling breakout and retest

Use the existing 24/7 500 Backtest rules as the control. A challenger can use
a frozen 20-bar 3m/5m range and a close outside it, then its first confirmed
retest within three bars. Exclude the breakout candle from range construction.
Enter only after the retest closes back beyond the edge. A close through the
far side invalidates the setup. Preserve the current risk floor and bracket
checks; log any adjustment to the structural stop.

Optional separate experiment: require pre-breakout Bollinger bandwidth to be
below its own prior rolling percentile, then expand. This is an original test
proposal. Bollinger describes squeeze/continuation uses, and cautions that
band tags are not standalone entry signals.
[Original indicator author's rules](https://www.bollingerbands.com/bollinger-band-rules).

Failure hypotheses: false breakouts, a range selected after seeing the move,
repeated re-entry on the same event and moving the reference after breakout.
No cash-market session restriction is proposed.

## 3. Range-only VWAP / band reversion

VWAP is a volume-weighted reference and can lag; it does not guarantee a return
to the mean. [TradingView VWAP documentation](https://www.tradingview.com/support/solutions/43000502018-volume-weighted-average-price-vwap/).

Proposed baseline: rolling 60-minute VWAP using sum(quote volume)/sum(base
volume) where reliable trade-derived fields exist. Otherwise label the
candle-typical-price approximation. Use a volume-weighted dispersion estimate;
do not silently mix it with a standard SMA Bollinger band. Require a flat
reference and low ADX regime, then a close back inside a lower band after an
outside excursion. Target the pre-entry frozen VWAP; reject if that target
cannot cover the chosen reward/risk and costs. Stop beyond the excursion
extreme with ATR allowance. Mirror for shorts. Test a predeclared timeout,
reporting it separately from stop/target outcomes.

ADX below 20 and above 25 are conventional educational ranges, not validated
crypto gates. Test a small neighborhood instead of optimizing hundreds of
thresholds. [Fidelity DMI/ADX](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/DMI).

Failure hypotheses: fading a real trend, using an evolving target to rewrite
past decisions, idealized maker fills and a gross edge smaller than friction.

## 4. Sweep and reclaim

Use Legend as the control: a pivot known before entry, penetration beyond it,
a genuine wick/reclaim and a pre-existing structural target. Compare with a
simple swing-sweep baseline without FVG. An FVG is a price pattern, not direct
proof that institutional unfilled orders are present.

Proposed simplification: confirmed two-left/two-right swing, first sweep in
its direction, reclaim at close, volume above a preceding baseline, and target
the most recent eligible opposing pivot. Use real candle extremes and freeze
ATR at signal time. Reject previously consumed references and targets that are
already behind the executable entry.

Osler finds stop-order cascades in FX. That mechanism can support continuation,
not merely reversal; it does not validate buying every swept low or a crypto
FVG strategy. [New York Fed paper](https://www.newyorkfed.org/research/staff_reports/sr150.html).

## 5. Order-flow confirmation ? advanced research only

For a future event-driven implementation, measure bid/ask depth imbalance and
aggressive buy/sell flow across consecutive synchronized updates, then evaluate
whether it improves a candle signal's net results. Do not buy because a single
snapshot shows a large bid wall.

Bitcoin cross-venue microstructure research evaluates subsecond prediction and
fee-sensitive execution. It is not a ready-made 1m strategy.
[Albers et al.](https://www.stats.ox.ac.uk/~cucuring/fragmentation_bitcoin_markets_arXiv.pdf).

A usable implementation needs historical book events, sequence validation,
quote-age limits, latency measurement, queue/partial-fill simulation and
resynchronization on gaps. A candle backtest cannot validate these fills.
[Binance book reconstruction](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly).

## How to compare candidates

Freeze the existing strategies as controls, vary one mechanism at a time,
record every attempted configuration and rank by held-out net expectancy,
drawdown and execution stability. Reject a more complex candidate if it does
not improve on a simpler baseline after costs. This is a research decision
rule, not a claim that any of these playbooks already passes it.
