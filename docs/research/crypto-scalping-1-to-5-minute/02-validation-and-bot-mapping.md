# Validation and mapping to the existing bots

This is a proposed experiment plan. No strategy was promoted or deployed by
the research. Keep 24/7 eligibility; analyze time-of-week results without adding
fixed trading sessions by default.

## Current code versus research candidates

| Existing implementation | Suggested comparison | Important limitation |
|---|---|---|
| `the-minute-black` | Simple ordinary-candle EMA pullback; original HA version as control | Current source-specific candle-size rule is not the same as exchange-volume confirmation |
| `500-backtest-v1` | Existing rolling hourly-bias/range/retest versus simpler channel breakout/retest | The crypto adaptation deliberately differs from the video's fixed session |
| `1-minute-legend-v1` | Sweep/reclaim with and without FVG qualifier | Discretionary demand and break-even guidance are not fully replicated |
| `vwap-band-reversion` | True VWAP-target reversion with rejection confirmation | Current code returns a fixed 2R bracket; its name/description should not be mistaken for an actual VWAP exit |

Local inspection also found legacy VWAP comments claiming maker pricing by
construction and specific performance effects from thresholds. Those comments
are not research evidence. The shared entry path uses market entries; do not
budget maker fees from a strategy comment. A future implementation task should
reconcile this documentation with actual execution before expanding that bot.

## Dataset and timeframe controls

Use Binance USD-M historical data for a USD-M strategy, including delisted or
failed symbols where available to reduce survivorship bias. Start with a small
liquid universe and expand only after checking depth and actual order size.
Keep download dates, checksums, symbol metadata, dataset revisions and the
applicable fee schedule. The exchange provides archived public candles/trades
and checksums; spot timestamp conventions differ from futures examples.
[Binance archive documentation](https://github.com/binance/binance-public-data).

Build 1m/2m/3m/4m/5m from identical source bars. An n-minute bucket starts at
`floor(open_time / (n*60000)) * n*60000` after normalizing milliseconds. Require
exactly n consecutive minute bars and emit only after completion. Sum volume,
use the first open, last close, maximum high and minimum low. Record warm-up
separately. This is a reproducibility specification.

Changing timeframe while retaining EMA20 changes the elapsed lookback. Run two
predeclared comparisons: equal bar counts and approximately equal elapsed
lookback. Count both as trials, rather than disguising the winner as a single
experiment. Keep chart interval separate from trade holding time.

## Causal and economic execution

- Compute signals only with information available at decision time; confirm
  pivots before using them and freeze range/stop/target references.
- Execute after signal completion. Model actual spread, side-specific fills,
  fees, slippage, funding events and rejected/partial orders.
- Use real OHLC for HA fills. If stop and target occur within the same bar,
  use finer data or conservative ordering, never automatic target-first.
- For passive entries, model queue/latency and non-fills. Touching a limit is
  not guaranteed execution, and an immediately crossing limit can be taker.
- Do not add spread twice if bid/ask fills already include it. Keep commissions,
  spread, impact and funding separately visible in reports.

Official references: [TradingView broker emulator](https://www.tradingview.com/support/solutions/43000786181-broker-emulator/),
[Binance fee mechanics](https://www.binance.com/en/support/faq/detail/98488a516eb84e3eb34605683dffd554),
[actual commission endpoint](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account#user-commission-rate).

## Selection protocol

Proposed procedure: chronological development, rolling validation and a final
untouched holdout spanning different market conditions. Record all attempted
strategies, intervals, parameter sets and rejected variants. Account for
positions crossing split boundaries and overlapping outcomes; never train
on labels whose future extends into a test period. Multiple trials can make
a winning backtest misleading even with a nominal holdout.
[Bailey et al.](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).

Report net expectancy per trade, total return, closed and mark-to-market
drawdown, profit factor, exposure, trade count, fees as a fraction of gross
profits, worst losing sequence and results by symbol/regime. Include a
block-bootstrap uncertainty estimate rather than treating correlated trades
as independent observations. Include no-trade and simpler-strategy controls.

A proposed project gate is positive held-out net expectancy, acceptable
predeclared drawdown, and survival under higher fee/slippage/latency scenarios.
The earlier project target of 500+ trades and profit factor above 1.3 may serve
as an administrative threshold, but neither number proves statistical validity.
A strategy failing real-cost assumptions is rejected, not rescued by higher
leverage. Leverage does not repair a negative per-notional edge.

## What is and is not ready

The existing replay is useful for causal candle-pattern comparisons. It does
not simulate historical order-book queue position, funding, all exchange
filters or full live execution. Testnet is an operational check, not proof of
production fill quality or profitability. A performance claim requires a
separate dataset-backed experiment and forward observation.
