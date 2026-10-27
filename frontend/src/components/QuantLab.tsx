import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import "./QuantLab.css";

type Metrics = { net_profit: number; total_return: number; trades: number; sharpe: number;
  max_drawdown: number; profit_factor: number | null; win_rate: number; fees_paid: number;
  funding_paid: number; final_equity: number; halted: boolean };
type Period = { metrics: Metrics; equity: [number, number][] };
type Strategy = { strategy_id: string; status: string; reasons: string[];
  definition: { direction: string; timeframe: string; entry: unknown; risk: unknown };
  training: Period; validation: Period; test: Period; stress_test?: Period; walk_forward: Metrics[];
  robustness: { score: number; stability_fraction: number; passed: boolean; cost_stress: unknown;
    parameter_variations: unknown; monte_carlo: unknown }; regimes: unknown; ml_note: string };
type Progress = { generation: number; population: number; total_tested: number; best: number;
  median: number; worst: number; profitable: number; rejected: number; best_net_profit: number;
  best_sharpe: number; lowest_drawdown: number };
type CoinResult = { symbol: string; timeframe: string; total_tested: number; strategies: Strategy[];
  periods: Record<string, { start: number; end: number }>; portfolio: { selected_before_holdout: string[] }; history: Progress[] };
type Job = { id: string; kind: string; status: string; created_at: number; phase: string;
  request: { symbols: string[]; timeframe: string }; progress?: Progress; worker_warning?: string;
  results?: CoinResult[]; errors?: { symbol: string; error: string }[]; result?: Period };

