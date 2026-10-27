import { favoriteId, useFavorites } from '../hooks/useFavorites';
import type { AppState } from "../lib/api";
import { SavedMarketData } from "./SavedMarketData";
import { useEffect, useState } from 'react';

type Candidate = { id: string; name: string; timeframe: string; backtest_only?: boolean };
type Row = { timeframe?: string; gross_profit?: number; symbol?: string; strategy: string; run_id: string; net: number; trades: number; tp_hits: number; sl_hits: number; forced_exits: number; session_exits?: number; fees: number };
type Job = { cache_hits?: number; cache_misses?: number; symbols?: string[]; current_coin?: string; coin_index?: number; coin_count?: number; errors?: { symbol: string; error: string }[]; id: string; status: string; phase: string; progress: number; symbol: string; downloaded?: number; expected?: number; results: Row[] };
type Setup = { symbol: string; setup_ready: boolean; setup_note: string; direction?: string; score: number; expires_at?: number };
const storageKey = 'scalper-research-job';
async function jsonResponse(response: Response) {
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Request failed. Check your settings.');
  return body;
}

export function AnnualResearch({ catalog, symbols, state, connected = false }: { catalog: Candidate[]; symbols: string[]; state?: AppState; connected?: boolean }) {
  const [selected, setSelected] = useState<string[]>(['the-minute-black']);
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [selectedCoins, setSelectedCoins] = useState<string[]>([]);
  const [customSupported, setCustomSupported] = useState(false);
  const [unavailableStrategies, setUnavailableStrategies] = useState<{ id: string; name: string; reason: string }[]>([]);
  const [coinQuery, setCoinQuery] = useState('');
  const [coinMode, setCoinMode] = useState('top20');
  const [batchSupported, setBatchSupported] = useState(false);
  useEffect(() => {
    let active = true;
    async function check() {
      try {
        const response = await fetch('/api/research/capabilities');
        const body = await response.json();
        if (active) {
          setBatchSupported(response.ok && body.top20 === true);
          setCustomSupported(response.ok && body.custom === true);
          setUnavailableStrategies(response.ok && Array.isArray(body.unavailable_strategies) ? body.unavailable_strategies : []);
        }
      } catch { if (active) { setBatchSupported(false); setCustomSupported(false); } }
    }
    void check(); const timer = setInterval(() => void check(), 10000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  const [days, setDays] = useState(365);
  const [entryTimeframe, setEntryTimeframe] = useState('all');
  const [amount, setAmount] = useState(100);
  const [leverage, setLeverage] = useState(3);
  const [riskPct, setRiskPct] = useState(1);
  const [end, setEnd] = useState('');
  const [jobId, setJobId] = useState(() => localStorage.getItem(storageKey) ?? '');
  const [job, setJob] = useState<Job>();
  const [testedStrategies, setTestedStrategies] = useState<string[]>([]);
  const [testedError, setTestedError] = useState('');
  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const result: unknown = await jsonResponse(await fetch('/api/research/tested-strategies'));
        if (!Array.isArray(result) || !result.every(id => typeof id === 'string')) throw new Error('Invalid testing history');
        if (active) { setTestedStrategies(result); setTestedError(''); }
      } catch {
        if (active) setTestedError('Testing history is unavailable. Some tested markers may be missing.');
      }
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 10000);
    return () => { active = false; clearInterval(timer); };
  }, [job?.status]);
  const testedIds = new Set([...testedStrategies, ...(job?.results ?? []).map(row => row.strategy)]);
  const { favorites, pairs: favoritePairs, toggleFavorite, error: favoriteError } = useFavorites();
  const favoriteStrategies = new Set(favoritePairs.map(pair => pair.strategy));
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [error, setError] = useState('');
  const [disableRequests, setDisableRequests] = useState<Record<string, number>>({});
  const [actionError, setActionError] = useState('');
  const [pending, setPending] = useState(false);
  const [query, setQuery] = useState('');
  const [scanStrategy, setScanStrategy] = useState('the-minute-black');
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState('');
  const [scan, setScan] = useState<{ candidates: Setup[]; ready: boolean; error?: string; scanned_at: number; age_seconds: number; stats: { universe_size?: number } }>();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!jobId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await jsonResponse(await fetch(`/api/research/jobs/${jobId}`)) as Job;
        if (!active) return;
        setJob(result);
        if (result.status === 'running') timer = setTimeout(() => void poll(), 2000);
      } catch (cause) { if (active) setError(String(cause)); }
    }
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [jobId]);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  async function disableStrategy(strategy: string) {
    if (favoriteStrategies.has(strategy)) return;
    setActionError('');
    setDisableRequests(previous => ({ ...previous, [strategy]: Date.now() }));
    try {
      await jsonResponse(await fetch('/api/strategy-monitor/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy, disabled: true }),
      }));
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : String(cause));
      setDisableRequests(previous => { const next = { ...previous }; delete next[strategy]; return next; });
    }
  }
  const strategyStatus = state?.strategy_monitor;
  const enabledIds = new Set(strategyStatus?.rows.filter(row => !row.disabled).map(row => row.id) ?? []);
  const enabledCatalog = catalog.filter(candidate => (candidate.backtest_only || enabledIds.has(candidate.id))
    && (entryTimeframe === 'all' || candidate.timeframe.split(' ')[0] === entryTimeframe));
  const enabledSelected = selected.filter(id => enabledCatalog.some(candidate => candidate.id === id));
  const visibleCoins = symbols.filter(coin => coin.includes(coinQuery.trim().toUpperCase()));
  const validSelectedCoins = selectedCoins.filter(coin => symbols.includes(coin));
  const coinCount = coinMode === 'top20' ? 20 : coinMode === 'custom' ? validSelectedCoins.length : 1;
  const busy = pending || job?.status === 'running';
  const isFavorite = (row: Row) => favorites.includes(favoriteId(row.symbol ?? job?.symbol ?? '', row.strategy));
  const favoriteCount = job?.results.filter(isFavorite).length ?? 0;
  const resultRows = (job?.results ?? []).filter(row => !favoritesOnly || isFavorite(row)).sort((a, b) => b.net - a.net);
  const ready = scan && !scan.error && now / 1000 - scan.scanned_at < 60
    ? scan.candidates.filter(c => c.setup_ready && c.expires_at != null && c.expires_at > now / 1000) : [];
  return <section className="research-insights annual-research" aria-label="Long-range strategy backtest">
    <div className="research-section-heading"><div><span className="research-eyebrow">STRATEGY LAB</span><h3>One-year strategy &amp; coin testing</h3></div><span className="research-readonly">Historical simulation</span></div>
    <div className="research-overview-cards" aria-label="Experiment overview">
      <div><span>Available coins</span><strong>{symbols.length || '?'}</strong><small>Active USDT perpetuals</small></div>
      <div><span>Strategies selected</span><strong>{enabledSelected.length}<small> / {enabledCatalog.length}</small></strong><small>Candle-based strategies</small></div>
      <div><span>History per coin</span><strong>{days} <small>days</small></strong><small>Plus warm-up candles</small></div>
      <div><span>Tests planned</span><strong>{enabledSelected.length * coinCount}</strong><small>Independent coin / strategy pairs</small></div>
    </div>
    <p>Choose strategies and starting capital to compare the top 20 active USDT perpetuals automatically, or select one or more coins yourself. A year uses 365 complete days of 1-minute candles plus warm-up. Long comparisons run in the background and can take hours.</p>
    <SavedMarketData symbol={symbol} refreshKey={job?.status} />
    <form onSubmit={async e => {
      e.preventDefault();
      if (coinMode === 'top20' && !batchSupported) { setError('20-coin comparison is waiting for the backend update.'); return; }
      if (coinMode === 'custom' && (!customSupported || !validSelectedCoins.length)) { setError('Select at least one coin and wait for custom selection support.'); return; }
      if (!enabledSelected.length) { setError('Select at least one enabled strategy.'); return; }
      setPending(true); setError('');
      try {
        const result = await jsonResponse(await fetch('/api/research/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...(coinMode === 'single' ? { symbol } : coinMode === 'custom' ? { symbols: validSelectedCoins } : {}), coin_mode: coinMode, strategies: enabledSelected, days, capital: amount, leverage, risk_pct: riskPct, ...(end ? { end_time: Date.parse(`${end}T00:00:00Z`) } : {}) }) })) as Job;
        localStorage.setItem(storageKey, result.id); setJob(result); setJobId(result.id);
      } catch (cause) { setError(String(cause)); } finally { setPending(false); }
    }}>
      <fieldset disabled={busy} className="research-settings"><legend>1. Configure your experiment</legend>
        <div className="research-settings-grid">
          <label>Coin selection <select value={coinMode} onChange={e => setCoinMode(e.target.value)}><option value="top20">Automatic top 20 coins by volume</option><option value="single">Single coin</option><option value="custom">Choose coins (1, 2, 3 or more)</option></select></label>
          <div className="research-coin-picker">
            <label>Search all coins <input type="search" placeholder="Search BTC, ETH, SOL?" disabled={coinMode === 'top20'} value={coinQuery} onChange={e => setCoinQuery(e.target.value)} /></label>
            {coinMode === 'custom' ? <>
              <strong id="research-coins-label">Backtest coins</strong>
              <span role="status">{validSelectedCoins.length} coins selected</span>
              <div className="research-coin-actions">
                <button type="button" disabled={!visibleCoins.length} onClick={() => setSelectedCoins(previous => [...new Set([...previous, ...visibleCoins])])}>Select all matching coins</button>
                <button type="button" disabled={!selectedCoins.length} onClick={() => setSelectedCoins([])}>Clear coins</button>
              </div>
              <div className="research-coin-list" role="group" aria-labelledby="research-coins-label">
                {visibleCoins.map(coin => <label key={coin} data-selected={selectedCoins.includes(coin)}>
                  <input type="checkbox" checked={selectedCoins.includes(coin)} onChange={e => setSelectedCoins(previous => e.target.checked ? [...previous, coin] : previous.filter(value => value !== coin))} />{coin}
                </label>)}
              </div>
              <small>Selected: {validSelectedCoins.join(', ') || 'None'}. Search keeps your selections.</small>
            </> : <label>Backtest coin <select size={6} disabled={coinMode !== 'single' || !symbols.length} required={coinMode === 'single'} value={symbol} onChange={e => setSymbol(e.target.value)} aria-describedby="research-coin-help">
              {symbol && !visibleCoins.includes(symbol) && <option value={symbol}>{symbol} (selected)</option>}
              {visibleCoins.map(coin => <option key={coin} value={coin}>{coin}</option>)}
            </select></label>}
            <small id="research-coin-help">{coinMode !== 'top20' ? `${visibleCoins.length} of ${symbols.length} active coins match.` : 'Automatic mode selects the top 20 coins when you start.'}</small>
            {coinMode !== 'top20' && coinQuery && !visibleCoins.length && <span role="status">No matching coins. Clear your search to see all markets.</span>}
          </div>
          <label>Long-range capital (USDT) <input type="number" min="0.01" max="100000" step="any" required value={amount} onChange={e => setAmount(Number(e.target.value))} /></label>
          <label>Backtest leverage <input type="number" min="1" max="10" required value={leverage} onChange={e => setLeverage(Number(e.target.value))} /></label>
          <label>Risk per trade (%) <input type="number" min="0.05" max="5" step="0.05" required value={riskPct} onChange={e => setRiskPct(Number(e.target.value))} /><small>The PDF guide suggests 0.25%–0.50% while researching its five setups.</small></label>
          <label>Entry timeframe <select value={entryTimeframe} onChange={e => setEntryTimeframe(e.target.value)}>
            <option value="all">All strategy timeframes</option>
            {[...new Set(catalog.map(candidate => candidate.timeframe.split(' ')[0]))].sort((a, b) => parseInt(a) - parseInt(b)).map(timeframe => <option key={timeframe} value={timeframe}>{timeframe} entry candles</option>)}
          </select><small>Filters strategies by their existing entry candles. Higher-timeframe confirmations stay unchanged.</small></label>
          <label>Duration <select value={days} onChange={e => setDays(Number(e.target.value))}><option value={7}>7 days</option><option value={30}>30 days</option><option value={90}>90 days</option><option value={365}>1 year (365 days)</option></select></label>
          <label>End date (UTC, exclusive) <input type="date" value={end} onChange={e => setEnd(e.target.value)} /></label>
        </div>
        {!strategyStatus && <p role="status">Waiting for strategy enable/disable status before selecting strategies.</p>}
        {strategyStatus && enabledCatalog.length === 0 && <p role="status">No enabled strategies match this timeframe. Choose another timeframe or enable a strategy in Strategy Monitor.</p>}
        <details open className="research-strategy-picker"><summary>2. Select strategies: {enabledSelected.length} selected / {enabledCatalog.length} enabled candle strategies</summary>
          <p className="research-helper">★ Tested means a saved backtest exists for this strategy on at least one dataset. It does not indicate profitability or testing on every selected coin.</p>
          {testedError && <p role="status">{testedError}</p>}
          <div className="research-selection-toolbar"><label>Search strategies <input value={query} onChange={e => setQuery(e.target.value)} /></label>{' '}
          <button type="button" onClick={() => setSelected(enabledCatalog.map(c => c.id))}>Select all strategies</button>{' '}
          <button type="button" onClick={() => setSelected([])}>Clear selection</button></div>
          <div className="research-strategy-list">{enabledCatalog.filter(c => `${c.name} ${c.id}`.toLowerCase().includes(query.toLowerCase())).map(c => <label key={c.id} className="research-strategy-option" data-selected={selected.includes(c.id)}>
            <input type="checkbox" checked={selected.includes(c.id)} onChange={e => setSelected(e.target.checked ? [...selected, c.id] : selected.filter(id => id !== c.id))} /> {c.name} · {c.timeframe}{c.backtest_only ? ' · Backtest only' : ''}
            {testedIds.has(c.id) && <small className="research-tested" title="A saved backtest exists for this strategy"><span aria-hidden="true">★ </span>Tested</small>}
          </label>)}</div>
        </details>
        {unavailableStrategies.length > 0 && <details className="research-strategy-picker">
          <summary>Advanced PDF: {unavailableStrategies.length} setups require additional data or simulation support</summary>
          <ul>{unavailableStrategies.map(candidate => <li key={candidate.id}><strong>{candidate.name}</strong><p>{candidate.reason}</p></li>)}</ul>
          <p className="research-helper">These setups are not selectable in candle-only backtests. They are not represented by substitute candle strategies.</p>
        </details>}
        {coinMode === "top20" && <p className="research-helper">Top 20 is selected once using current 24-hour volume, not historical profitability or a live entry signal. Every coin uses the same date range and independent starting capital. Missing-history coins are reported and not replaced.</p>}
        <p className="research-helper">Risk: {riskPct}% per trade; fee: 5 bps per side; slippage: 2 bps per side; spread: 2 bps. Stress doubles slippage and spread. Starting capital resets in each of three periods. Use the detailed evaluator below for custom costs.</p>
        <div className="research-start-row"><span>{enabledSelected.length * coinCount} tests ? {days} days ? {amount} USDT starting capital per test</span><button className="btn-primary" disabled={(coinMode === "custom" && (!customSupported || !validSelectedCoins.length)) || !enabledSelected.length || !enabledCatalog.length || (coinMode === "top20" && !batchSupported)}>Start historical backtest</button></div>
      </fieldset>
    </form>
    {coinMode === "top20" && !batchSupported && <p role="status">20-coin comparison is waiting for the backend update. Existing jobs can finish first.</p>}
    {coinMode === "custom" && !customSupported && <p role="status">Custom coin selection is waiting for the backend update.</p>}
    {error && <p role="alert">{error}</p>}
    {job && <div className="research-job-panel" aria-live="polite"><p>{job.symbol}: {job.phase} · {job.status}</p>
      {job.status === 'running' && <><progress max={100} value={job.progress} aria-label="Current download or replay pass" /> {job.progress}% of current pass
        <button onClick={async () => { try { await jsonResponse(await fetch(`/api/research/jobs/${job.id}/cancel`, { method: 'POST' })); } catch (cause) { setError(String(cause)); } }}>Cancel backtest</button></>}
      {job.downloaded != null && <p>{job.downloaded.toLocaleString()} / {job.expected?.toLocaleString()} candles downloaded</p>}
      {job.cache_hits != null && <p>Current coin: {job.cache_hits} cached chunks reused ? {job.cache_misses ?? 0} chunks requested from Binance.</p>}
      {job.symbols && <p>Selected universe: {job.symbols.join(', ')}</p>}
      <button onClick={() => {
        const url = URL.createObjectURL(new Blob([JSON.stringify(job, null, 2)], { type: 'application/json' }));
        const link = document.createElement('a'); link.href = url; link.download = `coin-comparison-${job.id}.json`; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }}>Download coin comparison report</button>
      {job.errors && job.errors.length > 0 && <ul aria-label="Unavailable coin histories">{job.errors.map((item, i) => <li key={`${item.symbol}-${i}`}>{item.symbol}: {item.error}</li>)}</ul>}
      <p className="research-helper">Completed backtest reports are saved automatically in the research database, including individual trades and settings. Open Saved experiments below to retrieve them later.</p>
      <p className="research-helper">Disable strategy blocks new entries for this strategy across all coins and bots. Existing positions keep their protection; backtest reports remain saved. Re-enable it from Strategy Monitor.</p>
      <p className="research-helper">Strategies with any favorite coin cannot be disabled from this table. Remove all favorites for that strategy to show its Disable strategy button again.</p>
      {(!connected || state?.mode !== 'testnet') && <p role="status">Strategy actions require a connected testnet engine.</p>}
      {actionError && <p role="alert">{actionError}</p>}
      <p className="research-helper">Profit/loss before fees includes simulated spread and slippage. Net profit/loss subtracts recorded fees. Entry timeframe describes candles, not trade holding time. Legacy rows use the current strategy timeframe when no snapshot was saved.</p>
      <p>TP/SL counts below use base costs only, across all three periods. A TP hit can still lose money after fees. Period-end and session-end exits are listed separately. This full-period table reveals validation outcomes.</p>
      <div className="research-favorites-toolbar">
        <button type="button" aria-pressed={favoritesOnly} onClick={() => setFavoritesOnly(!favoritesOnly)}>Favorites only ({favoriteCount})</button>
        <span className="research-helper">Star a coin / strategy pair. Favorites stay saved in this browser across backtests. This filter shows favorites in the current backtest.</span>
      </div>
      {favoriteError && <p role="alert">{favoriteError}</p>}
      <div className="research-table-scroll"><table><caption>Historical outcomes — not a live-trade recommendation</caption><thead><tr>{['Favorite', 'Coin', 'Strategy', 'Entry timeframe', 'Trades', 'TP hits', 'SL hits', 'Period-end exits', 'Session-end exits', 'Profit/loss before fees (USDT)', 'Fees (USDT)', 'Net profit/loss (USDT)', 'Report', 'Action'].map(t => <th key={t}>{t}</th>)}</tr></thead>
        <tbody>{resultRows.length === 0 && <tr><td colSpan={14}>{favoritesOnly ? 'No favorite results in this backtest. Show all results to add favorites.' : 'No results yet.'}</td></tr>}{resultRows.map(r => {
          const strategyState = state?.strategy_monitor?.rows.find(item => item.id === r.strategy);
          const disabled = strategyState?.disabled === true;
          const requestedAt = disableRequests[r.strategy];
          const pendingDisable = !!requestedAt && now - requestedAt < 30000 && !disabled;
          const timeframe = r.timeframe ?? catalog.find(candidate => candidate.id === r.strategy)?.timeframe;
          const gross = r.gross_profit ?? r.net + r.fees;
          const coin = r.symbol ?? job.symbol;
          const strategyName = catalog.find(c => c.id === r.strategy)?.name ?? r.strategy;
          const favorite = isFavorite(r);
          return <tr key={`${r.symbol}-${r.strategy}`}><td>
            <button type="button" className="research-favorite" aria-pressed={favorite}
              aria-label={`Favorite ${coin} / ${strategyName}`}
              title={favorite ? 'Remove from favorites' : 'Add to favorites'}
              onClick={() => toggleFavorite(favoriteId(coin, r.strategy))}>
              <span aria-hidden="true">{favorite ? '★' : '☆'}</span>
            </button>
          </td><td>{r.symbol ?? job.symbol}</td><th scope="row">{catalog.find(c => c.id === r.strategy)?.name ?? r.strategy}</th><td title={r.timeframe ? "Timeframe recorded for this result" : "Legacy result: timeframe from the current strategy definition"}>{timeframe ?? "Unknown"}</td><td>{r.trades}</td><td>{r.tp_hits}</td><td>{r.sl_hits}</td><td>{r.forced_exits}</td><td>{r.session_exits ?? 0}</td><td className={gross > 0 ? "research-profit" : gross < 0 ? "research-loss" : ""}>{gross > 0 ? "+" : ""}{gross.toFixed(2)}</td><td>{r.fees.toFixed(2)}</td><td className={r.net > 0 ? "research-profit" : r.net < 0 ? "research-loss" : ""}>{r.net > 0 ? "+" : ""}{r.net.toFixed(2)}</td><td><a href={`/api/research/runs/${r.run_id}`} target="_blank" rel="noreferrer">Saved report (JSON)</a></td><td>
            {catalog.find(c => c.id === r.strategy)?.backtest_only ? <span className="research-helper">Backtest only</span> : favoriteStrategies.has(r.strategy) ? <span className="research-helper">{disabled ? 'Favorited · already disabled in Strategy Monitor' : 'Favorited · protected from disabling here'}</span> : <button disabled={disabled || pendingDisable || !connected || state?.mode !== 'testnet' || !strategyState}
              aria-label={`Disable ${r.strategy} from ${r.symbol ?? job.symbol} backtest`}
              onClick={() => void disableStrategy(r.strategy)}>
              {disabled ? 'Disabled' : pendingDisable ? 'Waiting for engine?' : 'Disable strategy'}
            </button>}
            {!!requestedAt && !disabled && !pendingDisable && <small role="status">Not confirmed. Check Strategy Monitor before retrying.</small>}
          </td></tr>; })}</tbody></table></div>
    </div>}
    <h3>Confirmed setups now</h3>
    <p>Scan current markets for one strategy, then choose a coin to backtest. Scanner rank measures setup quality, not expected profit. Account limits and order checks still apply before trading.</p>
    <label>Scan strategy <select disabled={scanning} value={scanStrategy} onChange={e => { setScanStrategy(e.target.value); setScan(undefined); }}>{catalog.filter(c => !c.backtest_only).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>{' '}
    <button disabled={scanning || !catalog.length} onClick={async () => {
      setScanning(true); setScanError(''); setScan(undefined);
      try { setScan(await jsonResponse(await fetch(`/api/scan?strategy=${encodeURIComponent(scanStrategy)}`))); }
      catch (cause) { setScanError(String(cause)); } finally { setScanning(false); }
    }}>{scanning ? 'Scanning…' : 'Find confirmed setups'}</button>
    {scanError && <p role="alert">{scanError}</p>}
    {scan && <><p>{scan.stats?.universe_size ?? 0} markets checked. {ready.length} fresh confirmed setups. {scan.error ?? ''}</p>
      {!ready.length && <p>No fresh confirmed setups. Refresh the scan; no coin is labelled ready from historical results alone.</p>}
      <div className="research-table-scroll"><table><thead><tr><th>Coin</th><th>Setup rank score</th><th>Action</th></tr></thead><tbody>{ready.map(c => <tr key={c.symbol}><td>{c.symbol}</td><td>{c.score.toFixed(2)}</td><td><button disabled={busy} onClick={() => { setCoinMode('single'); setSymbol(c.symbol); setSelected([scanStrategy]); }}>Backtest {c.symbol}</button></td></tr>)}</tbody></table></div>
    </>}
    <p>Jobs continue while you change tabs. A server restart interrupts unfinished jobs; completed reports and candle cache remain saved. Missing historical data fails visibly rather than silently shortening a year.</p>
  </section>;
}
