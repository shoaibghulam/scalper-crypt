import type { AppState } from "../lib/api";
import { AnnualResearch } from "./AnnualResearch";
import { useEffect, useRef, useState } from "react";
import "./CreateScalp.css";
import "./Research.css";
import { ResearchInsights, type Diagnostics } from "./ResearchInsights";
import { verdict } from "../lib/research";

type Result = {
  period: number; stress: boolean; start: number; end: number;
  net: number; trade_count: number; win_rate_pct: number | null;
  expectancy: number | null; profit_factor: number | null;
  closed_equity_drawdown_pct: number; total_fees: number;
  rejected_entries: number; unfilled_entries: number;
  tp_hits?: number; sl_hits?: number;
  ambiguous_bars?: number; forced_exits?: number; session_exits?: number; max_consecutive_losses?: number;
  equity_curve?: { at: number; equity: number }[];
};
type Report = { results: Result[]; limitations: string[]; dataset_sha256: string;
  diagnostics?: Diagnostics; run_id?: string; saved_at?: number;
  config: { strategy: string; capital: number; leverage: number } };
type Candidate = { id: string; name: string; summary: string; timeframe: string;
  source?: string; research_only: boolean; backtest_only?: boolean; warmup_candles?: number; assumptions?: string[] };
type HistoryData = { csv: string; symbol: string; candles: number; start: number; end: number;
  source: string; dataset_sha256: string; minimum_notional: number; quantity_step: number;
  minimum_quantity: number; note: string };
type Trial = { id: string; report?: Report; error?: string };
const defaults = { capital: 10, leverage: 3, fee_bps: 5, slippage_bps: 2,
  spread_bps: 2, minimum_notional: 5, quantity_step: 0.001, risk_pct: 1, minimum_trades: 30 };
const labels: Record<keyof typeof defaults, string> = {
  capital: "Starting capital (USDT)", leverage: "Leverage",
  fee_bps: "Fee per side (bps)", slippage_bps: "Slippage per side (bps)",
  spread_bps: "Full spread (bps)", minimum_notional: "Minimum order notional (USDT)",
  quantity_step: "Quantity step (coin units)",
  risk_pct: "Planned risk per trade (%)", minimum_trades: "Minimum development trades",
};
const fmt = (value: number | null) => value === null ? "N/A" : value.toFixed(2);

