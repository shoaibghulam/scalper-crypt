import { useState } from "react";
import { useAlphaCoinHistory } from "../hooks/useAlphaCoinHistory";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import "./Research.css";
import "./AlphaLab.css";

type Metrics = { net: number; trade_count: number; total_fees: number; return_pct: number; closed_equity_drawdown_pct: number; profit_factor: number | null };
type Finalist = { method: string; formula: string; status: string; passed: boolean; unique_formulas: number;
  training: Metrics; validation: Metrics; holdout: Metrics; stress_holdout: Metrics };
type Config = { symbol: string; symbols?: string[]; method: string; device: string; days: number; population: number; generations: number; seed: number;
  capital: number; leverage: number; fee_bps: number; slippage_bps: number; spread_bps: number; minimum_trades: number };
type Job = { id: string; status: string; phase: string; progress: number; created_at: number; config: Config;
  coin_results?: { symbol: string; result: NonNullable<Job["result"]> }[]; errors?: { symbol: string; error: string }[]; completed_coins?: number; coin_count?: number;
  evaluations?: number; unique_training_formulas?: number;
  history?: { method: string; generation: number; best_training_score: number }[];
  result?: { finalists: Finalist[]; limitations: string[]; protocol: string;
    windows: Record<string, { start: number; end: number; candles: number }> } };
type Hardware = { torch: string | null; cuda: boolean; gpu: string | null; memory_gb?: number; multi_coin_limit?: number };

