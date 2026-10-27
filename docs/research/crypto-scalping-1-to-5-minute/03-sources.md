# Source register and evidence limits

Reviewed 18 September 2026. These 20 primary sources support definitions,
mechanisms, empirical caveats or implementation requirements. Inclusion is not
endorsement of an exchange, paid service or profitable strategy.

| # | Source | What it supports / what it does not |
|---|---|---|
| 1 | [Corbet et al., technical rules in cryptocurrency markets, 2019](https://doras.dcu.ie/25053/1/The_effectiveness_of_technical_trading_rules_in_cryptocurrency_markets%5B1%5D.pdf) | Direct one-minute Bitcoin technical-rule evidence, 2014?2018. Does not establish our exact rules' current, net executable returns. |
| 2 | [Deprez & Fr?mmel, 2024, university manuscript](https://backoffice.biblio.ugent.be/download/01HY3C3S169G1N6QNYR55NZMFB/01HY60XZGZYHNQ6188MSVJT0SG) | Cost-aware technical-rule selection with multiple-testing controls/OOS portfolios. Uses 10m/30m/60m/daily, not 1?5m. |
| 3 | [Wen et al., momentum, reversal or both, 2022](https://www.sciencedirect.com/science/article/pii/S1062940822000833) | Condition-dependent intraday predictability. Hourly outcomes from 5m data are not 5m scalping evidence; complete execution-cost treatment not verified here. |
| 4 | [Fil & Kristoufek, Pairs Trading in Cryptocurrency Markets, 2020](https://doi.org/10.1109/ACCESS.2020.3024619) | Includes 5m relative-value research. Two-leg costs and execution sensitivity; not single-coin VWAP performance. |
| 5 | [Kitron & Wengrowicz, August 2026](https://arxiv.org/abs/2608.21888) | Recent reversal measurement with holdout, primarily 15m and supplementary 5m. Unreviewed preprint; benchmark costs exceed reported gross edge. |
| 6 | [Albers et al., Bitcoin fragmentation/price formation, 2021](https://www.stats.ox.ac.uk/~cucuring/fragmentation_bitcoin_markets_arXiv.pdf) | 500ms prediction and execution experiments. Historical microstructure evidence, not a slow REST-bot recipe. |
| 7 | [Gould & Bonart, queue imbalance, 2015](https://arxiv.org/abs/1512.03492) | Next-tick direction in Nasdaq equities. Mechanistic reference only; wrong asset and horizon for direct crypto performance claims. |
| 8 | [Bailey et al., Probability of Backtest Overfitting, 2015](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) | Selection bias and PBO framework. Supports recording trials and stronger validation, not a particular profitable rule. |
| 9 | [Osler, New York Fed staff report 150](https://www.newyorkfed.org/research/staff_reports/sr150.html) | FX stop orders can propagate cascades. Does not prove sweep reversals or institutional FVG orders in crypto. |
| 10 | [Fidelity EMA guide](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/ema) | EMA calculation, trend/pullback interpretation and lag. Educational definition, not audited crypto results. |
| 11 | [Fidelity DMI/ADX guide](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/DMI) | Trend-strength interpretation and conventional thresholds. Threshold transfer to crypto requires testing. |
| 12 | [John Bollinger's rules](https://www.bollingerbands.com/bollinger-band-rules) | Original author's definitions, band-walking and limitations of band touches/default parameters. Not a guaranteed reversal system. |
| 13 | [TradingView VWAP documentation](https://www.tradingview.com/support/solutions/43000502018-volume-weighted-average-price-vwap/) | Calculation, anchor choices and interpretation. No exact playbook profitability claim. |
| 14 | [Charles Schwab, volume-weighted indicators](https://www.schwab.com/learn/story/how-to-use-volume-weighted-indicators-trading) | Institutional educational explanation of VWAP and intraday use, not crypto scalping validation. |
| 15 | [TradingView synthetic-chart backtest warning](https://www.tradingview.com/support/solutions/43000481029-strategy-produces-unrealistic-results-on-non-standard-chart-types-heikin-ashi-renko-etc/) | HA synthetic prices differ from executable OHLC; standard-price fills matter. |
| 16 | [TradingView broker emulator](https://www.tradingview.com/support/solutions/43000786181-broker-emulator/) | Commission, slippage, delay and conservative limit-fill modeling. Emulator settings do not guarantee live fills. |
| 17 | [Binance futures fee mechanics](https://www.binance.com/en/support/faq/detail/98488a516eb84e3eb34605683dffd554) | Maker/taker fees, notional basis, tiers and funding. Must verify applicability to actual account/product. |
| 18 | [Binance user commission rate API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account#user-commission-rate) | Account/symbol fee retrieval. Documentation examples are not the user's actual rate. |
| 19 | [Binance local order-book reconstruction](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly) | Snapshot/stream synchronization and gap recovery. Needed before event-level imbalance research. |
| 20 | [Binance public data repository](https://github.com/binance/binance-public-data) | Historical candle/trade files, timestamp conventions and integrity checks. Candles do not reconstruct queue-level fills. |

## Methodology

The deep-research skill guided parallel research into (1) direct empirical
crypto evidence, (2) transaction costs/order flow and (3) indicator definitions,
backtesting and integration with the existing bots. Firecrawl/Exa connectors
were unavailable; the available web search/open tools were used instead.

Search themes included intraday crypto momentum/reversal, one-minute technical
rules, five-minute pairs trading, transaction-cost-aware validation, VWAP/EMA/
Bollinger definitions, exchange fee mechanics, book reconstruction and backtest
overfitting. Academic papers were read through author/university copies where
available; official documentation supplied operational facts. Some publisher
access was limited; conclusions are bounded to the accessible material and no
missing cost model was assumed to exist.

Older work was retained for directly relevant methodology or primary definitions;
recentness alone was not treated as evidence quality. The 2026 reversal paper
is explicitly a preprint. No paid-course rankings, affiliate ?top strategies?
articles, unverifiable leaderboards or social-media win rates were used as
performance evidence.

The ranked shortlist is the report author's synthesis for this project's
architecture. No source establishes that our proposed parameters are optimal.
There is insufficient matched, current, net-cost evidence to crown a universal
winner among 1m/2m/3m/4m/5m strategies. The market dataset and strategy performance
must still be evaluated separately.