export function Research({ state, connected = false }: { state?: AppState; connected?: boolean }) {
  const [strategy, setStrategy] = useState("ema-trend-pullback");
  const [config, setConfig] = useState(defaults);
  const [dataSource, setDataSource] = useState("automatic");
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [candleCount, setCandleCount] = useState(4320);
  const [endTime, setEndTime] = useState("");
  const [showValidation, setShowValidation] = useState(false);
  const [savedRuns, setSavedRuns] = useState<{ id: string; saved_at: number; strategy: string; dataset_label: string }[]>([]);
  const [archiveError, setArchiveError] = useState("");
  const [symbols, setSymbols] = useState<string[]>([]);
  const [symbolError, setSymbolError] = useState("");
  const [dataset, setDataset] = useState<HistoryData>();
  const [file, setFile] = useState<File>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [report, setReport] = useState<Report>();
  const [catalog, setCatalog] = useState<Candidate[]>([]);
  const [catalogError, setCatalogError] = useState("");
  const [trials, setTrials] = useState<Trial[]>([]);
  const [progress, setProgress] = useState("");
  const [manifest, setManifest] = useState<{ started_at: string; strategies: string[]; file: string; dataset?: Omit<HistoryData, "csv"> }>();
  const stopped = useRef(false);
  async function refreshArchive() {
    try {
      const response = await fetch("/api/research/runs");
      if (!response.ok) throw new Error("Cannot load saved experiments.");
      const rows = await response.json();
      if (!Array.isArray(rows)) throw new Error("Invalid experiment archive response.");
      setSavedRuns(rows); setArchiveError("");
    } catch { setArchiveError("Saved experiments unavailable. Current results can still be downloaded."); }
  }
  useEffect(() => {
    let active = true;
    void fetch("/api/research/runs").then(async response => {
      if (!response.ok) throw new Error("Archive unavailable");
      const rows = await response.json();
      if (!Array.isArray(rows)) throw new Error("Invalid archive response");
      if (active) setSavedRuns(rows);
    }).catch(() => { if (active) setArchiveError("Saved experiments unavailable. Current results can still be downloaded."); });
    void fetch("/api/research/catalog").then(async response => {
      if (!response.ok) throw new Error("Cannot load the strategy library. Refresh to retry.");
      const entries = await response.json() as Candidate[];
      if (!Array.isArray(entries)) throw new Error("Invalid strategy catalog response.");
      if (active) setCatalog(entries);
    }).catch(cause => { if (active) setCatalogError(String(cause)); });
    void fetch("/api/research/symbols").then(async response => {
      if (!response.ok) throw new Error("Coin list unavailable; enter a USDT symbol or retry later.");
      const body = await response.json();
      if (!Array.isArray(body.symbols)) throw new Error("Cannot load coin list.");
      if (active) setSymbols(body.symbols);
    }).catch(cause => { if (active) setSymbolError(String(cause)); });
    return () => { active = false; stopped.current = true; };
  }, []);
  const candidate = catalog.find(c => c.id === strategy);
  async function run() {
    if (dataSource === "csv" && !file) return;
    setBusy(true); setError(""); setReport(undefined); setTrials([]); setDataset(undefined);
    setShowValidation(false);
    setProgress(""); setManifest(undefined);
    stopped.current = false;
    try {
      let csv: string;
      let fileName: string;
      let economics = { ...config, minimum_quantity: 0 };
      let provenance: Omit<HistoryData, "csv"> | undefined;
      if (dataSource === "automatic") {
        setProgress(`Downloading closed candles for ${symbol}...`);
        const response = await fetch("/api/research/history", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ symbol: symbol.trim().toUpperCase(), candles: candleCount,
            ...(endTime ? { end_time: Date.parse(`${endTime}Z`) } : {}) }),
        });
        const body = await response.json();
        if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Historical data download failed.");
        const history = body as HistoryData;
        csv = history.csv; fileName = `${history.symbol}-${history.start}.csv`;
        const { csv: _csv, ...metadata } = history;
        void _csv;
        provenance = metadata; setDataset(history);
        economics = { ...config, minimum_notional: history.minimum_notional,
          quantity_step: history.quantity_step, minimum_quantity: history.minimum_quantity };
        setConfig({ ...config, minimum_notional: history.minimum_notional, quantity_step: history.quantity_step });
      } else {
        if (!file) return;
        if (file.size > 2_000_000) throw new Error("CSV must be at most 2 MB.");
        csv = await file.text(); fileName = file.name;
      }
      if (stopped.current) { setProgress("Stopped before backtesting."); return; }
      const ids = strategy === "all-lab" ? catalog.filter(c => c.id.startsWith("lab-")).map(c => c.id)
        : strategy === "all-minute" ? catalog.filter(c => c.id.startsWith("minute-")).map(c => c.id)
        : strategy === "all-combinations" ? catalog.filter(c => c.id.startsWith("user-")).map(c => c.id)
        : strategy === "all-library" ? catalog.map(c => c.id) : [strategy];
      const warmup = Math.max(500, ...catalog.filter(c => ids.includes(c.id))
        .map(c => c.warmup_candles ?? (c.id === "user-20" ? 780 : 500)));
      if (!ids.length) throw new Error("Load the strategy library before running a comparison.");
      setManifest({ started_at: new Date().toISOString(), strategies: ids, file: fileName, dataset: provenance });
      const completed: Trial[] = [];
      for (const [index, id] of ids.entries()) {
        if (stopped.current) break;
        setProgress(`Testing ${index + 1}/${ids.length}: ${id}`);
        let response: Response;
        try { response = await fetch("/api/research/backtest", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...economics, strategy: id, csv, warmup, dataset_label: provenance?.symbol ?? fileName }),
      }); } catch (cause) {
        completed.push({ id, error: "Connection lost; experiment completion is unknown." });
        setTrials([...completed]); throw cause;
      }
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        const message = typeof body?.detail === "string" ? body.detail :
          `Research failed (${response.status}). Check your inputs and CSV.`;
        completed.push({ id, error: message }); setTrials([...completed]);
        throw new Error(message);
      }
      const result = await response.json() as Report;
      completed.push({ id, report: result }); setTrials([...completed]); setReport(result);
      }
      setProgress(`${completed.length}/${ids.length} experiments completed${stopped.current ? " · stopped" : ""}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setProgress("Evaluation stopped because of an error. Completed trials are preserved.");
    }
    finally { setBusy(false); void refreshArchive(); }
  }
  function download() {
    const url = URL.createObjectURL(new Blob([JSON.stringify({ manifest, trials, progress }, null, 2)],
      { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url; link.download = "scalper-research.json"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <section className="scan" aria-label="Backtest and Research">
    <h2>Backtest &amp; Research</h2>
    <AnnualResearch catalog={catalog} symbols={symbols} state={state} connected={connected} />
    <h3>Detailed short-window evaluation</h3>
    <p>Evaluate fixed strategy rules on historical candles. This does not place trades or change your bots.</p>
    <p>20 additional one-minute candidates + 50 original candidates + 19 requested combinations, video strategies, and existing candle strategies.
      Candidate numbers are identifiers, not rankings. None is certified profitable.
      Added research strategies are available in testnet Create Scalp and Auto Trade.</p>
    <p>Requested #18 is the existing Order-Flow Pullback and needs event data; it cannot run on candle CSVs.
      Comparisons use the longest required warm-up for every candidate. Funded Price Action needs at least 1,960 rows; #20 needs 1,480.</p>
    {catalogError && <p role="alert">{catalogError}</p>}
    <p>Choose a coin to download real Binance Futures history automatically, without API keys or a CSV.
      This uses public live-market prices even when trading is on testnet. CSV upload is also available.</p>
    {dataSource === "csv" && <p>Upload one coin's consecutive closed 1-minute candles: 1,200–10,000 rows, up to 2 MB.
      CSV columns: <code>open_time,open,high,low,close,volume</code>.
      Use UTC Unix milliseconds for open_time. Five-minute candles are built automatically.</p>}
    <form onSubmit={e => { e.preventDefault(); void run(); }}>
      <fieldset disabled={busy}><legend>Experiment settings</legend>
        <div className="scan-actions">
          <div className="field"><label htmlFor="research-source">Historical data</label>
            <select id="research-source" value={dataSource} onChange={e => setDataSource(e.target.value)}>
              <option value="automatic">Automatic coin history</option><option value="csv">Upload CSV</option>
            </select></div>
          {dataSource === "automatic" && <>
            <div className="field"><label htmlFor="research-end">History ends at (UTC, optional)</label>
              <input id="research-end" type="datetime-local" value={endTime} onChange={e => setEndTime(e.target.value)} /></div>
            <div className="field"><label htmlFor="research-symbol">Coin</label>
              <input id="research-symbol" list="research-coins" required value={symbol}
                onChange={e => setSymbol(e.target.value.toUpperCase())} />
              <datalist id="research-coins">{symbols.map(s => <option key={s} value={s} />)}</datalist></div>
            <div className="field"><label htmlFor="research-period">History period (includes warm-up)</label>
              <select id="research-period" value={candleCount} onChange={e => setCandleCount(Number(e.target.value))}>
                <option value={2000}>Latest 2,000 minutes</option><option value={4320}>Latest 3 days</option>
                <option value={8640}>Latest 6 days</option><option value={10000}>Latest 10,000 minutes</option>
              </select></div>
          </>}
          <div className="field"><label htmlFor="research-strategy">Strategy</label>
            <select id="research-strategy" value={strategy} onChange={e => setStrategy(e.target.value)}>
              <option value="ema-trend-pullback">EMA Trend Pullback · 1m entry</option>
              <option value="rolling-breakout-retest">Rolling Breakout and Retest · 5m entry</option>
              <optgroup label="Compare on identical data and costs">
                <option value="all-minute" disabled={!catalog.length}>Compare 20 additional one-minute strategies</option>
                <option value="all-lab" disabled={!catalog.length}>Compare original 50 research candidates</option>
                <option value="all-combinations" disabled={!catalog.length}>Compare 19 requested candle combinations</option>
                <option value="all-library" disabled={!catalog.length}>Compare library including existing strategies</option>
              </optgroup>
              <optgroup label="Individual research candidates and other baselines">
                {catalog.filter(c => !["ema-trend-pullback", "rolling-breakout-retest"].includes(c.id))
                  .map(c => <option key={c.id} value={c.id}>{c.id} · {c.name} · {c.timeframe}</option>)}
              </optgroup>
            </select></div>
          {(Object.keys(defaults) as (keyof typeof defaults)[]).map(key => <div className="field" key={key}>
            <label htmlFor={`research-${key}`}>{labels[key]}</label>
            <input id={`research-${key}`} type="number" required disabled={dataSource === "automatic" && ["minimum_notional", "quantity_step"].includes(key)} step={["leverage", "minimum_trades"].includes(key) ? 1 : "any"}
              min={key === "minimum_trades" ? 10 : key === "leverage" ? 1 :
                ["fee_bps", "slippage_bps", "spread_bps"].includes(key) ? 0 : 0.00000001}
              max={key === "leverage" ? 10 : key === "risk_pct" ? 5 : key === "minimum_trades" ? 500 : undefined} value={config[key]}
              onChange={e => setConfig({ ...config, [key]: Number(e.target.value) })} />
          </div>)}
          {dataSource === "csv" && <div className="field"><label htmlFor="research-file">Historical CSV</label>
            <input id="research-file" type="file" accept=".csv,text/csv" required
              onChange={e => setFile(e.target.files?.[0])} /></div>}
          <button className="btn-primary" disabled={dataSource === "csv" ? !file : !symbol.trim()}>Run chronological evaluation</button>
        </div>
      </fieldset>
    </form>
    {dataSource === "automatic" && <p>Order minimum and quantity step load automatically for the selected coin.
      {symbolError && ` ${symbolError}`}</p>}
    {dataset && <p>Loaded {dataset.candles} closed candles for {dataset.symbol} from {dataset.source}:
      {" "}{new Date(dataset.start).toISOString()} to {new Date(dataset.end).toISOString()}. {dataset.note}
      <button onClick={() => {
        const url = URL.createObjectURL(new Blob([dataset.csv], { type: "text/csv" }));
        const link = document.createElement("a"); link.href = url;
        link.download = `${dataset.symbol}-${dataset.start}.csv`; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }}>Download candles CSV</button></p>}
    {candidate && <p>{candidate.summary} {candidate.source &&
      <a href={candidate.source} target="_blank" rel="noreferrer">Read source concept</a>}
      {candidate.backtest_only ? " · Backtest only; use the long-range form for the required history. See the strategy assumptions below." : candidate.research_only && candidate.id !== "funded-price-action" && " · Our crypto adaptation uses a 1.5 ATR stop (0.45% floor) and 2R target."}</p>}
    {candidate?.id === "funded-price-action" && <ul>{candidate.assumptions?.map(item =>
      <li key={item}>{item}</li>)}</ul>}
    <p>Verify order minimums and quantity step for your coin. Sizing uses your planned risk percentage and up to
      50% margin. Defaults are assumptions. A small account may produce no eligible trades.</p>
    <p>At least 500 candles provide history (780 with the 15m combination; 1,260 with Funded Price Action); the remainder is divided into three separate periods.
      Periods 1–2 are development data; period 3 is reserved for validation and excluded from ranking.
      Each period also runs with double spread and slippage. No parameters are fitted.
      Repeatedly tuning against these periods makes them unsuitable as untouched test data.</p>
    {progress && <p role="status">{progress}</p>}
    {busy && <button onClick={() => { stopped.current = true; setProgress("Stopping after the current experiment…"); }}>
      Stop after current experiment</button>}
    {error && <p role="alert">{error}</p>}
    {trials.length > 0 && <>
      <h3>Comparison results</h3>
      <p>Development base and stressed net values sum the first two independent periods with equal starting capital.
        This is not a portfolio simulation. Save all trials, including losses. Selecting the best of
        50 trials can select luck; validate shortlisted rules on new dates and coins.
        Completed experiments are saved in SQLite and can be reopened below.</p>
      <button onClick={download}>Download all experiments</button>
      <p>Ranking uses only development stressed net (periods 1–2), never the validation result. No automatic promotion to trading.</p>
      <button disabled={busy} onClick={() => setShowValidation(v => !v)}>{showValidation ? "Hide validation results" : "Reveal validation results"}</button>
      {showValidation && <p>Validation has now been inspected. Use a new end date or new coin for the next independent check; this is not untouched data after tuning.</p>}
      <div style={{ overflowX: "auto" }}><table><caption>Development ranking — period 3 excluded</caption>
        <thead><tr>{["Candidate", "Development trades", "Development net", "Development stressed net",
          "Positive stress periods", "Evidence", ...(showValidation ? ["Validation net / stress"] : []), "Details"].map(label => <th scope="col" key={label}>{label}</th>)}</tr></thead>
        <tbody>{[...trials].sort((a, b) =>
          (b.report?.diagnostics?.development_stress.net ?? -Infinity)
          - (a.report?.diagnostics?.development_stress.net ?? -Infinity)).map(trial => {
          const base = trial.report?.results.filter(r => r.period < 3 && !r.stress) ?? [];
          const stress = trial.report?.results.filter(r => r.period < 3 && r.stress) ?? [];
          const evidence = trial.report?.diagnostics;
          return <tr key={trial.id}><th scope="row">{catalog.find(c => c.id === trial.id)?.name ?? trial.id}</th>
            {trial.error ? <td colSpan={showValidation ? 7 : 6}>{trial.error}</td> : <>
              <td>{base.reduce((sum, r) => sum + r.trade_count, 0)}</td>
              <td>{fmt(base.reduce((sum, r) => sum + r.net, 0))}</td>
              <td>{fmt(stress.reduce((sum, r) => sum + r.net, 0))}</td>
              <td>{stress.filter(r => r.net > 0).length}/{stress.length}</td>
              <td>{evidence ? verdict[evidence.status] : "Legacy report"}</td>
              {showValidation && <td>{evidence ? `${fmt(evidence.holdout.net)} / ${fmt(evidence.holdout_stress.net)}` : "N/A"}</td>}
              <td><button onClick={() => setReport(trial.report)}>View {trial.id}</button></td>
            </>}</tr>;
        })}</tbody></table></div>
    </>}
    {report && <>
      <h3>Completed experiment</h3>
      <ResearchInsights diagnostics={report.diagnostics} curves={report.results} />
      {report.run_id && <p>Saved experiment: {report.run_id}</p>}
      <p>{report.config.strategy} · {report.config.capital} USDT starting capital ·
        {report.config.leverage}x leverage. Results reflect settings at run time.</p>
      <button onClick={download}>Download full report and trades</button>
      <div style={{ overflowX: "auto" }}><table>
        <caption>Independent periods — capital resets for each row; do not add percentage returns.</caption>
        <thead><tr>{["Period (UTC)", "Costs", "Trades", "Net USDT", "Win %", "Avg net/trade",
          "Profit factor", "Closed drawdown %", "Fees", "Rejected", "Unfilled", "TP hits", "SL hits", "Ambiguous bars", "Forced exits", "Session-end exits", "Longest loss streak"].map(x => <th key={x} scope="col">{x}</th>)}</tr></thead>
        <tbody>{report.results.filter(r => r.period < 3 || showValidation).map(r => <tr key={`${r.period}-${r.stress}`}>
          <th scope="row">{r.period}: {new Date(r.start).toISOString()} – {new Date(r.end).toISOString()}</th>
          <td>{r.stress ? "Stress" : "Base"}</td><td>{r.trade_count}</td><td>{fmt(r.net)}</td>
          <td>{fmt(r.win_rate_pct)}</td><td>{fmt(r.expectancy)}</td><td>{fmt(r.profit_factor)}</td>
          <td>{fmt(r.closed_equity_drawdown_pct)}</td><td>{fmt(r.total_fees)}</td>
          <td>{r.rejected_entries}</td><td>{r.unfilled_entries}</td>
          <td>{r.tp_hits ?? "?"}</td><td>{r.sl_hits ?? "?"}</td>
          <td>{r.ambiguous_bars ?? "—"}</td><td>{r.forced_exits ?? "—"}</td><td>{r.session_exits ?? 0}</td><td>{r.max_consecutive_losses ?? "—"}</td>
        </tr>)}</tbody>
      </table></div>
      <p>N/A means the metric is undefined; no losses does not establish a reliable profit factor.
        Few or zero trades provide insufficient evidence of an edge.</p>
      <ul>{report.limitations.map(item => <li key={item}>{item}</li>)}</ul>
      <p style={{ overflowWrap: "anywhere" }}>Dataset SHA-256: {report.dataset_sha256}</p>
    </>}
    <details className="research-archive"><summary>Saved experiments ({savedRuns.length} most recent)</summary>
      <button disabled={busy} onClick={() => void refreshArchive()}>Refresh saved experiments</button>
      {archiveError && <p role="status">{archiveError}</p>}
      <ul>{savedRuns.map(run => <li key={run.id}>
        {new Date(run.saved_at * 1000).toLocaleString()} · {run.dataset_label} · {run.strategy}{" "}
        <button disabled={busy} onClick={async () => {
          try {
            const response = await fetch(`/api/research/runs/${encodeURIComponent(run.id)}`);
            if (!response.ok) throw new Error("Cannot load this saved experiment.");
            const restored = await response.json() as Report;
            setReport(restored); setTrials([{ id: restored.config.strategy, report: restored }]);
            setManifest(undefined); setDataset(undefined); setShowValidation(false); setError("");
            setProgress("Loaded saved experiment. Settings shown in the report are the original settings.");
          } catch { setArchiveError("Cannot load this saved experiment. Retry later."); }
        }}>Open {run.strategy}</button>
      </li>)}</ul>
    </details>
  </section>;
}