async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/alpha-lab/${path}`, body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request failed; check your inputs and retry.");
  return data as T;
}

const defaults: Config = { symbol: "BTCUSDT", method: "compare", device: "cuda", days: 30, population: 16,
  generations: 6, seed: 42, capital: 100, leverage: 3, fee_bps: 5, slippage_bps: 2, spread_bps: 2, minimum_trades: 30 };
const numericFields = [
  ["days", "History (days)", 7, 90, 1], ["population", "Candidates per generation / method", 8, 64, 1],
  ["generations", "Generations", 2, 30, 1], ["seed", "Random seed", 0, 2147483647, 1],
  ["capital", "Backtest capital (USDT)", 1, 100000, 1], ["leverage", "Backtest leverage", 1, 10, 1],
  ["fee_bps", "Fee per side (bps)", 0, 100, .1], ["slippage_bps", "Slippage per side (bps)", 0, 100, .1],
  ["spread_bps", "Full spread (bps)", 0, 100, .1], ["minimum_trades", "Minimum trades per period", 10, 500, 1],
] as const;
const coinNames: Record<string, string> = { BTCUSDT: "Bitcoin", ETHUSDT: "Ethereum", SOLUSDT: "Solana", XRPUSDT: "XRP", BNBUSDT: "BNB", DOGEUSDT: "Dogecoin" };
const methodNames: Record<string, string> = { compare: "Compare both approaches", rl: "AI learning", gp: "Evolve trading rules" };
const presets = [
  { id: "quick", name: "Quick check", note: "A small experiment to try the workflow.", days: 7, population: 8, generations: 2 },
  { id: "balanced", name: "Balanced", note: "A practical starting point for research.", days: 30, population: 16, generations: 6 },
  { id: "deep", name: "Deeper search", note: "More history and ideas. Takes longer; may still find no passing strategy.", days: 90, population: 32, generations: 12 },
] as const;

async function loadCoins(): Promise<string[]> {
  const response = await fetch("/api/research/symbols");
  if (!response.ok) throw new Error("Could not load coins. Please retry.");
  const data: unknown = await response.json();
  if (!data || typeof data !== "object" || !("symbols" in data) || !Array.isArray(data.symbols)) throw new Error("Coin list is unavailable. Please retry.");
  return [...new Set(data.symbols.filter((symbol): symbol is string => typeof symbol === "string" && /^[A-Z0-9]{2,25}USDT$/.test(symbol)))].sort();
}
const active = (status?: string) => status === "queued" || status === "running";
const date = (value: number) => new Date(value).toISOString().slice(0, 16).replace("T", " ");

export function AlphaLab() {
  const client = useQueryClient();
  const coinHistory = useAlphaCoinHistory();
  const [config, setConfig] = useState<Config>(defaults);
  const [jobId, setJobId] = useState("");
  const [endDate, setEndDate] = useState("");
  const [multiCoin, setMultiCoin] = useState(false);
  const [coinSelection, setCoinSelection] = useState<string[]>(["BTCUSDT"]);
  const selectedCoins = coinSelection.filter(symbol => !coinHistory.coins.includes(symbol));
  const setSelectedCoins = (next: string[] | ((current: string[]) => string[])) => setCoinSelection(current => typeof next === "function" ? next(current.filter(symbol => !coinHistory.coins.includes(symbol))) : next);
  const [passesOnly, setPassesOnly] = useState(true);
  const [reportCoin, setReportCoin] = useState("");
  const toggleCoin = (symbol: string) => setSelectedCoins(current => current.includes(symbol) ? current.filter(s => s !== symbol) : current.length < coinLimit ? [...current, symbol] : current);
  const [coinSearch, setCoinSearch] = useState("");
  const [deviceChoice, setDeviceChoice] = useState("auto");
  const coins = useQuery({ queryKey: ["alpha-coins"], queryFn: loadCoins, staleTime: 300000, retry: false });
  const availableCoins = (coins.data ?? []).filter(symbol => !coinHistory.coins.includes(symbol));
  const hardware = useQuery({ queryKey: ["alpha-hardware"], queryFn: () => api<Hardware>("capabilities"), retry: false, refetchInterval: 30000 });
  const coinLimit = hardware.data?.multi_coin_limit ?? 20;
  const archive = useQuery({ queryKey: ["alpha-history"], queryFn: () => api<Job[]>("jobs"), refetchInterval: 5000 });
  const job = useQuery({ queryKey: ["alpha-job", jobId], queryFn: () => api<Job>(`jobs/${jobId}`), enabled: !!jobId,
    refetchInterval: query => active(query.state.data?.status) ? 1500 : false });
  const selectJob = (id: string) => { setJobId(id); setReportCoin(""); };
  const device = deviceChoice === "auto" ? (hardware.data?.cuda ? "cuda" : "cpu") : deviceChoice;
  const start = useMutation({ mutationFn: () => api<Job>("jobs", { ...config, symbols: multiCoin ? selectedCoins : [], device, end_time: endDate ? Date.parse(`${endDate}T00:00:00Z`) : null }),
    onSuccess: data => { coinHistory.remember(data.config.symbols?.length ? data.config.symbols : [data.config.symbol]); client.setQueryData(["alpha-job", data.id], data); selectJob(data.id); void client.invalidateQueries({ queryKey: ["alpha-history"] }); } });
  const cancel = useMutation({ mutationFn: () => api<Job>(`jobs/${jobId}/cancel`, {}),
    onSuccess: () => { void client.invalidateQueries({ queryKey: ["alpha-job", jobId] }); } });
  const busy = start.isPending || active(job.data?.status) || !!archive.data?.some(row => active(row.status));
  const needsTorch = config.method !== "gp";
  const unavailable = needsTorch && (!hardware.data?.torch || (device === "cuda" && !hardware.data?.cuda));
  const selectedCoinValid = !coins.isError && (multiCoin ? selectedCoins.length > 0 && selectedCoins.length <= coinLimit && selectedCoins.every(s => availableCoins.includes(s)) : availableCoins.includes(config.symbol));
  const query = coinSearch.trim().toUpperCase().replace(/[\s/]/g, "");
  const filteredCoins = availableCoins.filter(symbol => symbol.includes(query) || coinNames[symbol]?.toUpperCase().includes(query));
  const selectedPreset = presets.find(p => p.days === config.days && p.population === config.population && p.generations === config.generations);
  const runningJob = archive.data?.find(row => active(row.status));

  const batch = (job.data?.config.symbols?.length ?? 0) > 1;
  const reports = job.data?.coin_results ?? (job.data?.result ? [{ symbol: job.data.config.symbol, result: job.data.result }] : []);
  const shownReports = reports.filter(row => !batch || !passesOnly || row.result.finalists.some(f => f.passed));
  const selectedReport = shownReports.find(row => row.symbol === reportCoin) ?? shownReports[0];
  const report = selectedReport?.result;
  const jobLabel = (configuration: Config) => configuration.symbols?.length ? `${configuration.symbols.length} coins` : configuration.symbol;

  return <section className="alpha-lab annual-research" aria-labelledby="alpha-heading">
    <div className="research-section-heading"><div><span className="research-eyebrow">AUTOMATED DISCOVERY · 1 MINUTE</span>
      <h2 id="alpha-heading">Alpha Lab</h2></div><span className="research-readonly">Research only</span></div>
    <p>Choose a coin and let the system search for trading ideas. We test them on past prices, subtract trading costs, and check the strongest ideas on prices they have not seen.</p>
    <div className="alpha-hardware">{hardware.isPending ? "Checking your computer…" : hardware.isError ? "Computer check failed." :
      hardware.data?.cuda ? `GPU ready · ${hardware.data.gpu}` : "CPU available"}
      <span> Historical testing only. Starting a search does not place trades.</span>
      {hardware.isError && <button type="button" onClick={() => void hardware.refetch()}>Retry computer check</button>}</div>
    <form onInvalidCapture={event => { const details = (event.target as HTMLElement).closest("details"); if (details) details.open = true; }}
      onSubmit={event => { event.preventDefault(); if (!busy && !unavailable && selectedCoinValid) start.mutate(); }}>
      <fieldset disabled={busy} className="alpha-step"><legend>1. Choose your coin</legend>
        <p>Choose one coin or select all available coins to search them one after another. Each coin gets the same search budget.</p>
        <label className="alpha-checkbox"><input type="checkbox" checked={multiCoin} disabled={!hardware.data?.multi_coin_limit} onChange={e => setMultiCoin(e.target.checked)} />Search multiple coins for passing candidates</label>
        {hardware.data && !hardware.data.multi_coin_limit && <p>Multi-coin search needs the updated API. Let any active experiment finish before restarting the API.</p>}
        {multiCoin && <div className="alpha-popular"><button type="button" onClick={() => setSelectedCoins(Object.keys(coinNames).filter(s => availableCoins.includes(s)))}>Select common coins</button><button type="button" disabled={!availableCoins.length || availableCoins.length > coinLimit} onClick={() => setSelectedCoins(availableCoins)}>Select all coins</button><button type="button" disabled={!filteredCoins.length || new Set([...selectedCoins, ...filteredCoins]).size > coinLimit} onClick={() => setSelectedCoins(current => [...new Set([...current, ...filteredCoins])])}>Select search results</button><button type="button" onClick={() => setSelectedCoins([])}>Clear coins</button></div>}
        <div className="alpha-popular"><span>{coinHistory.coins.length} previously submitted coins hidden in this browser.</span><button type="button" disabled={!coinHistory.coins.length} onClick={() => { coinHistory.reset(); setCoinSearch(""); }}>Reset coin history</button></div>
        <p>Coins are remembered when a search starts successfully, including queued coins. Reset lets you retry them after cancellation or a failed run. Saved reports are kept.</p>
        {coinHistory.error && <p role="alert">{coinHistory.error}</p>}
        {coins.data && !coins.isError && !availableCoins.length && <p role="status">No new coins available. Reset coin history to choose these coins again.</p>}
        <div className="alpha-popular" role="group" aria-label="Popular coins">{Object.entries(coinNames).filter(([symbol]) => availableCoins.includes(symbol)).map(([symbol, name]) =>
          <button type="button" key={symbol} aria-label={`Choose ${name} (${symbol.slice(0, -4)})`} aria-pressed={multiCoin ? selectedCoins.includes(symbol) : config.symbol === symbol} disabled={multiCoin && selectedCoins.length >= coinLimit && !selectedCoins.includes(symbol)} onClick={() => { if (multiCoin) toggleCoin(symbol); else setConfig({ ...config, symbol }); setCoinSearch(""); }}>
            <strong>{symbol.slice(0, -4)}</strong><span>{name}</span></button>)}</div>
        {coins.isPending ? <p role="status">Loading available coins…</p> : coins.isError ? <p role="alert">Could not load available coins. <button type="button" onClick={() => void coins.refetch()}>Retry coin list</button></p> : <>
          <div className="alpha-coin-picker"><label>Search coins<input type="search" placeholder="Try Bitcoin, ETH or SOL" value={coinSearch} onChange={e => setCoinSearch(e.target.value)} /></label>
            {multiCoin ? <div className="alpha-multi-list" role="group" aria-label="Available coins">{filteredCoins.length ? filteredCoins.map(symbol => <label key={symbol} className="alpha-checkbox"><input type="checkbox" checked={selectedCoins.includes(symbol)} disabled={selectedCoins.length >= coinLimit && !selectedCoins.includes(symbol)} onChange={() => toggleCoin(symbol)} />{symbol.slice(0, -4)} / USDT</label>) : <p>No matching coins</p>}</div> : <label>Available coins<select size={5} aria-describedby="alpha-coin-help" value={filteredCoins.includes(config.symbol) ? config.symbol : ""} onChange={e => setConfig({ ...config, symbol: e.target.value })}>
              <option value="" disabled>{filteredCoins.length ? "Select a coin" : "No matching coins"}</option>{filteredCoins.map(symbol => <option key={symbol} value={symbol}>{symbol.slice(0, -4)} / USDT{coinNames[symbol] ? ` — ${coinNames[symbol]}` : ""}</option>)}</select></label>}</div>
          <p id="alpha-coin-help">{filteredCoins.length} coins match your search. Click a coin above or select one from the list.</p>
        </>}
        <div className="alpha-selection">{multiCoin ? `${selectedCoins.length}/${coinLimit} coins selected` : "Selected coin:"} <strong>{multiCoin ? selectedCoins.slice(0, 20).join(", ") + (selectedCoins.length > 20 ? ` and ${selectedCoins.length - 20} more` : "") || "Choose at least one coin" : selectedCoinValid ? `${multiCoin ? `${selectedCoins.length} coins` : `${config.symbol.slice(0, -4)} / USDT`}` : "Choose an available coin"}</strong></div>
      </fieldset>
      {multiCoin && selectedCoins.length > 20 && <p>Large search: {selectedCoins.length} coins will run sequentially. You can cancel anytime; completed results stay saved.</p>}
      <fieldset disabled={busy} className="alpha-step"><legend>2. Choose how much to explore</legend>
        <div className="alpha-presets" role="group" aria-label="Experiment presets">{presets.map(preset => <button type="button" key={preset.id} aria-pressed={selectedPreset?.id === preset.id}
          onClick={() => setConfig({ ...config, days: preset.days, population: preset.population, generations: preset.generations })}>
          <strong>{preset.name}{preset.id === "balanced" ? " · Recommended" : ""}</strong><span>{preset.days} days of prices · {preset.population * preset.generations} ideas per approach</span><small>{preset.note}</small></button>)}</div>
        {!selectedPreset && <p>Custom settings selected. You can adjust them below or choose a preset.</p>}
      </fieldset>
      <fieldset disabled={busy} className="alpha-step"><legend>3. Set your test budget</legend>
        <p>This is simulated money used to compare results.</p>
        <div className="alpha-fields">{numericFields.filter(([key]) => key === "capital" || key === "leverage").map(([key, label, min, max, step]) => <label key={key}>{label}<input type="number" required min={min} max={max} step={step}
          value={config[key]} onChange={e => setConfig({ ...config, [key]: e.target.valueAsNumber })} /></label>)}</div>
      </fieldset>
      <details className="alpha-advanced"><summary>Advanced settings <span>— optional: search method, dates and trading costs</span></summary>
      <fieldset disabled={busy} className="alpha-fields"><legend>Fine-tune your experiment</legend>
        <label>Search method<select value={config.method} onChange={e => setConfig({ ...config, method: e.target.value })}>
          <option value="compare">Compare both approaches (recommended)</option><option value="rl">AI learning (reinforcement learning)</option><option value="gp">Evolve trading rules (genetic programming)</option></select></label>
        <label>Neural training device<select value={deviceChoice} disabled={config.method === "gp"} onChange={e => setDeviceChoice(e.target.value)}>
          <option value="auto">Automatic — use GPU if available</option><option value="cuda">NVIDIA GPU (CUDA)</option><option value="cpu">CPU</option></select></label>
        <label>End date (UTC, exclusive)<input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} /></label>
        {numericFields.filter(([key]) => key !== "capital" && key !== "leverage").map(([key, label, min, max, step]) => <label key={key}>{label}<input type="number" required min={min} max={max} step={step}
          value={config[key]} onChange={e => setConfig({ ...config, [key]: e.target.valueAsNumber })} /></label>)}
      </fieldset>
      <p>One basis point (bps) is 0.01%. The default fee of 5 bps means 0.05% each time a trade opens or closes. Slippage models a worse fill price; spread is the gap between buy and sell prices.</p>
      <p>More candidates and generations search more ideas. The random seed makes experiments easier to repeat. Leave the end date blank to use the latest available prices.</p>
      </details>
      <div className="alpha-launch"><div><strong>{multiCoin ? `${selectedCoins.length} coins` : `${config.symbol.slice(0, -4)} / USDT`} · {config.days} days · {methodNames[config.method]}</strong><p>{config.capital} USDT simulated capital per coin · {config.leverage}× leverage · trading costs included</p></div>
      {unavailable && <p role="status">{!hardware.data?.torch ? "Neural search requires PyTorch. Genetic programming remains available." : "CUDA is not available. Select CPU to run neural search."}</p>}
      <button className="alpha-start" type="submit" disabled={busy || unavailable || !selectedCoinValid}>Start discovery</button>
      {runningJob && <p>A search is already running. <button type="button" onClick={() => selectJob(runningJob.id)}>View running search</button></p>}
      </div>
      <div className="alpha-explainer"><div><strong>Learn</strong><span>Build ideas using the first 60% of prices.</span></div><div><strong>Compare</strong><span>Choose a finalist using the next 20%.</span></div><div><strong>Final check</strong><span>Test on the last 20%, then raise trading costs.</span></div></div>
    </form>
    {(start.error || cancel.error || job.error || archive.error) && <p role="alert">{(start.error || cancel.error || job.error || archive.error)?.message}</p>}
    {archive.data && archive.data.length > 0 && <label>Saved discovery runs<select value={jobId} onChange={e => selectJob(e.target.value)}>
      <option value="">Choose a run</option>{archive.data.map(row => <option key={row.id} value={row.id}>{jobLabel(row.config)} · {methodNames[row.config.method] ?? row.config.method} · {new Date(row.created_at * 1000).toLocaleString()} · {row.status}</option>)}</select></label>}
    {job.data && <div className="alpha-results">
      <h3>{jobLabel(job.data.config)} · {methodNames[job.data.config.method]} · {job.data.status}</h3>
      <p role="status">{job.data.phase}</p>
      {active(job.data.status) && <><progress max={100} value={job.data.progress} aria-label="Discovery progress" /><button type="button" disabled={cancel.isPending} onClick={() => cancel.mutate()}>Cancel discovery</button></>}
      <p>{batch ? "Latest coin: " : ""}{job.data.evaluations ?? 0} candidate evaluations · {job.data.unique_training_formulas ?? 0} unique training formulas</p>
      {!!job.data.history?.length && <details><summary>Training progress</summary><ul>{job.data.history.map(row => <li key={`${row.method}-${row.generation}`}>
        {row.method.toUpperCase()} generation {row.generation}: best training score {row.best_training_score.toFixed(2)}</li>)}</ul></details>}
        <a href={`/api/alpha-lab/jobs/${job.data.id}`} download={`alpha-lab-${job.data.id}.json`}>Download full report and formula tokens</a>
      {batch && <div className="alpha-step">
        <p>{job.data.completed_coins ?? 0}/{job.data.coin_count ?? job.data.config.symbols?.length} coins finished / {reports.reduce((sum, row) => sum + row.result.finalists.filter(f => f.passed).length, 0)} passing candidates</p>
        <label className="alpha-checkbox"><input type="checkbox" checked={passesOnly} onChange={e => setPassesOnly(e.target.checked)} />Show passing candidates only</label>
        <p>A pass requires positive profit after costs and enough trades in learning, selection, final testing and higher-cost testing. Searching more coins does not guarantee a pass; confirm any survivor on fresh data.</p>
        {shownReports.length === 0 && <p>{active(job.data.status) ? "No passing candidates found so far." : "No passing candidates found in the completed results. Uncheck the filter to review rejected candidates."}</p>}
        {shownReports.length > 0 && <label>Coin results<select value={selectedReport?.symbol ?? ""} onChange={e => setReportCoin(e.target.value)}>{shownReports.map(row => <option key={row.symbol} value={row.symbol}>{row.symbol} / {row.result.finalists.filter(f => f.passed).length} passed</option>)}</select></label>}
      </div>}
      {!!job.data.errors?.length && <div role="alert"><p>Some coins could not be tested:</p><ul>{job.data.errors.map(row => <li key={row.symbol}>{row.symbol}: {row.error}</li>)}</ul></div>}
      {report && <>
        <h3>{selectedReport.symbol} results</h3>
        <p>The final check uses prices that were kept out of learning and selection. A positive result still needs confirmation on fresh data.</p>
        <details><summary>How this experiment was tested</summary><p>{report.protocol}</p>
        <ul>{Object.entries(report.windows).map(([name, window]) => <li key={name}>{name}: {date(window.start)} to {date(window.end)} UTC · {window.candles.toLocaleString()} candles</li>)}</ul>
        </details>
        {report.finalists.filter(f => !batch || !passesOnly || f.passed).map(finalist => <article key={finalist.method} className="alpha-finalist">
          <h3>{methodNames[finalist.method]} result</h3><p className="alpha-verdict" data-passed={finalist.passed}><strong>{finalist.passed ? "Passed the first checks — needs forward testing" : "Did not pass — loss or too few trades"}</strong></p>
          <div className="alpha-result-cards"><div><span>Final test profit / loss</span><strong>{finalist.holdout.net.toFixed(2)} USDT</strong><small>After trading costs</small></div><div><span>Trades in the final test</span><strong>{finalist.holdout.trade_count}</strong><small>More trades provide more evidence</small></div><div><span>With higher trading costs</span><strong>{finalist.stress_holdout.net.toFixed(2)} USDT</strong><small>Double spread and slippage</small></div></div>
          <div className="research-table-scroll"><table><caption>Historical outcomes · {finalist.unique_formulas} formulas searched</caption>
            <thead><tr><th scope="col">Stage</th><th scope="col">Trades</th><th scope="col">Profit / loss (USDT)</th><th scope="col">Fees (USDT)</th><th scope="col">Return</th><th scope="col">Largest closed-balance drop</th></tr></thead>
            <tbody>{([ ["Learning", finalist.training], ["Selection", finalist.validation], ["Final test (holdout)", finalist.holdout], ["Final test with higher costs", finalist.stress_holdout] ] as const).map(([name, metrics]) =>
              <tr key={name}><th scope="row">{name}</th><td>{metrics.trade_count}</td><td>{metrics.net.toFixed(2)}</td><td>{metrics.total_fees.toFixed(2)}</td><td>{metrics.return_pct.toFixed(2)}%</td><td>{metrics.closed_equity_drawdown_pct.toFixed(2)}%</td></tr>)}</tbody>
          </table></div>
          <details><summary>Technical formula and detailed verdict</summary><p>{finalist.status}</p><p className="alpha-formula"><code>{finalist.formula}</code></p></details>
        </article>)}
        <details><summary>What these results can and cannot tell you</summary><ul>{report.limitations.map(note => <li key={note}>{note}</li>)}</ul></details>
      </>}
    </div>}
  </section>;
}
