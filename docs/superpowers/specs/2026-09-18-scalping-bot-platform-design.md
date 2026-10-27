# The Scalper Crypt — Design Spec

**Date:** 2026-09-18
**Status:** Approved — development started
**Scope:** Phase 0–8 of a self-hosted crypto scalping bot platform for Binance USDⓈ-M Futures.

---

## 1. What we are building

A self-hosted platform that scans Binance USDⓈ-M perpetuals for setups matching a
configured strategy, presents ranked candidates to the operator, and — once the
operator selects coins and sets size and leverage — opens and manages those trades
to completion with exchange-side protective orders.

The operator watches a live dashboard showing every bot, every open position, how
close each position is to its take-profit or stop-loss, the amount committed, the
leverage in use, and realised/unrealised profit.

### 1.1 Confirmed decisions

| Area | Decision |
|---|---|
| Execution model | Scanner ranks candidates → operator selects → bot manages the trade to close |
| Exits | Exchange-side algo orders, placed on Binance at entry-fill time |
| Strategy (first) | VWAP sigma-band mean reversion, gated to a ranging regime |
| Environments | Three: TESTNET (API correctness), PAPER (live data, simulated fills), LIVE |
| Deployment | Local Windows host, no auth in Phase 0–6, bound to localhost |
| Backtesting | Phase 6 — strategy engine runs on historical or live candles from day one |
| Risk defaults | 0.5% risk/trade, leverage cap 10x, max 3 concurrent positions |
| Bot model | One bot = one strategy config, holding multiple positions across multiple symbols |
| Sequencing | Engine + dashboard end-to-end on testnet early; validation in parallel in paper mode |

### 1.2 Explicitly out of scope

- Authentication and remote access (Phase 7; binds to 127.0.0.1 until then)
- Multi-account / sub-account support
- Order-book imbalance and depth-stream strategies
- A no-code strategy builder
- Mobile layout beyond "does not break"

---

## 2. Constraints discovered in research

### 2.1 Conditional orders moved to a separate endpoint (2025-12-09)

STOP_MARKET, TAKE_PROFIT_MARKET, STOP, TAKE_PROFIT and TRAILING_STOP_MARKET no longer
work on `POST /fapi/v1/order`. They return `-4120 STOP_ORDER_SWITCH_ALGO` and must be
sent to `POST /fapi/v1/algoOrder`.

| Old | New |
|---|---|
| `stopPrice` | `triggerPrice` |
| `activationPrice` | `activatePrice` |
| `newClientOrderId` | `clientAlgoId` |
| response `orderId` | response `algoId` |
| — | `algoType: "CONDITIONAL"` (mandatory) |

Two traps:

1. `exchangeInfo.orderTypes` still advertises STOP_MARKET for BTCUSDT. The metadata is
   wrong. Routing uses our own table, never the advertised types.
2. Algo orders occupy a separate namespace. `GET /fapi/v1/openOrders` does not list them
   and `DELETE /fapi/v1/allOpenOrders` does not cancel them. Every cancel-everything path
   must hit both namespaces.

### 2.2 Market-data WebSocket URLs are routed, and legacy URLs fail silently

Verified by live probe 2026-09-18:

| URL | Result |
|---|---|
| `fstream.binance.com/market/ws/btcusdt@kline_1m` | data flows |
| `fstream.binance.com/ws/btcusdt@kline_1m` (legacy) | connects, then permanent silence |
| `fstream.binance.com/ws/btcusdt@bookTicker` (legacy) | works (defaults to /public) |

| Stream | Endpoint |
|---|---|
| `@kline_*`, `@aggTrade`, `@markPrice`, `!markPrice@arr`, `@ticker`, `!ticker@arr` | `/market` |
| `@bookTicker`, `!bookTicker`, `@depth*` | `/public` |
| user data (listenKey) | `/private/ws/<listenKey>` |

Mixing categories on one combined connection silently drops the wrong-category streams,
so a minimum of four connections is required.

Testnet does not yet enforce routing — it serves klines on both `/ws/` and `/market/ws/`.
A bot can pass every testnet run on legacy URLs and get a dead kline feed the moment it
points at live. We use routed URLs in every environment from the first commit.

### 2.3 Fees decide profitability before any indicator does

Binance USDⓈ-M VIP 0: maker 0.0200%, taker 0.0500%. With BNB discount: maker 0.0180%,
taker 0.0450%. Assume VIP 0 permanently.

| Execution style | Round-trip cost |
|---|---|
| Taker in / taker out | 0.090% |
| Maker in / taker out | 0.063% |
| Maker in / maker out | 0.036% |
| Realistic mixed (maker entry, maker TP, taker stop, 50% win) | 0.0495% |

Break-even win rate for stop distance S, reward multiple R, round-trip cost C:

```
p_breakeven = (1 + C/S) / (1 + R)
```

Only C/S matters.

| Stop | R | Gross p* | Net p* |
|---|---|---|---|
| 0.15% | 2.0 | 33.3% | 57.8% |
| 0.30% | 2.0 | 33.3% | 45.6% |
| 0.50% | 2.0 | 33.3% | 40.7% |
| 0.30% | 1.0 | 50.0% | 68.3% |

Mandatory design consequences:

1. Hard floor on stop distance: 0.45% of price. Enforced by the scanner ATR filter and
   re-checked at order time. Target C/S <= 0.25.
2. Stops are sized from 5m/15m ATR, never 1m ATR. Typical 1m ATR(14) is 0.03–0.08% on
   BTC; a 1.2xATR(1m) stop gives C/S between 0.44 and 1.1 — structurally unprofitable.
   1m is used for timing only.
