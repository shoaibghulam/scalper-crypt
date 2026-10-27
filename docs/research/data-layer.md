# Phase 1: validated research data

This is the first increment of the expanded research system. It is separate from
Alpha Lab's existing downloader and does not change any strategy or execution
module. There is no new dashboard control yet. Phase 2 will consume this contract.

## Run from the project root in PowerShell

Download one day of BTC USDT-M perpetual history, aggregate to five-minute candles,
and request historical funding events:

```powershell
.\backend\.venv\Scripts\python.exe -m scalper.research.data --symbol BTCUSDT --timeframe 5m --start 2026-09-18 --end 2026-09-19 --funding
```

Replace BTCUSDT with ETHUSDT or XRPUSDT. Supported timeframes are `1m`, `5m`,
`15m`, and `1h`; default is `5m`. All input history is downloaded as 1m bars so
later detailed execution checks can share the same underlying time basis.
`BTC/USDT` is accepted and normalized. Dates are UTC; end is exclusive. ISO
timestamps with explicit offsets are converted to UTC. Both boundaries must align
to the chosen timeframe. Choose a range of at most 366 days that has already ended.

Files are written to `var/research-datasets/<sha256>.json`. Reusing identical
content returns the same filename. Load this cached snapshot without network:

```powershell
.\backend\.venv\Scripts\python.exe -m scalper.research.data --inspect "var/research-datasets/<sha256>.json"
```

Import a local file instead:

```powershell
.\backend\.venv\Scripts\python.exe -m scalper.research.data --csv candles.csv --symbol ETHUSDT --timeframe 15m --start 2026-09-18 --end 2026-09-19
```

CSV must contain consecutive **1m** rows covering exactly the requested interval.
Required columns: `timestamp,open,high,low,close,volume`. Timestamp is the candle
open time in UTC epoch **milliseconds**. Optional columns: `quote_volume`,
`trade_count`, `taker_buy_volume`. Missing optional values remain null. Zero volume
is valid. CSV funding import is not yet supported; `--csv --funding` is rejected.

## Code responsibilities

- `models.py`: immutable, versioned request/bar/funding/dataset contracts; rejects
  non-finite prices, invalid OHLC ranges, negative volume, and inconsistent windows.
- `loader.py`: paginated public Binance downloads, strict CSV import, and complete
  higher-timeframe aggregation. Uses the existing public client and pacing gate.
- `store.py`: atomic content-addressed snapshots; verifies checksum and schema on
  every load. This phase caches completed snapshots, not partial download pages.
- `__main__.py`: read-only CLI, progress and human-readable errors.

Each candle records `timestamp` and `available_at`, the exclusive end of its
interval. A 10:00 five-minute candle becomes available at 10:05. Later features
must join higher candles on `available_at <= decision_time`, never on opening time.
Phase 1 contains no indicators and cannot itself guarantee later strategies are
causal. Tests verify that changing later input bars cannot alter earlier aggregates.

## Assumptions and limits

- Source is public production Binance USDT-M data, not testnet prices. No API keys
  or account/order endpoints are used. Currently inactive/delisted symbols are
  rejected; this is not a survivorship-bias-free historical market universe.
- Missing/duplicate/out-of-order candles, partial higher-timeframe groups and
  unfinished candles are errors. Nothing is silently filled, sorted or dropped.
- Funding is a separate event stream with payment timestamp, rate and optional
  associated mark price. No eight-hour schedule is assumed. `queried` means the
  API query succeeded over the requested window, not independently proven exchange
  completeness. An empty response does not mean funding costs are zero.
- Settled funding is not a future prediction. Do not forward-fill a future funding
  event into earlier features. Its later accounting belongs in Phase 3.
- The source hash covers normalized 1m inputs and funding records. The dataset
  hash additionally covers the request, aggregation output and schema version.
  Snapshots have no volatile creation timestamp, so identical inputs are reproducible.
- Download pages are processed sequentially. Rate-limit/network failures stop
  the import; incomplete datasets are never saved as complete. Funding requests
  have extra pacing. Separate processes still share the exchange's IP allowance.
- CSV imports use local system time to reject future ranges; downloads use
  exchange server time. The CLI does not compute profitability or activate trading.

## Validation

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend/tests/unit/test_research_data.py
```

Tests cover all four timeframes, causal aggregation, gaps/duplicates, invalid
prices and volumes, UTC alignment, HTTP pagination, unfinished candles, funding
separation, CSV import, reproducible persistence and corrupt snapshot detection.

API reference: [Binance public futures market data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data).

Next increment: structured strategy definitions with bounded typed conditions,
explicit risk parameters and tests. No generated strategy will be approved for
paper or live execution by this data-loading phase.
