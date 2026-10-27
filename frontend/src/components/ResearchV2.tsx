import { useEffect, useState } from "react";
import type { AppState } from "../lib/api";
import { FavoriteTrade } from "./FavoriteTrade";
import { favoriteId, useFavorites, type FavoritePair } from "../hooks/useFavorites";
import "./ResearchV2.css";

/**
 * Research v2 - offline backtesting.
 *
 * Built around the question the old tab could not answer: what will this run
 * actually cost me? Downloading and signal computation are the two slow steps
 * and both are cached, so the storage panel is the headline rather than a
 * footnote. A run over data already held finishes in seconds, and the results
 * table says which strategies were answered from cache - so that speed is
 * explainable rather than suspicious.
 */

type Candidate = {
  id: string;
  name: string;
  timeframe: string;
  backtest_only?: boolean;
  /** Non-empty when the strategy is a frozen single-dataset hypothesis. */
  research_symbols?: string[];
};
type Stored = {
  symbol: string;
  months: number;
  first: string;
  last: string;
  candles: number;
  megabytes: number;
};
type ActiveJob = {
  id: string;
  kind: string;
  phase: string;
  progress: number;
  symbol: string;
  version: number;
};
type Inventory = {
  symbols: Stored[];
  cached_signal_series: number;
  active_job?: ActiveJob | null;
};
type Download = {
  symbol: string;
  months_downloaded: number;
  months_reused: number;
  candles_downloaded: number;
  error?: string;
};
type Row = {
  symbol: string;
  strategy: string;
  run_id: string;
  timeframe?: string;
  signals: number;
  signals_cached: boolean;
  net: number;
  gross_profit: number;
  won?: number;
  lost?: number;
  capital?: number;
  capital_deployed?: number;
  periods?: number;
  trades: number;
  tp_hits: number;
  sl_hits: number;
  fees: number;
};
type Job = {
  id: string;
  status: string;
  phase: string;
  progress: number;
  current_symbol?: string;
  symbol_index?: number;
  symbol_count?: number;
  results: Row[];
  errors?: { symbol: string; error: string }[];
  skipped?: { symbol: string; strategy: string; reason: string }[];
  downloads?: Download[];
};

const STORAGE_KEY = "scalper-research-v2-job";
const POLL_MS = 1000;

/**
 * FastAPI reports a rejected request two different ways: `detail` is a string
 * for a deliberate refusal and a LIST of per-field objects for a validation
 * failure. Reading only the string turned "528 coins is over the limit" into
 * "Request failed." and left the reason in the browser console.
 */
function refusalText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return "Request failed.";
  const parts = detail
    .map(entry => {
      if (typeof entry !== "object" || entry === null) return "";
      const { loc, msg } = entry as { loc?: unknown; msg?: unknown };
      if (typeof msg !== "string") return "";
      const field = Array.isArray(loc)
        ? loc.filter(part => typeof part === "string" && part !== "body").join(".")
        : "";
      return field ? `${field}: ${msg}` : msg;
    })
    .filter(Boolean);
  return parts.length ? parts.join("; ") : "Request failed.";
}

async function jsonResponse(response: Response) {
  const body = await response.json();
  if (!response.ok) throw new Error(refusalText(body?.detail));
  return body;
}

/**
 * Coin lists arrive as `{source, symbols}` from one endpoint and as a bare
 * array from another. Reading the wrong one put a plain object into state and
 * took the whole tab down on the next render, so the shape is checked here
 * rather than assumed anywhere else.
 */
function coinList(body: unknown): string[] {
  const raw = Array.isArray(body)
    ? body
    : typeof body === "object" && body !== null && Array.isArray((body as { symbols?: unknown }).symbols)
      ? (body as { symbols: unknown[] }).symbols
      : [];
  return raw.filter((value): value is string => typeof value === "string");
}

/**
 * A job payload, or null when the response is not one.
 *
 * Every field the header renders is read straight off this object, so an
 * unexpected body used to crash the whole tab rather than skip one poll. A
 * rejected payload leaves the last good job on screen, which is the honest
 * outcome: nothing new was learned.
 */