3. Never 1:1 R:R. A symmetric 0.3% scalp requires a 68.3% net win rate.
4. Post-only (GTX) entries and maker take-profits are the primary cost lever, worth
   roughly 45% of round-trip cost.
5. Break-even stop moves must be fee-aware: newSL = entry +/- 2 * roundTripFee * entry.

### 2.4 Testnet cannot validate a strategy

Measured simultaneously across environments 2026-09-18:

| | Live | Testnet |
|---|---|---|
| USDT perpetuals trading | 528 | 528 |
| BTCUSDT spread | 0.01 bps | 2.65 bps (~265x) |
| DOGEUSDT top-20 bid depth | 10.5M | 2,597M (~247x) |

Spreads far too wide and depth far too deep, in opposite directions. Wiped roughly
monthly with no notice.

Consequence: three environments. TESTNET proves API correctness. PAPER subscribes to live
streams and simulates fills against the real bookTicker, and is the only mode that can
honestly measure the edge. LIVE comes after PAPER clears the Phase 6 gate.

### 2.5 Frequency is the enemy

A cost-aware study of 78 mean-reversion backtests on tick-level data found 14 of 78
configurations won >=65% of trades and still lost money; one returned -12.5% at a 68% win
rate. Average return by timeframe degraded monotonically: -4.0% (1d), -9.5% (4h), -8.1%
(1h), -14.4% (15m). Regime swung outcomes by 57 percentage points.

Consequence: the regime gate is load-bearing. Every strategy ships with its gate or it
does not ship. The backtester charges maker and taker separately, because the mix changes
with win rate and a blended rate hides the real cost.

---

## 3. Architecture

### 3.1 Process topology

```
                    +-------------- Browser ----------------+
                    | Vite/React SPA (static bundle)        |
                    | TanStack Query - Zustand - tick store |
                    +---+------------------------+----------+
                  REST  |                        | WS (1 Hz coalesced,
               commands |                        |     seq-numbered)
 =======================+========================+==================
  localhost             |                        |
 +----------------------v------------------------v--------------+
 | PROCESS: api   (uvicorn - worker count irrelevant by design) |
 |  - FastAPI REST + StaticFiles(SPA build)                     |
 |  - /ws -> fans out engine state to browsers                  |
 |  - Opens SQLite READ-ONLY. Contains zero trading logic.      |
 |  - Commands: write to DB (durable) + push over engine socket |
 +-------------+-------------------------------+----------------+
               | subscribe (localhost WS)      | commands
 +-------------v-------------------------------v----------------+
 | PROCESS: engine   (ONE instance)   <- SOLE DATABASE WRITER   |
 |   supervisor                                                 |
 |     +- reconciler       (boot + every 60s + on WS reconnect) |
 |     +- market data      (routed streams, staleness watchdog) |
 |     +- user data stream (listenKey, keepalive < 60m)         |
 |     +- rate-limit governor   (one budget for the account)    |
 |     +- risk governor         (heat, daily loss, breakers)    |
 |     +- orphan sweeper        (both order namespaces, 30-60s) |
 |     +- publisher             (coalesce -> 1 Hz snapshot)     |
 |     +- bot tasks 1..N        (signal -> risk -> order)       |
 |   ProcessPoolExecutor <- CPU-bound indicator passes          |
 +-------------+-------------------------------+----------------+
               | WAL writes                    | REST + routed WS
 +-------------v------------+   +--------------v----------------+
 | SQLite (WAL, sync=FULL)  |   | Binance USDT-M Futures        |
 | per-environment file     |   | exchange-side algo SL/TP      |
 +--------------------------+   +-------------------------------+
```

### 3.2 Why the engine is a separate process

Uvicorn and Gunicorn workers are separate OS processes, each executing startup
independently. With `--workers 4` the engine would run four times: four times the
intended position size, opened simultaneously, each instance passing its own risk check
because limits are per-process. It bites even at `--workers 1`, because `--reload` forks
a child and Gunicorn respawns timed-out workers.

Three independent guards:

1. Structural. The engine is not in the API process, so worker count is irrelevant.
2. Deterministic clientOrderId: `{bot_id}-{signal_seq}-{intent}`, never random. Binance
   rejects duplicate non-blank client order IDs, so the venue itself is the last line of
   defence. On an ambiguous failure we query by clientOrderId; we never blind-retry.
3. Single-instance lease: a lock file renewed periodically. If the engine cannot take it,
   it exits loudly.

### 3.3 Transport between api and engine

The reference architecture used Redis. We deviate: this host has no Docker, and Redis on
Windows means Memurai or WSL. Instead the engine runs a WebSocket server bound to
127.0.0.1; the API subscribes as a client and fans out to browsers. Commands travel the
same socket, with a `commands` table in SQLite as the durable fallback that the engine
drains on reconnect.

This preserves the property that matters, a process boundary, so restarting the API
cannot touch a live position, without adding a dependency.

### 3.4 Database

SQLite, WAL mode, one file per environment.

```
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = FULL;
PRAGMA busy_timeout = 5000;
```

The engine is the sole writer; the API opens read-only. Access through SQLAlchemy so a
future move to Postgres is a URL change. Backups use `sqlite3 .backup` or `VACUUM INTO`,
never `cp`, because every file has a matching `-wal` and `-shm`.

Candles are not stored in this database. Each bot keeps a rolling deque of the last ~500
bars per symbol and timeframe, seeded from REST and maintained from the kline stream.
Historical data for backtesting goes to Parquet files read with DuckDB.

