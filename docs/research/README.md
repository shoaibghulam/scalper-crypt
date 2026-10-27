# Strategy Research Lab

A separate research and forward-paper workflow alongside the existing bot.
The original Alpha Lab and execution engine remain available. This system uses
typed rules, NumPy, a Numba-compiled replay loop, evolutionary search and Optuna.
It needs no LLM, exchange credentials or paid API.

## Use the dashboard

1. Open the dashboard at http://127.0.0.1:5173 and choose **Strategy Research**.
2. Select coins (BTC, ETH and XRP are the initial selection), then a timeframe.
3. Start with **Quick check** to verify your data and workflow. For 1h the UI uses
   at least 30 days so there is enough warm-up and forward-fold history.
4. Set simulated capital and maximum leverage. Expand advanced settings to set
   costs, search size, dates and hard risk limits. Funding is included by default.
5. Click **Start research**. Downloading candles and first-time compilation take
   time. Later identical data requests reuse verified snapshots.
6. Review learning, selection and unseen-test results, rejection reasons,
   parameter stability, fee stress, regimes and equity curves. Every finalist is
   displayed. The candidate JSONL download includes every evaluated losing rule.
7. Only research passes offer **Approve and start paper session**. This explicit
   action starts a public-data, forward-only simulation with no exchange orders.
8. Review paper results in Saved experiments and paper sessions. **Stop paper
   session** requests a stop; the worker checks every 15 seconds and finalizes at
   the last observed candle close. Do not interpret this as a live market fill.

No passing strategy is promised. Re-running selection against the same holdout
does not create fresh evidence. For a new research iteration reserve new dates
and forward paper observations. A single positive holdout is not enough.

## Installation and command line

From the project root, using the existing backend environment:

```powershell
.\backend\.venv\Scripts\python.exe -m pip install -e ".\backend[research]"
.\backend\.venv\Scripts\python.exe -m scalper.api
```

Start the frontend from `frontend` with `npm run dev`. A running API must be
restarted after installing the routes; do not restart while old Alpha Lab jobs
are active. Research workers are separate processes and survive API restarts.

Run without the dashboard:

```powershell
.\backend\.venv\Scripts\python.exe -m scalper.research --config docs\research\example-experiment.json
.\backend\.venv\Scripts\python.exe -m scalper.research --replay var\quant-lab\<experiment-id>\state.json
.\backend\.venv\Scripts\python.exe -m scalper.research --benchmark var\research-datasets\<dataset-hash>.json --evaluations 1000
```

The reproduction command uses immutable datasets and checks the saved research
code fingerprint when available. Preserve the code and package versions with
published results. It is not a fresh
out-of-sample test. Benchmark timings depend on bars, feature periods and machine.

## Components and phase coverage

| Phase | Implementation | Verification |
|---|---|---|
| 1. Data | `data/`: exact windows, 1m source aggregation, funding, CSV, checksummed snapshots | Gap, OHLC, pagination, aggregation, checksum tests |
| 2. Representation | `schema.py`: typed JSON expressions, risk and operator ceilings | Invalid rules, warm-up, complexity limits |
| 3. Fast backtest | `features.py`, `backtest.py`: NumPy features and compiled CPU execution | Independent financial examples and Python/kernel equivalence |
| 4–5. Generation/evolution | `search.py`: random rules, elites, mutation, crossover, bounded cache and archives | Seed reproducibility and bounded candidates |
| 6. Optimization | Training-only seeded Optuna TPE trials | Full-pipeline deterministic selection |
| 7. Walk-forward | `validation.py`: expanding training windows, embargoes, forward folds | Split boundaries and holdout-mutation independence |
| 8. Robustness | ±5/10/20% parameter changes, 1/2/3× costs, Monte Carlo, regimes, correlation proposal | Pipeline diagnostics and rejection gates |
| 9. Optional ML | `ml.py`: bounded Random Forest trained on completed training trades only | Training cutoff and JSON prediction tests |
| 10. Integration/paper | `integration.py`, `paper.py`, `api/quant_lab.py`: export, bot-interface adapter, explicit paper approval | Rejection/live guards, forward-only session, cancellation |

`experiments.py` orchestrates the workflow and persists config, dataset hashes,
timeframe, seed, costs, periods, versions, candidates, models and results.
`QuantLab.tsx` is the independent dashboard. No existing live strategy is replaced.

## Research protocol

- Learn on the first 60%; optimize parameters only there. Keep at most 10–40 top
  training definitions for development evaluation, depending on finalist count.
- The next 20% is development validation. Three expanding walk-forward folds by
  default select from that training-generated pool using only earlier data.
  This validates a frozen strategy family; it does not re-run genetic discovery
  inside every fold. Per-finalist fold metrics remain validation evidence.
- Default embargo is 12 bars. Every period starts flat and is forcibly closed at
  its exclusive end, so trade labels do not extend across split boundaries.
- Freeze finalist ordering and the correlation-based portfolio proposal using
  validation only. Then evaluate the final 20% once per finalist, including a
  fixed triple-cost stress check. Never reorder candidates by test performance.
