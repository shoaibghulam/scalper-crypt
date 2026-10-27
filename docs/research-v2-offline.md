# Research v2 — offline backtesting

Written for: whoever maintains or extends the research tooling.

v2 exists because v1 got slow. The obvious explanation — "it downloads from the
internet on every run" — turned out to be only a small part of it, so this
document records what was actually measured and what was built in response.

## Where the time was really going

Measured on BTCUSDT, one symbol-year (525,600 one-minute candles), strategy
`ctk-1`, on the development machine:

| Stage | Cost |
| --- | --- |
| Cold download, 365 sequential REST calls at ~677 ms | **247 s** |
| Warm load from the v1 JSON day-cache | **2.6 s** |
| `assess()` inside replay | **444–800 µs per candle** |
| A full run — 3 periods × 2 cost settings = 6 passes | **~25–40 min** |

Downloading was 3–15% of a run. The rest was `assess()` rebuilding a
`StrategyContext` slice and recomputing every indicator from scratch, once per
bar, for every one of the six passes — work that is O(n × window).

Making the data offline was worth doing, but on its own it would have cut a
27-minute run to roughly 23 minutes. The speed had to come from somewhere else.

## The observation v2 is built on

The six passes differ only in capital and cost assumptions. Those change
entries, brackets, sizing and fills. **They do not change which bars signal** —
`assess()` reads candle history and nothing else.

So the signal series is computed once per (strategy, candles) and reused by
every pass, and by every later run over the same dates.

Crucially this is not a second simulator. `replay()` takes an optional
`signals` mapping; when it is supplied the function skips `assess()` and builds
the context lazily, only on bars that actually fire. Everything else — sizing,
brackets, stop/target resolution, fees, equity — runs exactly as before. v1 and
v2 therefore produce identical reports by construction rather than by
agreement, which `tests/unit/test_replay_signals.py` asserts on the whole
result dict rather than on a summary figure.

## Measured result

Same machine, same symbol, `ctk-1`:

| | v1 | v2 first run | v2 rerun |
| --- | --- | --- | --- |
| 20-day window | 33 s | 31 s | **3 s** |
| One year | ~25–40 min (projected from the per-candle rate) | ~10 min | **10 s** |

Both produced `trades=1163, net=-146.87, tp=587, sl=576` for the year, and
`trades=98, net=-6.7451` for the 20-day window — identical, not merely close.

## The pieces

```
scalper/research/fast/
  store.py     columnar month files + manifest, integrity checks
  archive.py   bulk download from data.binance.vision, checksum-verified
  signals.py   the signal cache and its invalidation key
scalper/api/research_v2.py   the job runner and /api/research/v2/* routes
```

### store.py

One file per symbol/interval/UTC month at
`var/research-v2/<SYMBOL>/<interval>/<YYYY-MM>.npy`, a numpy structured array
(`open_time` int64 plus seven float64 columns, in exchange kline order). A
month loads in ~10 ms against ~2.6 s to parse a year of JSON.

`load_range` refuses to return a short or gapped series — it raises `DataGap`
instead. This matters more than it looks: a silently truncated range does not
appear downstream as an error, it appears as a strategy that traded less.

A month is either *final* or *in progress*. The in-progress month is stored
too, but `stored_months` deliberately omits it so a later run extends it rather
than trusting it as complete.

### archive.py

Binance publishes monthly and daily kline archives at `data.binance.vision`.
Twelve monthly files replace 365 REST calls. Each is verified against the
`.zip.CHECKSUM` published beside it, the current month is assembled from daily
archives, and whatever the daily archive has not caught up with comes from
REST. The CSV carries a header row in newer archives and not in older ones;
both parse.

The archive was checked against the REST data v1 uses: **0 mismatches across
44,640 overlapping bars**.

Public market data only — no credentials are sent and nothing is ordered.

### signals.py

`var/research-v2/_signals/<key>.npz`, holding `at` (int64 open times) and
`side` (int8, ±1).

The key is a sha256 over the strategy id, symbol, interval, a digest of the
exact candle block, and a digest of every strategy, indicator and backtest
source file. That last component is the important one: a stale candle store
fails loudly, but a stale signal cache would return plausible trades from code
that no longer exists and the report would look entirely normal. Editing any
strategy changes the key.

The cached series is a **superset** — it answers for every eligible bar,
including ones a particular replay would skip because it already held a
position. Those skips depend on capital and costs; the signal does not. Caching
the subset would under-report trades on the next run with different economics.

## Two decisions worth knowing about

**The window ends at the last complete UTC day, not "now".** A window ending at
the current minute moves every sixty seconds, so two runs an hour apart score
different candles, cannot be compared, and can never hit the cache. This was
caught during testing: reruns were missing the cache and returning −8.12 then
−8.08 for what looked like the same request.

**v1 was left untouched.** Both tabs are live so the two can be run on the same
dates and compared, which is how the equivalence above was confirmed on real
data rather than only in fixtures.

## Filling the store

```bash
python -m scalper.research.fast.archive BTCUSDT ETHUSDT --days 365
```

Or just start a run from the Research v2 tab — it downloads whatever is
missing, and says up front how many months that will be.

## Limits

- 1m candles only. `INTERVAL_MS` is the single place that assumption lives.
- Symbols download one at a time on purpose. The archive is a courtesy of the
  exchange, and a run that gets itself rate limited is slower than one that
  waits its turn.
- The signal cache has no size cap. It is small — a year of `ctk-1` is a few KB
  — but nothing prunes it yet.
- Everything here is still historical simulation. Costs and order minimums are
  assumptions, not verified exchange fills.