### 3.5 Schema

| Table | Purpose |
|---|---|
| `orders` | PK is `client_order_id`; also `exchange_order_id`, `algo_id`, `status`, `intent`, `submitted_at`, `raw_response` |
| `fills` | UNIQUE(exchange_trade_id). The idempotency guard that stops replayed PnL |
| `positions` | One row per logical position; `bot_id`, `status`, links to entry and exit fills |
| `ledger` | Append-only; every balance delta keyed to a fill. Never UPDATE. |
| `events` | Append-only audit of every command: who, when, what, from where |
| `commands` | Durable command queue drained by the engine |
| `engine_state` | `last_processed_event_time` per stream, safe-mode flag, run mode |
| `reconciliation_reports` | Adopted, closed and orphaned counts and detail per run |
| `api_keys` | Encrypted secret column, `environment` discriminator, permission snapshot |
| `bots` | Strategy config, state, allocated capital, TP and SL hit counters |

---

## 4. Components

### 4.1 ExchangeClient

Thin interface over `python-binance==1.0.37` (pinned, with `websockets<16`), with algo
endpoints called through our own signed-request helper because the library does not cover
them on all paths.

```
place_order(...)            -> LIMIT / MARKET only
place_algo_order(...)       -> every SL / TP / trailing stop
cancel_algo_order(...)
list_algo_open_orders(symbol)
cancel_all_orders(symbol)   -> sweeps BOTH namespaces
set_leverage(symbol, x)
set_margin_type(symbol, ISOLATED)
get_positions()
stream_klines(...)  stream_book(...)  stream_user_data(...)
```

Two things the library does not provide and we must add:

- Stream supervisor. `python-binance` has MAX_RECONNECTS = 5, after which the socket
  permanently dies. We wrap it in an unbounded exponential-backoff supervisor that
  rebuilds the manager on failure. Non-negotiable for 24/7 operation.
- Clock re-sync. The library syncs timestamp_offset once at startup and never refreshes,
  producing -1021 after hours of uptime. A background task re-syncs every 10-15 minutes
  using an RTT-midpoint estimate. recvWindow = 5000.

ThreadedWebsocketManager is never used; BinanceSocketManager only. max_queue_size is
raised above its default of 100, and indicators never run in the receive loop.

### 4.2 Numeric handling

Decimal end-to-end, serialised with the "f" presentation format. Plain float
stringification produces scientific notation such as 1e-08, which Binance rejects.

- quantity -> floor(qty / stepSize) * stepSize, then check >= minQty
- price -> round to tickSize (down for buy limits, up for sell limits)
- notional -> enforce max(MIN_NOTIONAL, minQty * price). Advertised MIN_NOTIONAL for
  BTCUSDT is $50 but minQty * price is about $78, and the larger one binds.

### 4.3 Market data

| Connection | Route | Streams |
|---|---|---|
| 1 | `/market` | `!ticker@arr`, whole-universe volume and change |
| 2 | `/market` | `<sym>@kline_1m` and `@kline_5m` for the scanner shortlist |
| 3 | `/public` | `<sym>@bookTicker` for symbols with open positions |
| 4 | `/private` | user data stream (listenKey) |

Sustained REST weight is approximately zero, leaving the full 2400/min budget for
trading. The 1024-stream-per-connection limit means all 528 USDT perpetuals fit on one
connection if the scanner needs them.

Rules:

- Act only on closed candles (`x == true`). Evaluating a condition on the forming bar is
  the single most common look-ahead bug.
- Never compute indicators in the receive loop. The server pings every 3 minutes and
  drops the connection after 10 minutes without a pong. Use asyncio.Queue plus a worker.
- Staleness watchdog per stream: no message for 3x the expected interval means dead,
  force reconnect. Socket open is never treated as feed alive.
- Rotate connections proactively at ~23h with overlap, ahead of the 24h forced disconnect.
- listenKey keepalive well inside the 60-minute window.

### 4.4 Scanner

Four stages, cheapest first. All thresholds are defaults and editable in the UI.

Stage 0, universe (daily, exchangeInfo): contractType PERPETUAL, status TRADING,
quoteAsset USDT, listed >= 21 days. Store tickSize, stepSize, minNotional, minQty.

Stage 1, liquidity (5 min, 2 whole-market calls):

| Filter | Threshold |
|---|---|
| 24h quote volume | >= $75M, then keep top 60 by volume |
| Open interest notional | >= $15M |
| Spread | <= 2.0 bps preferred, hard reject > 5 bps |
| Top-of-book depth | min(bidQty, askQty) * price >= 10 * plannedNotional |

Stage 2, volatility regime (5m klines, 300 bars):

| Filter | Threshold |
|---|---|
| ATR%(14, 5m) | >= 0.30% hard floor; preferred 0.35% to 1.50%; reject > 2.5% |
| ATR percentile | 40th to 90th of its own trailing 500-bar distribution |
| 24h change | deprioritise (do not reject) abs(delta 24h) > 25% |

The ATR floor is the fee filter in disguise. At 0.30%, a 1.5xATR stop is 0.45% and
C/S is about 0.24. At 0.20% it becomes 0.37 and the 2R break-even win rate jumps from
41% to 46%.

Stage 3, activity and regime classification:

