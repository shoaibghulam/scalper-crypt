# Alpha Lab: GPU neural formula search and genetic baseline

Implemented September 20, 2026. This is an original bounded research implementation,
not a port or installation of AlphaGen, AlphaTransform, or gplearn. No paid API is used.

## Why compare methods

- [AlphaGen](https://github.com/ICT-FinD-Lab/alphagen) generates formulaic stock factors
  with RL and includes GP baselines. Its built-in stock/Qlib pipeline and IC rewards
  are not Binance execution simulations. It allows custom calculation adapters.
- [AlphaTransform](https://github.com/kleonang/AlphaTransform) uses a Transformer policy
  and exposes IC, rank IC, and Sharpe training objectives on its provided stock dataset.
- [gplearn](https://github.com/trevorstephens/gplearn) provides CPU symbolic genetic
  programming. It is not a GPU trading engine.

These projects motivate symbolic discovery, not a claim of profitable 1m trading.
Rather than copy a stock objective, this implementation scores the application's
actual candle replay after transaction costs. No third-party repository code is vendored.

## Use

Select **Search multiple coins for passing candidates** to choose up to 1,000 coins. **Select all coins** selects the full available list;
**Select search results** adds matching coins without clearing earlier selections.
Coins run sequentially with the same settings and a frozen end date. Completed
coin reports are saved immediately; a coin data failure is listed and the search
continues. Cancellation preserves completed coins. The default **Show passing
candidates only** filter hides rejected candidates from the view, but keeps all
results in the downloaded report. It may show zero passes. A batch is a set of
independent experiments with the configured capital per coin, not a portfolio.
Searching more coins increases selection bias; confirm survivors on fresh data.

Open **Alpha Lab**, select a USDT perpetual, dates, budgets, and either comparison,
neural RL, or GP. GPU neural training uses CUDA; historical replay is CPU-bound.
The detected development machine is an RTX 4050 Laptop GPU with 6 GB VRAM.
Compare applies the same generation/population budget to each method (not equal
wall-clock time); repeated candidates reuse cached training results. Reports show
unique formula counts. Runs are bounded to 7–90 days, 8–64 candidates, 2–30 generations.

PyTorch is optional so ordinary trading and GP do not import it. Install CUDA support
inside the backend virtual environment using the official PyTorch wheel index:

```powershell
.\backend\.venv\Scripts\python.exe -m pip install torch==2.7.1 --index-url https://download.pytorch.org/whl/cu128
```

See [official installation instructions](https://pytorch.org/get-started/locally/).
The extra `alpha` declares the version; a CPU wheel cannot activate CUDA training.
No separate CUDA toolkit or LLM model is required for the wheel installation.

## Search and execution rules

The grammar is a depth-two binary expression: `op(op(a,b),op(c,d))`. Operators are
add/subtract/multiply/minimum/maximum, each clipped to [-3,3]. Leaves are causal
normalized return, EMA9/20 and20/50 spreads, rolling50 VWAP distance, RSI14, MACD12/26/9
histogram, Bollinger20/2 position, StochRSI14 smoothed3, relative volume20,
ATR14/price, signed ADX14, completed5m/15m/30m/1h EMA20/50 spreads, and constants0/1.
ATR, RSI and ADX use Wilder updates; EMAs use recursive first-value seeds. The
3,600-minute warmup removes initial transients but is not a claim of equivalence
with each existing fixed strategy's indicator initialization. Volume averages
include the current closed candle. This StochRSI feature is K, not a K/D crossover.
No exchange session restriction applies; VWAP rolls rather than resetting daily.

RL is an autoregressive GRU (32-dimensional token embedding, 64 hidden units),
trained by REINFORCE with batch-normalized reward baseline, entropy bonus0.01,
Adam0.003 and gradient norm clipping1. It is **not PPO**, not AlphaGen's trained
model, and not a Transformer. GP uses elites, per-token crossover, and25% token
mutation. Fixed tree depth limits complexity. Both also choose threshold0.5/1/1.5.

Crossing above the positive threshold signals long; crossing below the negative
threshold signals short. Entry is at the next1m open with adverse slippage/spread.
Each position risks1% of current simulated capital subject to existing margin,
fee and symbol-size constraints. Stop distance is max(1.5ATR,0.45% of entry),
target2R, max hold60minutes. The existing replay's `session_end` trade reason
denotes that elapsed-time exit here, not New York or another session clock.
Stops win same-bar ambiguity; independent periods begin flat and reset capital.

## Evaluation protocol and interpretation

Chronological60/20/20 split after warmup;60-minute gaps before validation and holdout.
Training reward is return percentage minus closed-equity drawdown percentage,
bounded[-100,100]. Too few trades score below-100. The top five distinct training
formulas per method enter validation. Validation freezes one finalist per method
before **either** method sees holdout. Holdout and double-spread/slippage holdout
are reported without feeding back into training or selecting an automatic winner.

Passing requires positive net and the user-selected minimum trades in all four
reported periods, including stressed holdout. This is an initial screen, not a
statistical significance test, probability of future profit, or trading approval.
Searching many formulas overfits; repeatedly inspecting the same holdout consumes
its independence. Confirm on fresh dates, multiple seeds, other coins and forward
paper/testnet data. No formula is automatically registered in the engine or Favorites.

Funding, latency, order-book liquidity, liquidation and intratrade equity drawdown
are not simulated. The shown drawdown is realized/closed-equity drawdown. Current
exchange size filters can differ from historical ones. These limitations are
included in each report rather than hidden behind a profit label.

## Persistence and isolation

Jobs and downloadable JSON reports live in `var/alpha-lab/`; public candle cache
is shared with existing research. Reports retain formula tokens, request, random
seed, exact data windows, dataset hash, model hash, GPU metadata, training progress,
and full holdout trades. Neural weights are not saved; exported tokens reproduce
the selected formula, while another neural training run may differ across CUDA
versions/hardware. Restarted unfinished jobs show Interrupted; cancellation stops
at candidate or candle checks. No checkpoint-resume feature is claimed.

The API serializes research with the existing research lock. Generated formulas
use a restricted token grammar and never `eval`, executable source, external
instructions, account keys, engine commands, or order endpoints.