- A pass needs positive net results, enough trades and no safety halt in training,
  validation and holdout; at least two thirds of forward folds profitable;
  at least two thirds of parameter variations profitable; all validation cost
  stresses profitable; acceptable Monte Carlo drawdown and positive bootstrap
  lower-tail profit; and a profitable triple-cost holdout.
- When requested, the ML filter needs 40 completed training trades and both
  positive and negative outcomes. Insufficient ML evidence rejects the result.
  Its probabilities are uncalibrated estimates, not financial confidence claims.
  Training metrics describe the base rule used to train the filter; later stages
  apply the frozen filter.

These gates do not prove statistical significance after searching many hypotheses.
Monte Carlo reshuffling does not preserve serial dependence. Regime thresholds
are learned from training observations; trend labels use only completed bars.
Regime breakdown is descriptive, not a requirement to trade every regime.

## Execution assumptions and hard limits

- Signals see completed candles and execute at the next open. Indicators are
  causal with explicit NaN warm-up. NOT cannot turn missing features into entries.
- Market fills use adverse slippage plus half the full spread on each side.
  Fees apply to entry and exit notional. Fees and slippage are configurable.
- A limit is valid for one bar, requires trading through its price, receives no
  fill beyond its limit and has no same-bar profit target credit. Same-bar stops
  are charged conservatively. There is no queue-depth/partial-fill model.
- Existing stops win ambiguous stop/target bars; next-open rule/time exits execute
  before later intrabar movements unless the open has already gapped through a stop.
- Trailing levels update after candle close, never using a bar's high to create
  a stop that is retroactively hit by the same bar's low.
- Actual signed funding rates and associated mark prices determine payments.
  Long pays positive funding; short receives it. Existing positions pay before
  opening orders at settlement. Exchange timestamp jitter up to 1 second is mapped
  to that boundary; original event timestamps remain stored. Off-boundary events
  beyond that tolerance and missing mark prices are rejected, not assigned zero.
  A successful funding query is not independent proof of exchange completeness.
- VWAP is rolling, not New York-session anchored. Trading is 24/7.
- One position per strategy, 1–5× maximum configured leverage, bounded allocation,
  maximum daily marked loss, maximum drawdown and consecutive-loss halt. These
  ceilings are operator inputs and cannot be raised by evolution. A breach halts
  the rest of that replay/session. Gaps and exit costs can overshoot a threshold.
- Equity is marked at closes; safety thresholds additionally inspect adverse
  intrabar extremes. Sharpe/Sortino use bar returns with 365-day annualization and
  zero risk-free rate. Annualized return is omitted below 30 days. Profit factor
  is undefined when gross loss is zero. Gross profit/loss sum net winning/losing
  trades. Funding paid can be negative. Paper metrics include open-position costs.
- Leverage is linear notional sizing. Exchange liquidation tiers, insurance,
  borrow, latency, market impact and portfolio shared-margin liquidation are not
  simulated. This is candle research and paper simulation, not live certification.

## Existing bot and live deployment boundary

JSON export contains identity, typed rules, model, risk configuration and separate
training/validation/test/walk-forward/robustness metrics. `ResearchStrategy`
implements the bot's `Strategy.assess` and `levels` interface for inspection and
paper tooling and is explicitly `backtest_only` and `manual_only`.

**Live deployment is intentionally not implemented.** There is no live-approval
API and no automatic registration in the live strategy registry. The existing
engine does not execute this DSL's arbitrary exits/trailing rules, so enabling it
would silently change behavior. A separately reviewed execution adapter, paper
evidence, and explicit human approval are required before real-money integration.
The completed deliverable here is the research-to-paper workflow and export
contract; it is not full live execution support for generated strategies.

## Performance and operations

Population up to 10,000, generations up to 100, and up to 1,000,000 scheduled
candidate evaluations per coin are configurable. This is a workload ceiling, not
a measured million-strategy throughput claim. The replay loop is compiled; feature
arrays and duplicate candidates are cached with memory bounds. Candidates are
written incrementally to JSONL. Individual candidate runs are not GPU accelerated.
The worker isolates CPU work from the web server; coins run sequentially and an
OS lock prevents multiple research workers. A separate lock bounds paper sessions.

This version does not implement a distributed/multi-GPU search, vectorized
population-wide batching or a shared-account portfolio backtest. Correlation
selection is a proposal for independent strategies, not a portfolio P/L promise.
Large requests can still take hours. Cancel from the dashboard between evaluations.
The dashboard retains the latest 50 run entries; all artifacts remain on disk.

Files: `var/quant-lab/<id>/state.json`, `<symbol>-candidates.jsonl`, `worker.log`,
and, for paper sessions, `strategy.json`. Dataset files are content-addressed under
`var/research-datasets/`. Paper reports list all immutable source segments.
Crashes are shown as interrupted; completed coin results are retained. A failed
data download is shown per coin and never relabeled as a rejected strategy.

## Validation commands and references

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend\tests
cd frontend
npx vitest run src/components/QuantLab.test.tsx
npm run lint
npm run build
```

Design references: [Numba compilation](https://numba.readthedocs.io/en/stable/reference/jit-compilation.html),
[Random Forest classifier](https://scikit-learn.org/1.8/modules/generated/sklearn.ensemble.RandomForestClassifier.html),
and [Binance futures market data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data).