| Metric | Use |
|---|---|
| RVOL_1m = vol / SMA(vol,20) | trigger gate >= 2.0 |
| RVOL_5m | regime gate >= 1.3 |
| ADX(14, 5m) | >= 25 trend, < 18 range, 18-25 no trade |
| Efficiency Ratio ER(20, 5m) | >= 0.35 trend, <= 0.20 range; disagreement with ADX means no trade |
| 15m displacement | require 0.4 <= abs(delta close)/ATR(14,15m) <= 3.0 |
| 15m HTF trend | close > EMA50 > EMA200, EMA50 > EMA50[5] |

Stage 4, derivatives and calendar:

| Filter | Rule |
|---|---|
| Funding blackout | no entries within +/-5 min of nextFundingTime; force flat at T-2m if losing |
| Crowded positioning | skip longs if lastFundingRate > +0.05%; skip shorts if < -0.05% |
| OI confirmation | price up with OI up favours continuation; price up with OI down is downweighted |
| Time of day (UTC) | full size 12:00-21:00, half size 06:00-12:00, no trade 02:00-06:00 and 21:00-23:00 |

Ranking function, to pick which symbol to trade right now:

```
score =  w1*z(RVOL_5m)
       + w2*z(ATR_percentile_centered)    # peaks near 70th pct, decays both sides
       + w3*regime_confidence             # abs(ADX - 21.5) / 10, capped at 1
       + w4*(-spread_bps)
       + w5*setup_quality                 # strategy-specific
       - w6*recent_loss_penalty           # per-symbol cooldown
```

Equal weights initially. Only w1 through w6 are ever fitted, never the indicator periods.

Free CVD: Binance kline arrays carry takerBuyBaseAssetVolume at index 9, so
delta = 2 * takerBuyBaseVolume - volume yields a real aggressor-imbalance series at zero
API cost, present in historical klines and therefore backtestable. Used as a confirmation
filter and for divergence detection, never as a standalone trigger.

### 4.5 Strategy 1: VWAP sigma-band mean reversion

Chosen because the entry is naturally a resting limit order, which earns maker pricing by
construction. That is the single largest cost lever available.

Indicators: session VWAP anchored 00:00 UTC on 1m bars, typical price (H+L+C)/3 weighted
by quote volume; sigma bands from volume-weighted variance at 1.0, 2.0 and 3.0 sigma.
RSI(7) on 1m. ATR(14), ADX(14), ER(20) on 5m.

Regime gate, all must hold:

- ADX(14, 5m) < 20
- ER(20, 5m) <= 0.20
- ATR%(14, 5m) within 0.35% to 1.50%
- 15m EMA50 slope over 5 bars within +/-0.15%

Entry, long (mirrored for short):

1. 1m close < VWAP - 2.0 sigma and RSI(7,1m) < 20
2. The next closed 1m bar closes above the prior bar close, and price is still
   < VWAP - 1.5 sigma
3. Post-only LIMIT GTX at max(VWAP - 2.0 sigma, confirmBar.close - 1 tick), cancelled
   after 3 unfilled bars

The 2.0 sigma threshold is not a tunable. Loosening to 1.5 sigma roughly triples signal
count and halves the edge.

Exits:

- SL = min(VWAP - 3.0 sigma, entry - 1.2 * ATR(14,5m)), floored at entry * (1 - 0.0045).
  Algo STOP_MARKET, workingType MARK_PRICE, priceProtect true.
- TP1 = VWAP - 1.0 sigma, close 50% (post-only limit)
- TP2 = VWAP, close 40% (post-only limit). On TP1 fill, move SL to
  entry + 2 * roundTripFee * entry.
- Runner 10% to VWAP + 1.0 sigma, stop trailed to VWAP
- Time stop: 25 x 1m bars. Flat at market if TP1 has not been hit.

Regime needed: range-bound, mid-to-high realised volatility, no 15m or 1h trend. It will
be destroyed on a trend day. The ADX and ER gate is load-bearing.

### 4.6 Order lifecycle

```
1. Signal on a CLOSED bar -> risk check -> size
2. INSERT order (status=PENDING, client_order_id=X) -> COMMIT
   intent is written BEFORE the exchange call, never after
3. POST /fapi/v1/order   LIMIT GTX, newClientOrderId=X
4. Wait for ORDER_TRADE_UPDATE with X == FILLED from the USER DATA STREAM,
   not from the REST response, which is only an acknowledgement
5. POST /fapi/v1/algoOrder twice (SL and TP), closePosition=true, no quantity
6. On ACCOUNT_UPDATE where position amount == 0:
      cancel algoOpenOrders(symbol)  AND  allOpenOrders(symbol)
      close position in ONE transaction: position + fill + ledger
```

Why closePosition=true rather than reduceOnly with an explicit quantity: if the entry
partially fills, a reduceOnly stop sized for the full intended position tries to close
more than is held and is rejected with -2022, at precisely the moment the stop was
needed. closePosition=true is quantity-agnostic and immune.

Partial fills: record the fill on `x == TRADE`; complete the order only on
`X == FILLED`. Exit size always comes from `z` (cumulative filled), never the requested
quantity.

workingType: MARK_PRICE on the stop, so a thin last-price wick cannot take out a good
trade and so our trigger matches the price liquidation uses. CONTRACT_PRICE on the
take-profit, where the actual print is what we want.

### 4.7 Position mode

One-way mode (positionSide BOTH) with ISOLATED margin, plus an application-level symbol
lock: a bot claims a symbol for the lifetime of a trade and releases it on flat.

Hedge mode is rejected deliberately. It provides exactly two slots per symbol, LONG and
SHORT, which does not isolate N bots. Two bots both wanting long BTCUSDT still collide on
one position, and both their closePosition=true stops act on it, so one bot stop closes
the other trade. It also forbids reduceOnly. Genuine isolation requires sub-accounts,
which is out of scope.

