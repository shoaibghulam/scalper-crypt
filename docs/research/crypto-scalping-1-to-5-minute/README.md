# Crypto scalping: 1?5 minute research shortlist

Research date: 18 September 2026. Scope: 24/7 liquid crypto, primarily the
existing Binance USD-M candle-based bot. No strategy code, account settings,
credentials or live orders were changed by this research.

**Conclusion:** no reviewed source establishes a universally best, trusted,
profitable 1?5 minute crypto scalping system. The strongest next step is to test
simple trend pullbacks, breakout/retests and regime-filtered mean reversion
under the same realistic execution assumptions. This ranking is **engineering
judgment about research priority**, not a measured profitability league table.

## Ranked shortlist for this project

| Priority | Strategy family | Suggested research timeframe | Why shortlist it | Evidence limit |
|---|---|---|---|---|
| 1 | Trend pullback / EMA continuation | 3m or 5m context; 1m trigger | Simple, interpretable baseline; closely related to Minute Black | One-minute Bitcoin technical-rule research exists, but it does not validate our HA rules or net live returns |
| 2 | Rolling breakout and retest / volatility expansion | 3m or 5m | Fits the existing 24/7 500 Backtest bot; entries and invalidation can be specified | Range-breakout research does not validate this exact retest implementation |
| 3 | Range-only VWAP/Bollinger mean reversion | 3m or 5m, compare 1m | Provides a contrasting hypothesis for non-trending markets | A price-band touch alone is not an entry edge; measurable reversal may be smaller than costs |
| 4 | Liquidity sweep and reclaim | 1m trigger with 5m context | Closest to Legend; clear rejection/invalidation can be tested | Stop-order mechanics do not establish profitable FVG or ?smart money? rules |
| 5 | Order-flow / book-imbalance confirmation | Event data, optionally summarized over 1m | Has genuine microstructure research support | Subsecond evidence and infrastructure are not equivalent to this REST/candle bot |

