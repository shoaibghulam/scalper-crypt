# The Scalper Crypt

Self-hosted crypto scalping platform for Binance USDT-M perpetual futures.
Scans the market for setups matching a strategy, presents ranked candidates,
and manages selected trades to completion with protective orders held on the
exchange.

Design spec: `docs/superpowers/specs/2026-09-18-scalping-bot-platform-design.md`

## Status

The new **Strategy Research** dashboard provides evolutionary discovery,
compiled backtests with funding, walk-forward and robustness checks, optional
Random Forest filtering, strategy exports and explicit forward-paper approval.
See the [research guide](docs/research/README.md) for setup, assumptions and limits.
Generated strategies are not enabled for live execution.

The scanner, protected order execution, reconciliation, risk controls and
dashboard are implemented. Five research candidates are available alongside
the original video bots, with 24/7 crypto rules.

See the [five-strategy testnet guide](docs/research/crypto-scalping-1-to-5-minute/05-five-strategies-testnet.md)
for exact rules, limitations and verification. The event candidate uses
reconstructed depth plus aggregate trades and executes only on testnet.

Local tests and a public testnet event probe passed. Signed testnet order
lifecycle acceptance and out-of-sample profitability validation remain
outstanding. Live profitability has not been established.

## Getting it trading on testnet

Set `BINANCE_ENV=testnet` and futures testnet credentials in your local `.env`.
Leave `TRADING_MODE=live` unset. Stop any older engine before starting this build,
verify the dashboard environment and existing bot states, then select a research
strategy in Create Scalp. Start with one strategy and symbol. Verify entry,
exchange-held protection, closure, restart reconciliation and PANIC on testnet.

The shared minimum stop distance is 0.45%. Strategies use real structure and
their documented ATR timeframe. Market entries incur taker fees; fee, spread
and risk gates can reject valid chart patterns. Testnet liquidity is not
representative of live markets.

## Layout

```
backend/src/scalper/
  config.py      environments and URL routing
  core/          fee model, break-even maths, exchange filter rounding
  risk/          position sizing, leverage caps, liquidation headroom
  engine/        trading process: single writer, single instance
  exchange/      Binance client behind our own interface
  scanner/       symbol selection
  strategies/    signal generation
  api/           FastAPI dashboard and guarded engine commands
frontend/        Vite + React dashboard
```

## Running it

One command starts everything. The dashboard is at http://127.0.0.1:8000.

```bash
cd backend
./.venv/Scripts/python.exe -m scalper
```

That launches the trading engine and the web app as two separate processes
under a supervisor, restarts the engine if it dies, and stops both on Ctrl+C.

The engine deliberately stays its own process rather than living inside the web
app. Uvicorn workers are separate processes that each run startup
independently, so an engine started from inside the app would be multiplied by
the worker count: four workers means four engines, four times the intended
position size, and four risk governors each satisfied it was within limits.
Keeping them as siblings under a supervisor gives one-command startup without
that failure mode, and matches the shape a systemd unit or compose file would
use later.

Either process can still be run alone while developing:

```bash
./.venv/Scripts/python.exe -m scalper.engine   # trading only
./.venv/Scripts/python.exe -m scalper.api      # dashboard only
```

## Development

```bash
cd backend
uv venv
uv pip install -e ".[dev]"
./.venv/Scripts/python.exe -m pytest tests/ --cov=scalper
./.venv/Scripts/python.exe -m ruff check src tests
```

The dashboard is a Vite build served as static files by FastAPI, so there is no
Node runtime in production. Rebuild it after changing the frontend:

```bash
cd frontend
npm run build      # emits dist/, which the API serves
npm run dev        # hot reload on :5173, proxying /api to :8000
npx vitest run
```