selfTradePreventionMode is set deliberately rather than left at the EXPIRE_MAKER default,
since several bots on one account can otherwise expire each other orders.

Leverage is set before every entry, never on an open position, because changing it
recalculates the liquidation price immediately and can move liq into the market. Margin
type is set only when flat; -4046 (no need to change) is treated as success.

---

## 5. Risk management

### 5.1 Position sizing

```
riskUSDT     = equity * riskPct                  # default 0.005
stopDistance = abs(entryPrice - stopPrice)
qty          = riskUSDT / stopDistance
qty          = floor(qty / stepSize) * stepSize
notional     = qty * entryPrice
margin       = notional / leverage
```

Reject if notional < max(MIN_NOTIONAL, minQty * price) or
margin > availableBalance * 0.8.

Leverage is not a risk knob. Risking 0.5% at 10x is identical to 0.5% at 1x when the stop
distance is the same; leverage only changes the collateral held.

### 5.2 Leverage cap

Isolated liquidation distance is approximately (1/L) - MMR. Require liquidation distance
>= 4x stop distance:

```
L_max = 1 / (4 * stopDistanceFraction + MMR)
```

The formula permits far more than we allow. At a 0.45% stop it returns 47x. Hard cap 10x.
Fat tails, exchange wicks and mark-versus-last divergence are the reason, not the margin
maths. Maximum leverage also falls as notional rises through the bracket tiers.

### 5.3 Circuit breakers

| Rule | Value |
|---|---|
| Risk per trade | 0.5% (0.25% while validating) |
| Max concurrent positions | 3 (hard cap 5) |
| Max portfolio heat | 1.5% of equity at risk simultaneously |
| Max positions per symbol | 1 |
| Correlation cap | max 2 same-direction positions across correlated majors |
| Daily loss limit | -2.0%, flat everything, halt until 00:00 UTC |
| Consecutive stops | 4 stop-losses in 24 bars, halt 4 bars |
| Equity drawdown | 10% over trailing 48 bars (>=20 trades), halt 12 bars |
| Per-symbol cooldown | 2 bars after any exit |
| Per-symbol underperformance | < 2% profit over 6 bars / 2 trades, lock 60 min |

All enforced in the engine risk governor, which sits between signal and order and cannot
be bypassed by a bot task.

---

## 6. Failure handling

### 6.1 Startup reconciliation

The engine starts with trading DISABLED and fails closed. No bot may open a position
until reconciliation returns clean.

```
1. PULL TRUTH (REST, not WS, because the stream only carries the future):
     GET /fapi/v3/positionRisk      open positions, entry, size, liq
     GET /fapi/v1/openOrders        resting orders
     GET /fapi/v1/algoOpenOrders    resting algo orders (separate namespace)
     GET /fapi/v1/userTrades        fills since last_processed_event_time - 5m
     GET /fapi/v2/account           balance, margin

2. LOAD DB state.

3. DIFF into four buckets:
   (a) in DB + on exchange, size matches  -> ADOPT, rebind to its bot
   (b) in DB, NOT on exchange             -> it closed while we were down.
         Find closing fills, compute realised PnL, close in DB.
         IDEMPOTENT on exchange trade_id so a second run cannot re-realise PnL.
   (c) on exchange, NOT in DB  = ORPHAN   -> do NOT auto-flatten.
         do NOT cancel its orders. Park as UNMANAGED, ensure a protective
         stop exists, alert, surface in UI, require a human decision.
         That symbol stays in safe mode.
   (d) size mismatch                      -> exchange wins. Rewrite DB. Log it.

4. ORDERS: query each pending order BY clientOrderId, because we may never have
   received an exchange orderId. Filled -> record. Resting -> adopt.
   Never existed -> mark abandoned.

5. RE-ARM PROTECTION: every adopted position MUST have a live algo stop on the
   exchange. Missing -> place it NOW, before anything else.

6. Clear safe mode, write a reconciliation report row, resume.
```

Bucket (c) is deliberately conservative. An orphan sweep that cancels a genuinely open
position stop-loss is a documented production failure: the cleanup that removes your
protection.

### 6.2 Invariants

1. Protective orders live on the exchange, not in the bot. A dead bot must never mean a
   naked position. This single choice converts a crash from a catastrophe into an
   inconvenience.
2. One transaction for a close: position, fill and ledger together, or crash recovery
   double-applies PnL.
3. Idempotency on exchange IDs: UNIQUE(exchange_trade_id) on fills,
   UNIQUE(client_order_id) on orders. Replaying the full event history is a no-op.
4. Write intent before action, never the reverse.
5. Reconcile every 60s, not only at boot, and on every stream reconnect. Drift means a
   bug and we want to know within a minute.

### 6.3 Error taxonomy

| Code | Meaning | Handling |
|---|---|---|
| -1021 | Timestamp outside recvWindow | Re-sync clock, retry once |
| -2019 | Margin insufficient | Skip the trade. Never retry immediately. |
| -2022 | ReduceOnly rejected | Probably already flat. Verify, do not blind-retry |
| -4046 | Margin type unchanged | Treat as success |
| -4047 / -4048 | Margin type change blocked | Only change when flat |
| -4120 | Conditional order on wrong endpoint | Route to /fapi/v1/algoOrder |
| -4164 | Below min notional | Reject at sizing time, before the call |
| -4028 | Invalid leverage | Check leverageBracket for the notional tier |
| 429 | Rate limited | Honour Retry-After, back off |
| 418 | IP banned (2 min to 3 days) | Kill switch plus operator alert. A multi-day ban with open positions is an unhedged-risk event. |