Support for the underlying concepts, not this ranking: [Fidelity EMA](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/ema),
[one-minute Bitcoin technical rules](https://doras.dcu.ie/25053/1/The_effectiveness_of_technical_trading_rules_in_cryptocurrency_markets%5B1%5D.pdf),
[Bollinger's own rules](https://www.bollingerbands.com/bollinger-band-rules),
[Osler on stop cascades](https://www.newyorkfed.org/research/staff_reports/sr150.html),
[Bitcoin microstructure study](https://www.stats.ox.ac.uk/~cucuring/fragmentation_bitcoin_markets_arXiv.pdf).

My recommendation is to compare priorities 1?3 against the current bots before
adding more indicators. For a candle-based implementation, start research at
3m/5m and retain 1m timing as a comparison. This is a practical choice to test,
not proof that slower candles have greater expectancy.

## What the research actually supports

Corbet et al. use one-minute Bitcoin observations and find predictive value
in some simple technical rules. That is relevant to a trend baseline, but the
review did not establish realistic net-cost, walk-forward profitability for
our exact strategy. The historical sample is 2014?2018, not current market
conditions. [Author-hosted paper](https://doras.dcu.ie/25053/1/The_effectiveness_of_technical_trading_rules_in_cryptocurrency_markets%5B1%5D.pdf).

Deprez and Fr?mmel evaluate many rules with costs, multiple-testing controls and
out-of-sample selection. Their frequencies are 10, 30, 60 minutes and daily:
this supports a rigorous selection process, not a claim about a winning
one-minute bot. [University full text](https://backoffice.biblio.ugent.be/download/01HY3C3S169G1N6QNYR55NZMFB/01HY60XZGZYHNQ6188MSVJT0SG).

Wen et al. find both momentum and reversal effects depending on conditions.
Their main hourly return analysis is built from five-minute observations;
**five-minute input data is not a five-minute trading horizon**.
[Publisher](https://www.sciencedirect.com/science/article/pii/S1062940822000833).

A recent, unreviewed preprint reports detectable short-horizon crypto reversal
but an edge smaller than its benchmark transaction costs. Its main horizon is
15 minutes, so it is supplementary caution, not direct validation of our
1?5 minute designs. [August 2026 preprint](https://arxiv.org/abs/2608.21888).

Five-minute crypto pairs trading has also been studied, with strong sensitivity
to costs, parameters and execution. It requires two-leg portfolio execution;
it does not validate a single-coin VWAP fade. Keep it outside this project's
first single-position shortlist. [Fil and Kristoufek](https://doi.org/10.1109/ACCESS.2020.3024619).

## 1, 2, 3, 4 or 5 minutes?

These are candidate sampling intervals, not five independently proven systems.
A one-minute signal can hold a trade longer than one minute.

| Interval | Recommended experiment, not proven advantage |
|---|---|
| 1m | Existing Minute Black and Legend controls; measure cost and latency sensitivity |
| 2m | Resampled comparison with 1m; do not assume two-minute candles remove noise profitably |
| 3m | Primary alternative for pullback and breakout research |
| 4m | Sensitivity test; seek stable behavior between 3m and 5m, not a specially optimized winner |
| 5m | Primary structure/range baseline, optionally with 1m entry confirmation |

For reproducibility, build all five resolutions from the same complete 1m
history. For 2m/4m, aggregate exact consecutive bars on fixed UTC boundaries;
never use an incomplete aggregate. This is a proposed engineering convention.
Binance's public-data documentation lists 1m, 3m and 5m among native intervals,
not 2m/4m; use the correct spot/futures dataset and timestamp units.
[Binance public data](https://github.com/binance/binance-public-data).

Your requirement remains **24/7 eligibility**: no New York open, 7/8 a.m. or
weekday restrictions. A rolling VWAP window or a UTC aggregation boundary is a
calculation convention, not an allowed-trading-hours gate.

## Why ?most trusted? is not ?highest win rate?

For this review, trust means transparent rules, correctly matched timeframe,
real costs, causal execution, reproducible evidence and an untouched test
period. An indicator's official documentation explains its calculation; it
is not an audited performance record. Repeatedly choosing the best-looking
backtest creates selection bias. [Backtest overfitting research](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).

Do not promote a strategy from a profit screenshot, influencer accuracy claim,
a martingale equity curve, synthetic HA fills, or a backtest that ignores
spread/fees. TradingView explicitly distinguishes HA signal prices from real
OHLC execution. [TradingView](https://www.tradingview.com/support/solutions/43000481029-strategy-produces-unrealistic-results-on-non-standard-chart-types-heikin-ashi-renko-etc/).

## Costs can overturn an apparently accurate signal

Binance futures commission depends on maker/taker execution, account tier and
applicable discounts. Use actual account/symbol rates, not an example copied
from documentation. Funding matters if a position spans its settlement event.
[Binance fees](https://www.binance.com/en/support/faq/detail/98488a516eb84e3eb34605683dffd554),
[commission-rate API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account#user-commission-rate).

Illustration only: 0.05% commission per leg plus 0.02% total execution friction
makes roughly 0.12% round-trip cost on notional. With symmetric 0.20% gross
wins/losses, break-even win probability becomes `(0.20+0.12)/(0.20+0.20)=80%`.
This is arithmetic under fixed-cost assumptions, not a quote of your fees.
Costs on actual entry/exit notionals, variable slippage and funding require a
more detailed simulation.

## Deliverables and scope

- [Candidate rules to test](01-candidate-playbooks.md)
- [Validation plan and mapping to existing bots](02-validation-and-bot-mapping.md)
- [Source register and evidence limitations](03-sources.md)

Confidence: high in the distinction between evidence and marketing; low in
which exact strategy/timeframe will produce positive future net returns.
No live-money result, new market-data backtest or parameter optimization was
performed in this literature review.

## First implemented candidate

[EMA Trend Pullback implementation and usage](04-ema-pullback-implementation.md)
is now available as `ema-trend-pullback`. This is a research candidate requiring
out-of-sample and testnet validation, not a verified profitable deployment.

## Five-strategy testnet build

All five baseline research choices are implemented. See the
[exact rules, verification results and testnet guide](05-five-strategies-testnet.md).
The event candidate is EMA pullback plus sequenced order-flow confirmation,
restricted to testnet; candle-only replay is not supported for it.
