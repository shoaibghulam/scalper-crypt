import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { configureAutoTrade, type AppState } from "../lib/api";
import { useStrategies } from "../hooks/useScan";
import { BotFleet } from "./BotFleet";
import "./CreateScalp.css";

export function AutoTrade({ state, connected }: { state?: AppState; connected: boolean }) {
  const [amountDraft, setAmount] = useState<string | null>(null);
  const [leverageDraft, setLeverage] = useState<string | null>(null);
  const amount = amountDraft ?? state?.auto_trade?.amount ?? "100";
  const leverage = leverageDraft ?? String(state?.auto_trade?.leverage ?? 5);
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, []);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const strategies = useStrategies();
  const queries = useQueryClient();
  const auto = state?.auto_trade;
  const enabled = auto?.enabled ?? false;
  const changed = Number(amount) !== Number(auto?.amount)
    || Number(leverage) !== auto?.leverage;
  const cap = state?.max_leverage ?? 10;
  const valid = Number.isFinite(Number(amount)) && Number(amount) > 0 && Number(amount) <= 100000
    && Number(leverage) >= 1 && Number(leverage) <= cap;
  const canStart = connected && state?.mode === "testnet" && state.trading_enabled && valid;
  const owned = (state?.bots ?? []).filter(b => auto?.bot_ids?.includes(b.id));

  const blocked = new Set(state?.strategy_monitor?.rows.filter(row => row.disabled).map(row => row.id));
  const eligible = strategies.data?.strategies.filter(s => !blocked.has(s.id));
  const activeOwned = owned.filter(b => !blocked.has(b.strategy));
  const included = enabled ? eligible?.filter(s => activeOwned.some(b => b.strategy === s.id))
    : eligible;
  const settling = owned.filter(b => b.open_positions > 0 || b.pending_closures || b.entry_pending);

  async function send(stop: boolean) {
    setBusy(true); setError(""); setMessage("");
    try {
      await configureAutoTrade(amount, Number(leverage), stop);
      setMessage(stop ? "Stop requested. Waiting for engine confirmation; any submitted order remains managed."
        : "Request sent. The engine's result appears below.");
      await queries.invalidateQueries({ queryKey: ["state"] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setBusy(false); }
  }

  return <section className="scan" aria-label="Auto Trade">
    <h2>Auto Trade</h2>
    <p>Automatically find entries across all {enabled ? activeOwned.length : eligible?.length ?? "available"} strategy types.
      Each strategy keeps its candle confirmation rules. Entry priority rotates between strategies.
      Two-minute candles are not currently supported. Research candidates are included for testnet testing.</p>
    <p>Check every active Binance USDT perpetual on each scan. There is no top-20 or top-60 coin cap;
      only symbols passing liquidity and spread checks proceed to candle analysis.
      Trades open only on confirmed signals. Candle timeframe does not set trade duration.</p>
    <div className="scan-head">
      <div className="field">
        <label htmlFor="auto-amount">Margin per position (USDT)</label>
        <input id="auto-amount" type="number" min="0.01" max="100000" step="0.01"
          value={amount} disabled={busy}
          onChange={e => setAmount(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="auto-leverage">Leverage</label>
        <select id="auto-leverage" value={leverage}
          disabled={busy}
          onChange={e => setLeverage(e.target.value)}>
          {[1, 2, 3, 5, 8, 10, 15, 20, 25, 50, 100].map(x =>
            <option key={x} value={x} disabled={x > cap}>{x}x</option>)}
        </select>
      </div>
      <button className="btn-primary" disabled={busy || !canStart}
        onClick={() => void send(false)}>{enabled
          ? changed ? "Update all strategies" : "Sync strategies"
          : "Start Auto Trade"}</button>
      {enabled && <button disabled={busy || !connected} onClick={() => void send(true)}
        className="btn-stop">Stop Auto Trade entries</button>}
    </div>
    <p>Use the same settings to add missing enabled strategies without duplicate bots.
      Change the amount or leverage to update future entries across the fleet.
      New coins are discovered on each scan. Open positions retain their original settings;
      individually stopped bots stay stopped during updates.</p>
    <p>Testnet only · Isolated margin · Account leverage cap {cap}x · Up to {state?.max_positions ?? 5} open positions
      across manual and automatic bots. The amount is per position, not a total budget.
      Stopping prevents new entries; existing positions keep their protection.</p>
    {!connected && <p role="status">Waiting for a live engine connection.</p>}
    {connected && !state?.trading_enabled && <p role="status">Trading is disabled. Resolve the Overview alerts first.</p>}
    {enabled && <p role="status">Auto Trade active: {auto?.amount} USDT per position at {auto?.leverage}x.
      {owned.length} strategy bots. Settings persist across engine restarts.</p>}
    {enabled && eligible && eligible.some(s => !activeOwned.some(b => b.strategy === s.id)) &&
      <p role="status">This running fleet has {owned.length} strategy bots. Sync strategies
        to include all {eligible.length} enabled strategies. Existing positions stay protected.</p>}
    {enabled && auto?.coverage && <p role="status">Last scan: {auto.coverage.universe} active USDT perpetuals checked,
      {auto.coverage.eligible} passed liquidity/spread filters.
      {auto.coverage.completed ? " Candle screening completed." : " Candle screening in progress or interrupted."}</p>}
    {settling.length > 0 && <p role="status">{settling.length} strategy bots have open or unsettled trades.
      Restarting preserves their positions and history; those bots wait for settlement before new entries.
      Other strategies can continue.</p>}
    {message && <p role="status">{message}</p>}
    {error && <p role="alert">{error}</p>}
    {state?.last_action?.kind === "auto_trade" &&
      now - state.last_action.at < 60 && <p role={state.last_action.ok ? "status" : "alert"}>
      {state.last_action.detail}</p>}
    {blocked.size > 0 && <p>{blocked.size} strategies are disabled in Strategy Monitor and cannot open new entries.</p>}
    <details><summary>Included strategies</summary><ul>{included?.map(s =>
      <li key={s.id}><strong>{s.name}</strong>: {s.summary}</li>)}</ul></details>
    {owned.length > 0 && <BotFleet bots={owned} />}
  </section>;
}