The /futures/data/ endpoints return no weight header and use a separate undocumented
bucket, so they get their own conservative local limiter.

### 6.4 Orphan sweeper

Every 30 to 60 seconds, diff positionRisk against algoOpenOrders. Any algo order on a
symbol with zero position is an orphan and is cancelled. This is the safety net for
missed WebSocket events and is what keeps us under the 200-orders-per-symbol cap.

---

## 7. API and streaming to the browser

REST for commands and snapshots; a single WebSocket for state.

- Every WS message carries a monotonic `seq` per channel. A gap triggers resync.
- Snapshot-then-delta: on connect and every reconnect, GET /api/state returns the full
  snapshot and the socket streams deltas from there.
- Exponential backoff with jitter, capped at 30s.
- Application-level heartbeat with a receive-side liveness check. A WebSocket can remain
  OPEN while delivering nothing; no message for 3x the expected interval means dead.
- After 3 failed attempts, fall back to 2s REST polling and show a degraded badge. Never
  go dark.
- Cadence: coalesced snapshots at 1 Hz, 4 Hz ceiling. Exchange ticks are never forwarded.
  Throttling at the transport means the UI cannot jitter even if a component is written
  naively.

TypeScript client generated from the FastAPI OpenAPI schema, so a backend field rename is
a compile error rather than a blank cell.

---

## 8. Frontend

### 8.1 Stack

Vite + React + TypeScript, built to static files and served by FastAPI via StaticFiles.
No Node in production, same-origin by construction so cookies and the WebSocket need no
CORS configuration.

Next.js is rejected: SSR, RSC, ISR, image optimisation and edge middleware are all
zero-value behind a login with no SEO, and a Next server is a second long-lived runtime
to supervise on a host whose entire purpose is keeping a trading engine alive.

| Concern | Choice |
|---|---|
| Server state | TanStack Query. Its isStale and dataUpdatedAt feed the staleness UI directly |
| Client state | Zustand: selected bot, filters, layout, palette |
| Tick state | Small external store read via useSyncExternalStore with a per-symbol selector |
| Routing | TanStack Router, typed search params, filters live in the URL |
| Candles | lightweight-charts, attributionLogo stays on (Apache-2.0 plus a NOTICE attribution obligation) |
| Other charts | Recharts for equity curve and P&L bars |
| Tables | TanStack Table (headless) + TanStack Virtual |
| Components | Tailwind + shadcn/ui, restyled. Defaults are not a design. |
| Forms | react-hook-form + Zod |

The WS to Query bridge: WS messages patch the Query cache via setQueryData; reconnect
calls invalidateQueries, which gives snapshot-then-delta recovery in one line.

### 8.2 Design tokens

Semantic CSS custom properties, never raw hex: --pnl-pos, --pnl-neg, --pnl-flat,
--state-live, --state-stale, --state-down, --danger, --env-accent. The colourblind
palette and the environment accent then become one-line token swaps.

Direction: dense numeric operations console. Dark surface, high-contrast numerals,
generous vertical rhythm with tight horizontal density, one accent, hairline rules rather
than card borders. Explicitly not glassmorphism, which destroys the contrast dense
numerics need. Typography is the design: tabular figures throughout.

### 8.3 The distance-to-TP/SL bar

The centrepiece of the position table, flattened from the TradingView position tool into
a table-row-sized element:

```
 SL        entry                        TP
 |---------+--------o-------------------|      LONG  10x
 63,180    63,520   63,742         64,180
 -0.53%      0     +0.35%          +0.69%       R:R 1.30
                    liq 62,910  (OUTSIDE SL - ok)
```

Rules that keep it honest:

1. Bar width is proportional to the real SL-to-TP price span, with the entry tick at its
   true proportional offset. Normalising entry to the centre draws a lie about R:R.
2. The marker is mark price, not last. Binance computes liquidation and unrealised PnL
   from mark, so plotting last makes the bar disagree with the exchange own maths.
3. Distance-to-TP percent and distance-to-SL percent are always rendered as numbers
   beside the bar. They are the literal answer to how close is it.
4. Side is encoded by a directional glyph and the word LONG or SHORT, never colour alone.
5. The liquidation tick is plotted whenever leverage is above 5x. When it falls inside
   the stop, the stop can never fire, because the exchange closes the position first.
   This is the highest-value single element in the app.
6. Margin ratio as a secondary micro-bar.

### 8.4 Screens

Overview, the main dashboard. Answers exactly two questions: is anything about to go
wrong right now, and how much am I up or down.

- Status strip (sticky): mode badge, engine health and loop lag, exchange feed and
  latency, updated N seconds ago, rate-limit budget, PANIC isolated top-right behind a
  divider
- Six KPI tiles, highest value upper-left: unrealised P&L (dollars large, ROE percent
  small); realised today net of fees with gross on hover; bots running, stopped and
  error; open positions; exposure as notional plus weighted average leverage; TP hits and
  SL hits today with hit rate and a 7-day sparkline
- Position table, flat across all bots, virtualised, with the TP/SL bar. Default sort is
  a stable key (opened_at), never P&L, or rows move as you click them.
- Bot fleet strip: name, strategy, state dot, open positions, today P&L and sparkline,
  TP and SL hit counters, pause and stop. Error-state bots sort first.
- Alerts panel, rendered only when non-empty: orphan positions, unprotected positions,
  daily-loss-limit approach, stale feed, rate-limit pressure.