function jobFrom(body: unknown): Job | null {
  if (typeof body !== "object" || body === null) return null;
  const candidate = body as Partial<Job>;
  if (typeof candidate.id !== "string" || typeof candidate.status !== "string") return null;
  return {
    ...candidate,
    phase: typeof candidate.phase === "string" ? candidate.phase : "",
    progress: typeof candidate.progress === "number" ? candidate.progress : 0,
    results: Array.isArray(candidate.results) ? candidate.results : [],
    errors: Array.isArray(candidate.errors) ? candidate.errors : [],
    skipped: Array.isArray(candidate.skipped) ? candidate.skipped : [],
    downloads: Array.isArray(candidate.downloads) ? candidate.downloads : [],
  } as Job;
}

function strategyList(body: unknown): Candidate[] {
  if (!Array.isArray(body)) return [];
  return body.filter(
    (row): row is Candidate =>
      typeof row === "object" && row !== null && typeof (row as Candidate).id === "string",
  );
}

export function ResearchV2({ state, connected = false }: { state?: AppState; connected?: boolean }) {
  const [catalog, setCatalog] = useState<Candidate[]>([]);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [inventory, setInventory] = useState<Inventory>();
  const [selected, setSelected] = useState<string[]>(["ctk-1"]);
  const [coins, setCoins] = useState<string[]>(["BTCUSDT"]);
  const [coinQuery, setCoinQuery] = useState("");
  const [query, setQuery] = useState("");
  const [resultQuery, setResultQuery] = useState("");
  const [profitableOnly, setProfitableOnly] = useState(false);
  const [tradePair, setTradePair] = useState<FavoritePair>();
  const [days, setDays] = useState(365);
  const [amount, setAmount] = useState(100);
  const [leverage, setLeverage] = useState(3);
  const [riskPct, setRiskPct] = useState(1);
  const [jobId, setJobId] = useState(() => localStorage.getItem(STORAGE_KEY) ?? "");
  const [job, setJob] = useState<Job>();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [downloadDays, setDownloadDays] = useState(365);

  useEffect(() => {
    void fetch("/api/research/catalog")
      .then(async r => setCatalog(r.ok ? strategyList(await r.json()) : []))
      .catch(() => setCatalog([]));
    void fetch("/api/research/symbols")
      .then(async r => setSymbols(r.ok ? coinList(await r.json()) : []))
      .catch(() => setSymbols([]));
  }, []);

  // The inventory drives the cost estimate, so it keeps refreshing while a job
  // runs: months land in the store as they download.
  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const body = await jsonResponse(await fetch("/api/research/v2/inventory"));
        const rows = (body as Inventory | null)?.symbols;
        if (active) {
          setInventory(
            Array.isArray(rows)
              ? (body as Inventory)
              : { symbols: [], cached_signal_series: 0, active_job: null },
          );
        }
      } catch {
        if (active) setInventory(undefined);
      }
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [job?.status]);

  const running = job?.status === "running";
  useEffect(() => {
    if (!jobId) return;
    let active = true;
    async function poll() {
      try {
        const body = jobFrom(
          await jsonResponse(await fetch(`/api/research/v2/jobs/${encodeURIComponent(jobId)}`)),
        );
        if (active && body) setJob(body);
      } catch {
        /* A missed poll is not a failure; the next one reports the truth. */
      }
    }
    void poll();
    const timer = setInterval(() => void poll(), POLL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [jobId, running]);

  const held = new Map((inventory?.symbols ?? []).map(row => [row.symbol, row]));
  const visibleCoins = symbols.filter(coin => coin.includes(coinQuery.trim().toUpperCase()));
  const needle = query.trim().toLowerCase();
  const visibleStrategies = catalog.filter(
    c => c.name.toLowerCase().includes(needle) || c.id.includes(needle),
  );
  const offline = coins.filter(coin => held.has(coin)).length;
  // Only one research job runs at a time, and it may not be ours: another
  // tab, a script, or a run started before this page was reloaded.
  const active = inventory?.active_job ?? null;
  const foreign = active && active.id !== jobId ? active : null;
  const busy = pending || running || Boolean(foreign);
  // "Running..." on a button would claim this page started the job. When the
  // blocker belongs to someone else, say that instead.
  const waitLabel = foreign ? "Blocked by another job" : "Running…";
  const rows = [...(job?.results ?? [])].sort((a, b) => b.net - a.net);
  const { favorites, toggleFavorite, error: favoriteError } = useFavorites();
  const tradableName = (id: string) => catalog.find(c => c.id === id)?.name ?? id;
  /**
   * Why the engine would refuse this strategy, or "" when it would not.
   *
   * Only a known reason blocks the button. An empty catalog means the fetch is
   * in flight or failed - not evidence against the strategy - and treating it
   * as one left every Trade button dead, with nothing saying why, for as long
   * as the API was unreachable.
   */
  const refusal = (id: string) => {
    const strategy = catalog.find(c => c.id === id);
    if (strategy?.backtest_only) return "Backtest only";
    if (state?.strategy_monitor?.rows.find(row => row.id === id)?.disabled) {
      return "Disabled in Strategy Monitor";
    }
    return "";
  };
  const resultNeedle = resultQuery.trim().toLowerCase();
  const shown = rows.filter(
    row =>
      (!profitableOnly || row.net > 0) &&
      (!resultNeedle ||
        row.symbol.toLowerCase().includes(resultNeedle) ||
        row.strategy.toLowerCase().includes(resultNeedle)),
  );
  const cachedCount = rows.filter(row => row.signals_cached).length;
  // A couple of research entries are fitted to one dataset and refuse
  // elsewhere. Working that out here turns a run that fails once per coin
  // into a sentence shown before anything is downloaded.
  const pinnedSelected = catalog.filter(
    c => selected.includes(c.id) && (c.research_symbols?.length ?? 0) > 0,
  );
  const unrunnable = pinnedSelected.flatMap(c =>
    coins
      .filter(coin => !(c.research_symbols ?? []).includes(coin))
      .map(coin => ({ strategy: c.name, coin, only: (c.research_symbols ?? []).join(", ") })),
  );
  const storedCandles = inventory?.symbols.reduce((sum, s) => sum + s.candles, 0) ?? 0;
  const storedMb = inventory?.symbols.reduce((sum, s) => sum + s.megabytes, 0) ?? 0;

  async function start(event: React.FormEvent) {
    event.preventDefault();
    if (!coins.length) {
      setError("Select at least one coin.");
      return;
    }
    if (!selected.length) {
      setError("Select at least one strategy.");
      return;
    }
    setPending(true);
    setError("");
    try {
      const result = (await jsonResponse(
        await fetch("/api/research/v2/jobs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            symbols: coins,
            strategies: selected,
            days,
            capital: amount,
            leverage,
            risk_pct: riskPct,
          }),
        }),
      ));
      const started = jobFrom(result);
      if (!started) throw new Error("The server did not return a job to follow.");
      localStorage.setItem(STORAGE_KEY, started.id);
      setJob(started);
      setJobId(started.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPending(false);
    }
  }

  /**
   * Fetch history for the chosen coins without scoring anything.
   *
   * A backtest downloads what it needs anyway, but only for the dates that
   * run asks for. Pulling the data as its own step lets the store be filled
   * once, deliberately, for coins you have no CSV of and intend to test later.
   */
  async function download(event: React.FormEvent) {
    event.preventDefault();
    if (!coins.length) {
      setError("Select at least one coin to download.");
      return;
    }
    setPending(true);
    setError("");
    try {
      const result = (await jsonResponse(
        await fetch("/api/research/v2/download", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ symbols: coins, days: downloadDays }),
        }),
      ));
      const started = jobFrom(result);
      if (!started) throw new Error("The server did not return a job to follow.");
      localStorage.setItem(STORAGE_KEY, started.id);
      setJob(started);
      setJobId(started.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPending(false);
    }
  }

  async function cancel(id: string = jobId) {
    if (!id) return;
    try {
      await jsonResponse(
        await fetch(`/api/research/v2/jobs/${encodeURIComponent(id)}/cancel`, {
          method: "POST",
        }),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  /** Watch a job this page did not start, so its progress is visible here. */
  function follow(id: string) {
    localStorage.setItem(STORAGE_KEY, id);
    setJobId(id);
    setError("");
  }

  return (
    <section
      className="research-insights annual-research v2-research"
      aria-label="Offline backtesting"
    >
      <div className="research-section-heading">
        <div>
          <span className="research-eyebrow">STRATEGY LAB &middot; V2</span>
          <h3>Offline backtesting</h3>
        </div>
        <span className="research-readonly">Historical simulation</span>
      </div>

      <p>
        Candles are downloaded once from Binance&rsquo;s published archive into local storage,
        then every test runs against that copy. Each strategy&rsquo;s signals are computed once
        and reused by all six scoring passes, so a repeated run finishes in seconds. The
        simulation is the same one v1 uses, so the numbers are directly comparable.
      </p>

      <div className="research-overview-cards" aria-label="Offline data overview">
        <div>
          <span>Coins stored offline</span>
          <strong>{inventory?.symbols.length ?? 0}</strong>
          <small>
            {offline} of {coins.length} selected already held
          </small>
        </div>
        <div>
          <span>Candles on disk</span>
          <strong>{storedCandles.toLocaleString()}</strong>
          <small>{storedMb.toFixed(1)} MB</small>
        </div>
        <div>
          <span>Signal series cached</span>
          <strong>{inventory?.cached_signal_series ?? 0}</strong>
          <small>Reused instead of recomputed</small>
        </div>
        <div>
          <span>Tests planned</span>
          <strong>{coins.length * selected.length}</strong>
          <small>Coin / strategy pairs</small>
        </div>
      </div>

      {inventory && inventory.symbols.length > 0 && (
        <div className="research-storage">
          <strong>Local candle store</strong>
          <div className="research-storage-stats">
            {inventory.symbols.slice(0, 8).map(row => (
              <span key={row.symbol}>
                {row.symbol}: {row.months} month{row.months === 1 ? "" : "s"} ({row.first} &rarr;{" "}
                {row.last})
              </span>
            ))}
          </div>
          <small>
            A coin already stored needs no download. The month in progress is refreshed only when
            a run asks for dates it does not yet cover.
          </small>
        </div>
      )}

      <fieldset disabled={busy} className="research-settings v2-coin-section">
        <legend>1. Choose coins</legend>
        <div className="research-settings-grid">
            <div className="research-coin-picker">
              <label>
                Search coins
                <input
                  type="search"
                  placeholder="BTC, ETH, SOL"
                  value={coinQuery}
                  onChange={e => setCoinQuery(e.target.value)}
                />
              </label>
              <strong id="v2-coins-label">Coins</strong>
              <span role="status">{coins.length} selected</span>
              <div className="research-coin-list" role="group" aria-labelledby="v2-coins-label">
                {visibleCoins.slice(0, 200).map(coin => (
                  <label key={coin} data-selected={coins.includes(coin)}>
                    <input
                      type="checkbox"
                      checked={coins.includes(coin)}
                      onChange={e =>
                        setCoins(prev =>
                          e.target.checked ? [...prev, coin] : prev.filter(c => c !== coin),
                        )
                      }
                    />
                    {coin}
                    {held.has(coin) && <span className="v2-tag">offline</span>}
                  </label>
                ))}
              </div>
              <small>
                {visibleCoins.length} of {symbols.length} coins match.
              </small>
            </div>
          <div className="v2-bulk">
            <strong>Bulk selection</strong>
            <div className="research-coin-actions">
              <button
                type="button"
                onClick={() => setCoins(prev => [...new Set([...prev, ...visibleCoins])])}
                disabled={!visibleCoins.length}
              >
                Add all {visibleCoins.length} matching
              </button>
              <button type="button" onClick={() => setCoins([])} disabled={!coins.length}>
                Clear
              </button>
              <button
                type="button"
                onClick={() =>
                  setCoins(prev => [
                    ...new Set([...prev, ...(inventory?.symbols.map(s => s.symbol) ?? [])]),
                  ])
                }
                disabled={!inventory?.symbols.length}
                title="Stored coins need no download, so a run over them starts immediately"
              >
                Add all {inventory?.symbols.length ?? 0} stored offline
              </button>
            </div>
            <small>
              Searching does not drop what you already picked, so you can build a list
              across several searches.
            </small>
          </div>
        </div>
      </fieldset>

      <form onSubmit={download} className="v2-download">
        <fieldset disabled={busy} className="research-settings">
          <legend>2. Download market data</legend>
          <p>
            No CSV needed. History comes from Binance&rsquo;s published archive, verified
            against the checksum published beside it, and is stored locally as complete
            UTC months. A month with a missing candle is rejected rather than stored, so
            what you test on has every bar or none.
          </p>
          <div className="research-settings-grid">
            <label>
              History to fetch (days)
              <input
                type="number"
                min="2"
                max="365"
                required
                value={downloadDays}
                onChange={e => setDownloadDays(Number(e.target.value))}
              />
              <small>Applies to every coin selected below.</small>
            </label>
            <div className="v2-download-summary">
              <strong>{coins.length}</strong>
              <span>coin{coins.length === 1 ? "" : "s"} selected</span>
              <small>
                {coins.length - offline > 0
                  ? `${coins.length - offline} still to fetch, ${offline} already stored`
                  : "all already stored - downloading again only tops up the current month"}
              </small>
            </div>
            <div className="research-start-row">
              <button type="submit" disabled={busy || !coins.length}>
                {busy ? waitLabel : `Download ${coins.length} coin(s)`}
              </button>
            </div>
          </div>
        </fieldset>
      </form>

      <form onSubmit={start}>
        <fieldset disabled={busy} className="research-settings">
          <legend>3. Configure the backtest</legend>
          <div className="research-settings-grid">
            <label>
              History (days)
              <input
                type="number"
                min="2"
                max="365"
                required
                value={days}
                onChange={e => setDays(Number(e.target.value))}
              />
              <small>Ends at the last complete UTC day, so a run is reproducible.</small>
            </label>
            <label>
              Capital (USDT)
              <input
                type="number"
                min="0.01"
                max="100000"
                step="any"
                required
                value={amount}
                onChange={e => setAmount(Number(e.target.value))}
              />
            </label>
            <label>
              Leverage
              <input
                type="number"
                min="1"
                max="10"
                required
                value={leverage}
                onChange={e => setLeverage(Number(e.target.value))}
              />
            </label>
            <label>
              Risk per trade (%)
              <input
                type="number"
                min="0.05"
                max="5"
                step="0.05"
                required
                value={riskPct}
                onChange={e => setRiskPct(Number(e.target.value))}
              />
            </label>
          </div>

          <details className="research-strategy-picker" open>
            <summary>Strategies ({selected.length} selected)</summary>
            <div className="research-selection-toolbar">
              <label>
                Search strategies
                <input
                  type="search"
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder="Name or id"
                />
              </label>
              <button
                type="button"
                onClick={() =>
                  setSelected(prev => [
                    ...new Set([...prev, ...visibleStrategies.map(c => c.id)]),
                  ])
                }
                disabled={!visibleStrategies.length}
                title="Select every strategy matching the current search, including any beyond the first 200 shown"
              >
                Select all {visibleStrategies.length}
              </button>
              <button type="button" onClick={() => setSelected([])} disabled={!selected.length}>
                Clear
              </button>
            </div>
            <div className="research-strategy-list">
              {visibleStrategies.slice(0, 200).map(candidate => (
                <label
                  key={candidate.id}
                  className="research-strategy-option"
                  data-selected={selected.includes(candidate.id)}
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(candidate.id)}
                    onChange={e =>
                      setSelected(prev =>
                        e.target.checked
                          ? [...prev, candidate.id]
                          : prev.filter(id => id !== candidate.id),
                      )
                    }
                  />
                  <span>
                    {candidate.name}
                    {(candidate.research_symbols?.length ?? 0) > 0 && (
                      <span className="v2-tag">{candidate.research_symbols?.join("/")} only</span>
                    )}
                    <br />
                    <small>{candidate.timeframe}</small>
                  </span>
                </label>
              ))}
            </div>
          </details>

          {unrunnable.length > 0 && (
            <p className="v2-note" role="status">
              <strong>
                {unrunnable.length} coin / strategy pair
                {unrunnable.length === 1 ? "" : "s"} will be skipped.
              </strong>{" "}
              {pinnedSelected
                .map(c => `${c.name} only runs on ${(c.research_symbols ?? []).join(", ")}`)
                .join("; ")}
              . It was fitted to that one dataset, so a result anywhere else would not mean
              anything. Everything else in the run is unaffected.
            </p>
          )}
          <div className="research-start-row">
            <span>
              {coins.length - offline > 0
                ? `${coins.length - offline} coin(s) need downloading first.`
                : "All selected coins are already stored offline."}
            </span>
            <button type="submit" className="btn-primary" disabled={busy}>
              {busy ? waitLabel : "Run offline backtest"}
            </button>
          </div>
        </fieldset>
      </form>

      {foreign && (
        <div className="v2-foreign" role="status">
          <div>
            <strong>Another research job is running</strong>
            <p>
              {foreign.symbol}
              {foreign.kind === "download" ? " · downloading" : " · backtest"} &mdash;{" "}
              {foreign.phase} ({foreign.progress}%)
            </p>
            <small>
              Only one runs at a time, so this one has to finish or be stopped before a new run
              can start. It may have been started in another tab, or before this page reloaded.
            </small>
          </div>
          <div className="research-coin-actions">
            <button type="button" onClick={() => follow(foreign.id)}>
              Follow it here
            </button>
            {foreign.version === 2 && (
              <button type="button" onClick={() => void cancel(foreign.id)}>
                Cancel it
              </button>
            )}
          </div>
        </div>
      )}

      {error && <p role="alert">{error}</p>}

      {job && (
        <div className="research-job-panel" aria-label="Run progress">
          <div className="research-section-heading">
            <h4>
              {running ? "Running" : job.status.replace(/_/g, " ")}
              {job.current_symbol && running
                ? ` · ${job.current_symbol} (${job.symbol_index}/${job.symbol_count})`
                : ""}
            </h4>
            {running && (
              <button type="button" onClick={() => void cancel()}>
                Cancel
              </button>
            )}
          </div>
          <p>{job.phase}</p>
          <progress value={job.progress} max={100} />
          {job.downloads && job.downloads.length > 0 && (
            <small>
              {job.downloads
                .map(d =>
                  d.error
                    ? `${d.symbol}: ${d.error}`
                    : `${d.symbol}: ${d.months_downloaded} downloaded, ${d.months_reused} reused`,
                )
                .join(" · ")}
            </small>
          )}
          {job.skipped && job.skipped.length > 0 && (
            <small>
              Skipped {job.skipped.length} pair
              {job.skipped.length === 1 ? "" : "s"}:{" "}
              {job.skipped.map(s => `${s.symbol} / ${s.strategy}`).join(", ")} &mdash; fitted to a
              single dataset.
            </small>
          )}
          {job.errors && job.errors.length > 0 && (
            <ul role="alert">
              {job.errors.map(e => (
                <li key={e.symbol}>
                  {e.symbol}: {e.error}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {rows.length > 0 && (
        <>
          {tradePair && (
            <FavoriteTrade
              key={favoriteId(tradePair.symbol, tradePair.strategy)}
              pair={tradePair}
              name={tradableName(tradePair.strategy)}
              state={state}
              connected={connected}
              available={!refusal(tradePair.strategy)}
              onClose={() => setTradePair(undefined)}
            />
          )}
          {favoriteError && <p role="alert">{favoriteError}</p>}
          <div className="research-section-heading">
            <h4>Results</h4>
            <span className="research-readonly">
              {cachedCount} of {rows.length} answered from cached signals
            </span>
          </div>
          <div className="research-selection-toolbar">
            <label>
              Filter results
              <input
                type="search"
                value={resultQuery}
                onChange={e => setResultQuery(e.target.value)}
                placeholder="Coin or strategy"
              />
            </label>
            <button
              type="button"
              onClick={() => setProfitableOnly(on => !on)}
              aria-pressed={profitableOnly}
            >
              Profitable only ({rows.filter(row => row.net > 0).length})
            </button>
            {shown.length !== rows.length && (
              <span className="research-readonly">
                Showing {shown.length} of {rows.length}
              </span>
            )}
          </div>
          {!shown.length && (
            <p className="research-readonly">
              No results match this filter. {rows.length} pair
              {rows.length === 1 ? "" : "s"} ran.
            </p>
          )}
          {shown.length > 0 && (
          <div className="research-table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Coin</th>
                  <th scope="col">Strategy</th>
                  <th scope="col">Won</th>
                  <th scope="col">Lost</th>
                  <th scope="col">Fees</th>
                  <th scope="col">Before fees</th>
                  <th scope="col">Net P/L</th>
                  <th scope="col">Return</th>
                  <th scope="col">Trades</th>
                  <th scope="col">TP hits</th>
                  <th scope="col">SL hits</th>
                  <th scope="col">Win rate</th>
                  <th scope="col">Signals</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {shown.map(row => {
                  const decided = row.tp_hits + row.sl_hits;
                  const winRate = decided ? (100 * row.tp_hits) / decided : null;
                  const deployed = row.capital_deployed;
                  const ret = deployed ? (100 * row.net) / deployed : null;
                  return (
                    <tr key={`${row.symbol}-${row.strategy}`}>
                      <td>{row.symbol}</td>
                      <td>{row.strategy}</td>
                      <td className="research-profit">
                        {row.won == null ? "—" : `+${row.won.toFixed(2)}`}
                      </td>
                      <td className="research-loss">
                        {row.lost == null ? "—" : `-${row.lost.toFixed(2)}`}
                      </td>
                      <td className="research-loss">-{row.fees.toFixed(2)}</td>
                      <td
                        className={row.gross_profit >= 0 ? "research-profit" : "research-loss"}
                      >
                        {row.gross_profit >= 0 ? "+" : ""}
                        {row.gross_profit.toFixed(2)}
                      </td>
                      <td className={row.net >= 0 ? "research-profit" : "research-loss"}>
                        {row.net >= 0 ? "+" : ""}
                        {row.net.toFixed(2)}
                      </td>
                      <td className={row.net >= 0 ? "research-profit" : "research-loss"}>
                        {ret == null ? "—" : `${ret >= 0 ? "+" : ""}${ret.toFixed(1)}%`}
                      </td>
                      <td>{row.trades}</td>
                      <td className="research-profit">{row.tp_hits}</td>
                      <td className="research-loss">{row.sl_hits}</td>
                      <td>{winRate == null ? "—" : `${winRate.toFixed(1)}%`}</td>
                      <td>
                        {row.signals}
                        {row.signals_cached && <span className="v2-tag">cached</span>}
                      </td>
                      <td>
                        <div className="research-coin-actions">
                          <button
                            type="button"
                            className="research-favorite"
                            aria-pressed={favorites.includes(favoriteId(row.symbol, row.strategy))}
                            aria-label={`Favorite ${row.symbol} / ${tradableName(row.strategy)}`}
                            onClick={() => toggleFavorite(favoriteId(row.symbol, row.strategy))}
                          >
                            {favorites.includes(favoriteId(row.symbol, row.strategy)) ? "★" : "☆"}
                          </button>
                          <button
                            type="button"
                            disabled={!!refusal(row.strategy)}
                            title={refusal(row.strategy) || undefined}
                            aria-label={`Trade ${row.symbol} / ${tradableName(row.strategy)}`}
                            onClick={() =>
                              setTradePair({ symbol: row.symbol, strategy: row.strategy })
                            }
                          >
                            Trade
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          )}
          <dl className="v2-legend">
            <dt>Won / Lost</dt>
            <dd>
              Money made by winning trades and lost by losing ones, each already net of that
              trade&rsquo;s own fees. They differ by exactly the Net P/L.
            </dd>
            <dt>Before fees</dt>
            <dd>What the trades made as price movement alone, with the fee bill added back.</dd>
            <dt>Net P/L</dt>
            <dd>
              Before fees minus fees &mdash; the actual result, in USDT.
              {rows[0]?.periods
                ? ` Each strategy is scored over ${rows[0].periods} separate periods that each
                   start flat with ${rows[0].capital?.toFixed(0) ?? "the configured"} USDT, so
                   the figure is the sum of ${rows[0].periods} independent runs, not one account
                   compounding.`
                : ""}
            </dd>
            <dt>Return</dt>
            <dd>
              Net P/L against the capital actually put to work &mdash; starting capital multiplied
              by the number of periods. This is the number to compare strategies on, because the
              USDT figure scales with whatever capital you typed in.
            </dd>
          </dl>
          <small>
            Historical simulation only. Costs and order minimums are assumptions, not verified
            exchange fills, and past results do not establish live profitability.
          </small>
        </>
      )}
    </section>
  );
}