async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/quant-lab/${path}`, body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Check your settings and try again.");
  return data as T;
}
const active = (status?: string) => status === "running" || status === "queued";
const money = (n: number) => n.toFixed(2);
const pct = (n: number) => `${(n * 100).toFixed(2)}%`;

export function EquityCurve({ period, label }: { period: Period; label: string }) {
  const values = period.equity.map(p => p[1]);
  if (!values.length) return null;
  const low = Math.min(...values), high = Math.max(...values);
  const points = values.map((v, i) => `${20 + i / Math.max(1, values.length - 1) * 660},${140 - (v - low) / Math.max(1e-9, high - low) * 110}`).join(" ");
  return <figure className="quant-curve"><figcaption>{label} · {money(values[0])} → {money(values[values.length - 1])} USDT</figcaption>
    <svg viewBox="0 0 700 160" role="img" aria-label={`${label}, minimum equity ${money(low)}, maximum ${money(high)} USDT`}>
      <line x1="20" x2="680" y1="145" y2="145" stroke="currentColor" opacity=".2" />
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="2.5" />
    </svg></figure>;
}

function MetricsTable({ strategy }: { strategy: Strategy }) {
  return <div className="quant-scroll"><table><caption>Each stage starts with the same simulated capital. Costs included.</caption>
    <thead><tr><th>Stage</th><th>Net USDT</th><th>Trades</th><th>Sharpe</th><th>Drawdown</th><th>Profit factor</th><th>Win rate</th><th>Fees</th><th>Funding paid</th></tr></thead>
    <tbody>{([['Learning', strategy.training], ['Selection', strategy.validation], ['Unseen test', strategy.test]] as const).map(([name, result]) => <tr key={name}>
      <th>{name}</th><td>{money(result.metrics.net_profit)}</td><td>{result.metrics.trades}</td><td>{money(result.metrics.sharpe)}</td>
      <td>{pct(result.metrics.max_drawdown)}</td><td>{result.metrics.profit_factor === null ? 'Undefined' : money(result.metrics.profit_factor)}</td>
      <td>{pct(result.metrics.win_rate)}</td><td>{money(result.metrics.fees_paid)}</td><td>{money(result.metrics.funding_paid)}</td>
    </tr>)}</tbody></table></div>;
}

export function QuantLab() {
  const client = useQueryClient();
  const [symbols, setSymbols] = useState(['BTCUSDT', 'ETHUSDT', 'XRPUSDT']);
  const [coinSearch, setCoinSearch] = useState('');
  const [timeframe, setTimeframe] = useState('5m');
  const [days, setDays] = useState(60);
  const [population, setPopulation] = useState(64);
  const [generations, setGenerations] = useState(8);
  const [capital, setCapital] = useState(1000);
  const [leverage, setLeverage] = useState(1);
  const [fee, setFee] = useState(5);
  const [slippage, setSlippage] = useState(2);
  const [spread, setSpread] = useState(2);
  const [seed, setSeed] = useState(42);
  const [minimumTrades, setMinimumTrades] = useState(20);
  const [trials, setTrials] = useState(20);
  const [endDate, setEndDate] = useState('');
  const [ml, setMl] = useState(false);
  const [jobId, setJobId] = useState('');
  const [dailyLoss, setDailyLoss] = useState(5);
  const [drawdown, setDrawdown] = useState(20);
  const [position, setPosition] = useState(25);
  const [losses, setLosses] = useState(8);
  const history = useQuery({ queryKey: ['quant-history'], queryFn: () => api<Job[]>('jobs'), refetchInterval: 5000 });
  const job = useQuery({ queryKey: ['quant-job', jobId], queryFn: () => api<Job>(`jobs/${jobId}`), enabled: !!jobId,
    refetchInterval: q => active(q.state.data?.status) ? 2000 : false });
  const coins = useQuery({ queryKey: ['quant-coins'], queryFn: async () => {
    const response = await fetch('/api/research/symbols');
    if (!response.ok) throw new Error('Could not load the full coin list. The three starting coins are available below.');
    return await response.json() as { symbols: string[] };
  }, staleTime: 300000 });
  const selected = (data: Job) => { setJobId(data.id); client.setQueryData(['quant-job', data.id], data); void client.invalidateQueries({ queryKey: ['quant-history'] }); };
  const start = useMutation({ mutationFn: () => api<Job>('jobs', { symbols, timeframe, days,
    end: endDate ? Date.parse(`${endDate}T00:00:00Z`) : null,
    search: { population, generations, capital, seed, min_trades: minimumTrades, optimization_trials: trials,
      ml_filter: ml, costs: { fee: fee / 10000, slippage: slippage / 10000, spread: spread / 10000, require_funding: true },
      safety: { max_leverage: leverage, max_position_size: position / 100, max_daily_loss: dailyLoss / 100,
        max_drawdown: drawdown / 100, max_consecutive_losses: losses } } }), onSuccess: selected });
  const cancel = useMutation({ mutationFn: () => api(`jobs/${jobId}/cancel`, {}), onSuccess: () => { void client.invalidateQueries({ queryKey: ['quant-job', jobId] }); } });
  const paper = useMutation({ mutationFn: ({ symbol, id }: { symbol: string; id: string }) => api<Job>(`jobs/${jobId}/paper/${symbol}/${id}`, {}), onSuccess: selected });
  const busy = start.isPending || !!history.data?.some(j => j.kind === 'research' && active(j.status));
  const toggle = (symbol: string) => setSymbols(previous => previous.includes(symbol) ? previous.filter(s => s !== symbol) : previous.length < 20 ? [...previous, symbol] : previous);
  const allCoins = [...new Set(['BTCUSDT', 'ETHUSDT', 'XRPUSDT', ...(coins.data?.symbols ?? [])])];
  const filtered = allCoins.filter(s => s.includes(coinSearch.trim().toUpperCase().replace('/', '')));
  const progress = job.data?.progress;
  const fields = [
    { label: 'History (days)', value: days, set: setDays, min: 7, max: 366 },
    { label: 'Candidates per generation', value: population, set: setPopulation, min: 8, max: 10000 },
    { label: 'Generations', value: generations, set: setGenerations, min: 1, max: 100 },
    { label: 'Optimization trials', value: trials, set: setTrials, min: 0, max: 500 },
    { label: 'Minimum trades in each stage', value: minimumTrades, set: setMinimumTrades, min: 2, max: 10000 },
    { label: 'Random seed', value: seed, set: setSeed, min: 0, max: 2147483647 },
    { label: 'Fee per side (bps)', value: fee, set: setFee, min: 0, max: 100, step: .1 },
    { label: 'Slippage per side (bps)', value: slippage, set: setSlippage, min: 0, max: 100, step: .1 },
    { label: 'Full spread (bps)', value: spread, set: setSpread, min: 0, max: 100, step: .1 },
    { label: 'Maximum daily loss (%)', value: dailyLoss, set: setDailyLoss, min: .1, max: 50, step: .1 },
    { label: 'Maximum drawdown (%)', value: drawdown, set: setDrawdown, min: 1, max: 80 },
    { label: 'Maximum position allocation (%)', value: position, set: setPosition, min: 1, max: 100 },
    { label: 'Stop after consecutive losses', value: losses, set: setLosses, min: 1, max: 100 },
  ];
  return <section className="quant-lab">
    <header><p className="quant-eyebrow">RESEARCH → VALIDATION → PAPER</p><h1>Strategy Research Lab</h1>
      <p>Discover simple trading rules, test them after costs, and check them on prices the search has never seen.</p>
      <p className="quant-note">Compiled CPU research · GPU is not used by this engine · No live orders</p></header>
    <form onSubmit={event => { event.preventDefault(); start.mutate(); }}>
      <fieldset disabled={busy}><legend>1. Choose your coins and timeframe</legend>
        <div className="quant-choices">{['BTCUSDT', 'ETHUSDT', 'XRPUSDT'].map(symbol => <label key={symbol}>
          <input type="checkbox" checked={symbols.includes(symbol)} onChange={() => toggle(symbol)} />{symbol.replace('USDT', ' / USDT')}</label>)}</div>
        <details><summary>Find more coins · {symbols.length} selected (maximum 20)</summary>
          <label>Search coins<input value={coinSearch} onChange={e => setCoinSearch(e.target.value)} placeholder="BTC, ETH, SOL…" /></label>
          <div className="quant-coin-list">{filtered.map(symbol => <label key={symbol}><input type="checkbox" checked={symbols.includes(symbol)} disabled={!symbols.includes(symbol) && symbols.length >= 20} onChange={() => toggle(symbol)} />{symbol}</label>)}</div>
          <button type="button" onClick={() => setSymbols(filtered.slice(0, 20))}>Select first {Math.min(20, filtered.length)} matching coins</button>{' '}
          <button type="button" onClick={() => setSymbols([])}>Clear coins</button>
        </details>
        {coins.isError && <p role="status">{coins.error.message}</p>}
        <p>{symbols.length ? symbols.join(' · ') : 'Choose at least one coin.'}</p>
        <label>Entry timeframe<select value={timeframe} onChange={e => { setTimeframe(e.target.value); if (e.target.value === '1h') setDays(d => Math.max(30, d)); }}>
          <option value="1m">1 minute</option><option value="5m">5 minutes</option><option value="15m">15 minutes</option><option value="1h">1 hour</option></select></label>
        <p>Run separate experiments to compare timeframes using the same dates and costs.</p>
      </fieldset>
      <fieldset disabled={busy}><legend>2. Set your search budget</legend><div className="quant-choices">
        <button type="button" onClick={() => { setDays(timeframe === '1h' ? 30 : 14); setPopulation(16); setGenerations(3); setTrials(5); }}>Quick check · 48 candidates</button>
        <button type="button" onClick={() => { setDays(60); setPopulation(64); setGenerations(8); setTrials(20); }}>Balanced · 512 candidates</button>
        <button type="button" onClick={() => { setDays(120); setPopulation(256); setGenerations(20); setTrials(50); }}>Deeper search · 5,120 candidates</button></div>
        <p>{days} days · up to {(population * generations).toLocaleString()} search evaluations + {trials} optimization trials per coin. Repeated strategies reuse cached results. Validation takes additional work.</p>
        <div className="quant-grid"><label>Simulated capital (USDT)<input required type="number" min="1" max="1000000000" value={capital} onChange={e => setCapital(Number(e.target.value))} /></label>
          <label>Maximum leverage<select value={leverage} onChange={e => setLeverage(Number(e.target.value))}>{[1, 2, 3, 4, 5].map(x => <option key={x} value={x}>{x}×</option>)}</select></label></div>
        <details><summary>Advanced search settings and hard risk limits</summary><div className="quant-grid">{fields.map(f => <label key={f.label}>{f.label}<input required type="number" min={f.min} max={f.max} step={f.step ?? 1} value={f.value} onChange={e => f.set(Number(e.target.value))} /></label>)}
          <label>End date (UTC, exclusive)<input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} /></label></div>
          <label className="quant-check"><input type="checkbox" checked={ml} onChange={e => setMl(e.target.checked)} />Add a Random Forest trade filter (requires 40 completed training trades)</label>
          <p>One basis point is 0.01%. Historical funding is included. One position per strategy. Risk limits cannot be changed by the search.</p>
        </details>
      </fieldset>
      <div className="quant-workflow"><p><strong>Learn · 60%</strong><br />Generate and evolve rules.</p><p><strong>Validate · 20%</strong><br />Walk forward, perturb parameters and increase costs.</p><p><strong>Unseen test · 20%</strong><br />Frozen finalists face a final rejection check.</p></div>
      <button className="quant-primary" disabled={busy || !symbols.length} type="submit">{busy ? 'Research running…' : 'Start research'}</button>
    </form>
    {[start.error, cancel.error, paper.error, job.error, history.error].filter(Boolean).map((error, i) => <p role="alert" key={i}>{error?.message}</p>)}
    <label className="quant-history">Saved experiments and paper sessions<select value={jobId} onChange={e => setJobId(e.target.value)}><option value="">Choose a run</option>
      {history.data?.map(row => <option key={row.id} value={row.id}>{row.kind} · {row.request.symbols.join(', ')} · {row.request.timeframe} · {row.status} · {new Date(row.created_at * 1000).toLocaleString()}</option>)}</select></label>
    {job.data && <section aria-label="Experiment results"><h2>{job.data.kind === 'paper' ? 'Forward paper session' : 'Research results'} · {job.data.status}</h2>
      <p role="status">{job.data.phase}</p>{job.data.worker_warning && <p role="alert">{job.data.worker_warning}</p>}
      {active(job.data.status) && <button disabled={cancel.isPending} onClick={() => cancel.mutate()}>{job.data.kind === 'paper' ? 'Stop paper session' : 'Cancel research'}</button>}
      <p><a href={`/api/quant-lab/jobs/${jobId}/download`}>Download full experiment report</a></p>
      {progress && <div className="quant-stats">{Object.entries(progress).filter(([key, value]) => key !== 'best_strategy' && typeof value === 'number').map(([key, value]) => <p key={key}><span>{key.replaceAll('_', ' ')}</span><strong>{Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })}</strong></p>)}</div>}
      {job.data.errors?.map(error => <p role="alert" key={error.symbol}>{error.symbol}: {error.error}</p>)}
      {job.data.result && <><p>Paper net P/L: {money(job.data.result.metrics.net_profit)} USDT · {job.data.result.metrics.trades} completed trades</p><EquityCurve period={job.data.result} label="Forward paper equity" /></>}
      {job.data.results?.map(coin => <section key={coin.symbol}><h2>{coin.symbol} · {coin.timeframe}</h2>
        <p>{coin.total_tested.toLocaleString()} evaluations · {coin.strategies.filter(s => s.status === 'research_passed').length} research passes. Every finalist is shown, including losses.</p>
        <p><a href={`/api/quant-lab/jobs/${jobId}/candidates/${coin.symbol}`}>Download all evaluated candidates, including rejected rules</a></p>
        <details><summary>Dates and split boundaries</summary>{Object.entries(coin.periods).map(([name, p]) => <p key={name}>{name}: {new Date(p.start).toISOString()} → {new Date(p.end).toISOString()}</p>)}</details>
        {coin.strategies.map(s => <article className="quant-result" key={s.strategy_id}>
          <h3>{s.definition.direction.toUpperCase()} · {s.strategy_id}</h3><p className="quant-verdict">{s.status === 'research_passed' ? 'Passed research checks — eligible for paper approval' : 'Did not pass'}</p>
          {!!s.reasons.length && <ul>{s.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
          <MetricsTable strategy={s} /><EquityCurve period={s.test} label="Unseen-test equity" />
          {s.stress_test && <p>Unseen test with triple fees, slippage and spread: {money(s.stress_test.metrics.net_profit)} USDT.</p>}
          <p>Robustness score: {pct(s.robustness.score)} · Profitable parameter variations: {pct(s.robustness.stability_fraction)}</p><p>ML: {s.ml_note}</p>
          <details><summary>Rules, walk-forward folds, cost stress and regime results</summary>
            <pre>{JSON.stringify({ definition: s.definition, walk_forward: s.walk_forward, robustness: s.robustness, regimes: s.regimes }, null, 2)}</pre></details>
          <p><a href={`/api/quant-lab/jobs/${jobId}/export/${coin.symbol}/${s.strategy_id}`}>Export strategy JSON</a></p>
          {s.status === 'research_passed' && <button disabled={paper.isPending} onClick={() => paper.mutate({ symbol: coin.symbol, id: s.strategy_id })}>Approve and start paper session</button>}
        </article>)}
        <p>Portfolio proposal selected before the unseen test: {coin.portfolio.selected_before_holdout.join(', ') || 'No sufficiently independent candidates'}.</p>
      </section>)}
      <p className="quant-note">A research pass is historical evidence, not a promise of profit. Paper sessions use future closed candles and simulated fills. Live deployment is not enabled.</p>
    </section>}
  </section>;
}