- No candlestick chart here. Twelve canvases repainting per tick costs tens of ms of
  main-thread time per second and none is readable at that size.

Create Scalp, the scanner screen. Ranked candidates with the score breakdown, regime
classification, spread, ATR percent, computed stop distance and the resulting C/S ratio,
plus the position size the chosen risk produces. Operator ticks symbols, sets amount and
leverage, and confirms. Rejected symbols are shown with the filter that rejected them,
because the scanner must be debuggable.

Bot detail. State, params, running since, allocated capital, leverage, today and all-time
P&L, TP and SL hit counts, win rate. Tabs: Live (positions, candlestick chart with entry,
exit, TP and SL markers, strategy watchlist values), Trades (closed, with exit reason),
Config, Logs.

Position drawer, slides over without navigating away. Vertical price ladder (liq, SL,
entry, mark, TP), fill history, and live protective-order status read from the exchange:
SL armed with the actual algo order and trigger price, or NO STOP ON EXCHANGE, the single
most important warning in the application. Actions: move SL to break-even, close 50
percent, close all.

History and Analytics. Closed trades, equity curve, P&L grouped by exit reason and entry
tag, TP versus SL distribution, max drawdown, and fee total as a percentage of gross.

Exchange and Keys. Settings. System and Health, which includes engine uptime, loop
timing, a stream table with last-message age, and the last reconciliation report showing
exactly what was adopted, closed or orphaned on the most recent restart.

### 8.5 Real-time rendering rules

- font-variant-numeric: tabular-nums on every numeric cell. Without it the row shivers
  once a second as glyph widths change.
- Fixed decimals per symbol from tickSize and stepSize, right-aligned. The decimal point
  never moves. Never a global two-decimal format.
- Flash-on-change: directional background tint decaying under 500ms. Animate
  background-color and opacity only, never width, height or anything triggering layout.
- prefers-reduced-motion replaces the flash with a static arrow.
- Explicit plus or minus sign and a directional glyph on every P&L value; fixed column
  position; neutral colour for zero. Colourblind palette available as a token swap.
- Do NOT wrap continuously-updating prices in aria-live. It is unusable with a screen
  reader. Reserve aria-live polite for discrete events (TP hit, bot stopped, feed
  degraded) and assertive for emergencies.
- Skeletons, not spinners, on first load.

### 8.6 Staleness and trust

Five states, not a binary: live, degraded, reconnecting, disconnected, engine down. Two
independent indicators, because they are different emergencies: browser-to-API, where you
cannot see the truth, and engine-to-Binance, where the bot is flying blind, which is far
worse.

A green dot alone is insufficient. Ship a label such as LIVE, 12ms, updated 1s ago. Mark
a feed stale at 2x the refresh interval, and when stale, desaturate the numbers
themselves and overlay a reconnecting notice with the age of the last update. Rendering a
stale number at full contrast is exactly how an operator hits the kill switch based on a
40-second-old price.

### 8.7 Dangerous actions

| Tier | Action | Friction |
|---|---|---|
| 1 | Pause a bot, cancel one order | Direct button, no dialog, toast with undo |
| 2 | Close one position at market | Dialog summarising symbol, side, qty, mark, estimated fees, estimated realised PnL |
| 3 | Stop all bots | Dialog stating the count, for example stop 7 bots leaving 12 positions open |
| 4 | PANIC: stop all and flatten | Type-to-confirm, full list of what closes and the aggregate PnL it locks in |

Buttons are labelled with the verb, for example Close 0.42 BTC long, never OK. Cancel is
the low-effort path and is never the Enter default; Esc cancels. PANIC is always visible
on every screen, because a kill switch behind a menu is not a kill switch, spatially
isolated from benign controls, and is server-authoritative and idempotent: POST
/api/panic writes a flag the engine checks every loop iteration, so it works with the
WebSocket down and survives an engine restart. It is exempt from self-imposed rate
limiting.

### 8.8 Environment separation

Structural, not decorative:

- Separate database files per environment
- Separate API key records with an environment column; the engine hard-fails at startup
  on a mismatch
- Live requires TRADING_MODE=live set outside the application. A UI toggle can never arm
  real money, and changing environment requires a restart.
- Distinct accent colour across the entire chrome, not a dismissible banner; distinct
  favicon and document title prefix; mode word always adjacent to the balance; and the
  actual API base URL shown in Settings as ground truth

All three modes are loud. A calm live mode is as dangerous as a subtle test mode.

---

## 9. Security

Phase 0 to 6 binds to 127.0.0.1 with no auth. These rules apply from the first commit:

- API keys in an encrypted DB column (Fernet via a SQLAlchemy TypeDecorator), master key
  in a 0600 file outside the database. Disk encryption is not a substitute, because the
  realistic threat is an exfiltrated sqlite file or a backup tarball.
- No endpoint ever returns a secret. Responses carry id, label, exchange, environment,
  key_preview, permissions and ip_whitelisted only. A test asserts that no Pydantic
  response model anywhere contains the secret field.
- Logging filter scrubs key-shaped strings before they reach a file.
- The browser never talks to Binance directly.
- Binance key configuration: Reading on, Futures on, Withdrawals off and never on, IP
  whitelist on. Keys without an IP restriction expire after 30 days, so whitelisting also
  stops the bot mysteriously dying every month.
- On key save and every startup, call the API-restrictions endpoint and render the real
  permissions. Show a hard red warning if withdrawals are enabled. Do not trust the
  operator to have configured it correctly. Check.
- .env gitignored, every variable documented in .env.example, startup validation fails
  fast and loud on a missing secret.
