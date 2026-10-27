import { useState } from "react";
import type { AppState } from "../lib/api";
import "./StrategyMonitor.css";

export type StrategyResult = {
  single_coin_timeframe?: "1m" | "5m" | "10m" | null;
  id: string; name: string; trades: number; wins: number; losses: number; breakeven: number;
  win_rate: number | null; loss_rate: number | null; net: number; fees: number;
  expectancy: number | null; profit_factor: number | null; recent_trades: number;
  recent_losses: number; flagged: boolean; disabled: boolean; reason: string | null; unverified: number;
};
export type StrategyReport = { rows: StrategyResult[]; auto_disable: boolean; persistent: boolean };
const number = (value: number | null) => value === null ? "N/A" : value.toFixed(2);

export function StrategyMonitor({ state, connected }: { state?: AppState; connected: boolean }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [sort, setSort] = useState("net");
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const report = state?.strategy_monitor;
  const all = report?.rows ?? [];
  const disabledCount = all.filter(r => r.disabled).length;
  const reviewCount = all.filter(r => r.flagged && !r.disabled).length;
  const tradeCount = all.reduce((sum, r) => sum + r.trades, 0);
  const totalNet = all.reduce((sum, r) => sum + r.net, 0);
  const rows = all.filter(r => (filter === "all" || (filter === "enabled" && !r.disabled)
    || (filter === "disabled" && r.disabled) || (filter === "review" && r.flagged && !r.disabled))
    && `${r.name} ${r.id}`.toLowerCase().includes(search.trim().toLowerCase())).sort((a, b) => sort === "trades" ? b.trades - a.trades
    : sort === "losses" ? b.losses - a.losses : b.net - a.net);
  async function save(body: object) {
    setBusy(true); setMessage(""); setError("");
    try {
      const response = await fetch("/api/strategy-monitor/settings", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!response.ok) {
        const result = await response.json();
        throw new Error(typeof result.detail === "string" ? result.detail : "Could not update strategy settings");
      }
      setMessage("Request sent. Wait for the engine result and updated status below.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }
  return <section className="strategy-monitor" aria-label="Strategy Monitor">
    <header className="monitor-header"><div><span className="monitor-eyebrow">Experiment dashboard</span>
      <h2>Strategy Monitor</h2><p>Compare results. Keep the evidence. Refine your strategy selection.</p></div>
      <span className={`monitor-badge ${connected ? "positive" : "negative"}`}>
        {connected ? "Engine connected" : "Engine disconnected"}</span></header>
    {!report ? <p role="status">Waiting for strategy statistics from the engine.</p> : <>
      <div className="monitor-cards" aria-label="Strategy totals">
        {[{ id: "all", label: "Total strategies", value: all.length, hint: "Entire strategy library" },
          { id: "enabled", label: "Enabled strategies", value: all.length - disabledCount, hint: "Permitted to start trading" },
          { id: "disabled", label: "Disabled strategies", value: disabledCount, hint: "New entries blocked" },
          { id: "review", label: "Needs review", value: reviewCount, hint: "15+ losses in latest 20" }].map(card =>
          <button key={card.id} className={`monitor-card ${card.id}`} aria-pressed={filter === card.id} aria-label={`${card.label} ${card.value}`}
            onClick={() => setFilter(card.id)}><span>{card.label}</span><strong>{card.value}</strong>
            <small>{card.hint}</small></button>)}
      </div>
      <div className="monitor-summary"><span><b>{tradeCount}</b> verified closed trades</span>
        <span>Net P&amp;L <b className={totalNet < 0 ? "negative" : "positive"}>{number(totalNet)} USDT</b></span>
        <span>{report.persistent ? "History saved" : "History persistence unconfirmed"}</span>
        <span>Enabled means permitted, not necessarily running.</span></div>
      <div className="monitor-rule"><div><h3>Automatic loss rule</h3>
        <p>Disable a strategy after 15 or more losses in its latest 20 verified trades.</p></div>
        <label><input type="checkbox" checked={report.auto_disable}
          disabled={busy || !connected || state?.mode !== "testnet"}
          onChange={e => void save({ auto_disable: e.target.checked })} />
          Auto-disable {report.auto_disable ? "on" : "off"}</label>
      </div>
      <details className="monitor-help"><summary>How results and strategy controls work</summary>
        <p>Wins and losses use net P&amp;L after fees across manual and automatic bots. Break-even trades are separate.
          Unverified records are excluded. Fewer than 20 trades is insufficient evidence for the loss rule.</p>
        <p>Disabling stops new entries and preserves open-position protection and history. Enabling permits future starts;
          stopped bots remain stopped. Turn off auto-disable before enabling a strategy that still meets the rule.</p>
      </details>
      <div className="monitor-toolbar"><label>Search strategies<input type="search" placeholder="Name or strategy ID"
        value={search} onChange={e => setSearch(e.target.value)} /></label>
        <label>Status<select value={filter} onChange={e => setFilter(e.target.value)}>
          <option value="all">All strategies</option><option value="enabled">Enabled</option>
          <option value="disabled">Disabled</option><option value="review">Needs review</option></select></label>
        <label>Sort strategies<select value={sort} onChange={e => setSort(e.target.value)}>
          <option value="net">Net profit</option><option value="trades">Trade count</option><option value="losses">Loss count</option>
        </select></label><span role="status">Showing {rows.length} of {all.length}</span></div>
      <div className="monitor-table-wrap" tabIndex={0} role="region" aria-label="Strategy performance table">
        <table><caption>All-time verified performance ? latest 20 trades for review</caption>
          <thead><tr>{["Strategy", "Status", "Trades", "Wins / losses", "Win rate", "Net USDT",
            "Profit factor", "Recent losses", "Action"].map(x => <th key={x} scope="col">{x}</th>)}</tr></thead>
          <tbody>{rows.map(row => <tr key={row.id}>
            <th scope="row">{row.name}<small>{row.id}</small></th>
            <td><span className={`monitor-badge ${row.disabled ? "muted" : row.flagged ? "negative" : "positive"}`}>
              {row.disabled ? "Disabled" : row.flagged ? "Review" : "Enabled"}</span>
              <small>{row.reason || (row.trades < 20 ? "Collecting evidence" : "Testing")}</small></td>
            <td>{row.trades}<small>{row.unverified} unverified</small></td>
            <td><span className="positive">{row.wins} W</span> / <span className="negative">{row.losses} L</span>
              <small>{row.breakeven} break-even</small></td>
            <td>{number(row.win_rate)}{row.win_rate !== null ? "%" : ""}
              <small>Loss {number(row.loss_rate)}{row.loss_rate !== null ? "%" : ""}</small>
              <meter min={0} max={100} value={row.win_rate ?? 0} aria-label={`${row.name} win rate`} /></td>
            <td><strong className={row.net < 0 ? "negative" : row.net > 0 ? "positive" : ""}>{number(row.net)}</strong>
              <small>Fees {number(row.fees)}</small></td>
            <td>{number(row.profit_factor)}<small>{number(row.expectancy)} / trade</small></td>
            <td>{row.recent_losses}/{row.recent_trades}<small>{row.recent_trades < 20 ? `${20 - row.recent_trades} more to assess` : "Review window complete"}</small></td>
            <td><button className="monitor-action" aria-label={`${row.disabled ? "Enable" : "Disable"} ${row.id}`}
              disabled={busy || !connected || state?.mode !== "testnet"}
              onClick={() => void save({ strategy: row.id, disabled: !row.disabled })}>
              {row.disabled ? "Enable" : "Disable"}</button></td>
          </tr>)}</tbody></table>
        {rows.length === 0 && <p className="monitor-empty">No strategies match these filters.
          <button onClick={() => { setFilter("all"); setSearch(""); }}>Clear filters</button></p>}
      </div>
    </>}
    {message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}
    {state?.last_action?.kind === "strategy_experiment" && <p role={state.last_action.ok ? "status" : "alert"}>{state.last_action.detail}</p>}
    <p className="monitor-footnote">Shared capital and position limits affect trade opportunities. Compare net results as well as win rate.
      Testnet results do not establish live profitability.</p>
  </section>;
}