- Preflight check for the Futures-specific trap: the Futures API cannot be used if the
  API key was created before the Futures account was opened. Detect at startup and give a
  specific error, not a generic 401.

When auth arrives in Phase 7: Argon2id password plus TOTP, short-lived access token in
memory, refresh token in an HttpOnly Secure SameSite=Strict cookie, never localStorage;
CSRF token on state-changing routes; a separate short-lived ws_token for the WebSocket
handshake rather than a JWT in the query string.

---

## 10. Testing

Tests first, 80 percent coverage floor.

Unit. Indicator correctness against known fixtures; filter rounding across every tickSize
and stepSize combination present in exchangeInfo; position sizing; the break-even and C/S
calculators; the strategy entry and exit predicates as pure functions over candle arrays.

Integration. The four reconciliation buckets, each with a test, explicitly including
orphan does not cancel the stop-loss; bracket placement and the ACCOUNT_UPDATE to cancel
both namespaces path; partial-fill handling driven by cumulative filled quantity; the
error taxonomy in section 6.3; clock-skew recovery.

Chaos. Kill the engine mid-trade and assert the reconciler adopts correctly; force a
WebSocket disconnect and assert snapshot-then-delta recovery with no duplicate PnL;
simulate a 418 and assert the kill switch fires; simulate a testnet wipe.

Backtest validation (Phase 6). Act only on closed candles; model entry at next bar open
plus slippage, never the signal bar close; when both TP and SL fall inside one bar range,
assume the loss, and if a result flips when that assumption is toggled the strategy has
no edge; charge maker and taker separately plus measured slippage plus funding when a
hold crosses settlement; reconstruct the historical tradable universe from daily
exchangeInfo snapshots, because backtesting on the current symbol list is a severe
survivorship bias on perpetuals.

Frontend. Screenshot checks for the TP/SL bar across long and short, in-profit, in-loss,
and liq-inside-stop cases; a colourblind-simulator pass before shipping.

---

## 11. Phases and gates

| Phase | Deliverable | Gate |
|---|---|---|
| 0 | Repo skeleton: api and engine as separate processes from the first commit, SQLite schema, config with routed URLs, .env.example | Restarting api does not touch engine |
| 1 | ExchangeClient, stream supervisor, clock re-sync, filter rounding, on TESTNET | Place, bracket and cancel a full round trip; every filter edge case rounds correctly |
| 2 | Reconciliation and the four-bucket diff, orphan sweeper | Automated tests for all four buckets pass, including orphan-does-not-cancel-SL; kill mid-trade recovers |
| 3 | Scanner standalone, logging ranked output to disk, no trading | Filters demonstrably select symbols that move; rejection reasons visible |
| 4 | REST and WS with seq, heartbeat and backoff; OpenAPI to TS client; dashboard with status strip, KPI tiles, position table and TP/SL bar | Numbers do not jitter; stale state visibly degrades the UI; snapshot-then-delta verified across a forced disconnect |
| 5 | Strategy 1 live on testnet, Create Scalp screen, multi-bot, bot detail, position drawer | Operator can scan, select, size, trade and watch to close |
| 6 | Backtester with pessimistic fills; PAPER mode on live data | Net PF above 1.3 over 500+ out-of-sample trades across 15+ symbols, stable when pessimistic fills are toggled. If it fails, stop. Do not add indicators. |
| 7 | Kill switch, tiered confirms, auth, environment hard-fail | PANIC works with the WS down and survives an engine restart |
| 8 | LIVE at exchange-minimum size for one week | Measured maker fill rate and realised slippage match the model |

The Phase 6 gate is a genuine stop. If the strategy does not clear it, the correct
response is to report that, not to add indicators until the backtest looks good.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| The strategy has no edge after real costs | Phase 6 gate is explicit and blocking; paper mode measures against real spreads |
| python-binance is thinly maintained (533 open issues, HEAD about 3.5 months old) | Pinned version, ExchangeClient interface so it can be swapped, our own supervisor and clock sync, algo endpoints called directly |
| Binance changes the API again | Routing and order-type decisions live in one table, not scattered; integration tests run against testnet |
| Testnet monthly wipe destroys state mid-development | Reconciliation handles it by design; it doubles as a useful chaos test |
| Overfitting during tuning | Indicator periods fixed in advance; only the 6 ranking weights are fitted; walk-forward 3:1; require a plateau not a spike; require 15+ symbols |
| Windows host sleeps and the bot stops | Exchange-side stops mean a sleeping host is an inconvenience, not a catastrophe; document the power settings to change |
| Operator confuses environments | Structural separation in section 8.8: separate DBs, separate keys, env var required for live |

---

## 13. Open questions

None blocking. Deferred deliberately:

- Notification channel (Telegram or Discord) for TP and SL hits and halts, Phase 7
- Whether to add strategies 2 to 4 (EMA21 pullback, squeeze breakout, liquidity sweep),
  decided by Phase 6 results
- Sub-accounts for genuinely concurrent opposing exposure, only if needed

---

## 14. Research sources

Three research reports produced 2026-09-18, retained in the session tool-results
directory:

- Scalping strategy research: fee mathematics, the four-strategy shortlist, scanner
  thresholds, risk formulas, backtesting pitfalls, open-source prior art
- Binance API research: live-probed base URLs, the algo-order migration, WebSocket
  routing map, testnet liquidity measurements, rate limits, Python library comparison
- Architecture and UX research: process topology, crash recovery, existing bot UIs,
  trading dashboard UX, frontend stack, key security

Primary sources are linked inline within those reports.
