import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { configureSingleCoin, type AppState } from "../lib/api";
import { BotFleet } from "./BotFleet";
import { PositionTable } from "./PositionTable";
import "./CreateScalp.css";

export function SingleCoin({ state, connected }: { state?: AppState; connected: boolean }) {
  const saved = state?.single_coin;
  const [amount, setAmount] = useState(saved?.amount ?? "10");
  const [leverage, setLeverage] = useState(String(saved?.leverage ?? 3));
  const [timeframe, setTimeframe] = useState<"1m" | "5m" | "10m">(saved?.timeframe ?? "1m");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const query = useQueryClient();
  const searching = saved?.enabled ?? false;
  const bot = state?.bots.find(b => b.id === saved?.bot_id);
  const occupied = Boolean(bot && (bot.open_positions > 0 || bot.entry_pending || bot.pending_closures));
  const valid = Number.isFinite(Number(amount)) && Number(amount) > 0 && Number(amount) <= 100000
    && Number(leverage) <= (state?.max_leverage ?? 10);
  const ranking = (state?.strategy_monitor?.rows ?? []).filter(r =>
    r.single_coin_timeframe === timeframe && !r.disabled && !r.flagged && r.trades >= 1 && r.net > 0)
    .sort((a, b) => b.wins - a.wins || (b.win_rate ?? 0) - (a.win_rate ?? 0) || b.trades - a.trades || a.id.localeCompare(b.id));
  const chosen = searching || occupied ? saved?.selection : ranking[0];
  const canStart = ranking.length > 0 && connected && state?.mode === "testnet" && state.trading_enabled && valid && !occupied;
  async function send(stop: boolean) {
    setBusy(true); setError(""); setMessage("");
    try {
      await configureSingleCoin(amount, Number(leverage), timeframe, stop);
      setMessage("Request sent; the engine confirms the result below.");
      await query.invalidateQueries({ queryKey: ["state"] });
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }
  return <section className="scan" aria-label="Single Coin">
    <h2>Single Coin</h2>
    <p>Find one confirmed setup, open one protected position, then stop searching.
      You choose when to start the next search. No coin or strategy guarantees profit.</p>
    <div className="single-ranking" aria-label="Strategy selection">
      <h3>Top strategy by verified wins</h3>
      <p>Uses only the strategy with the most verified wins with at least one verified closed trade,
        positive net profit, enabled status and no loss-rule flag.
        Matches your entry timeframe. Ties use win rate, then trade count. Profit amount does not determine ranking.</p>
      {chosen ? <p><strong>{chosen.name}</strong> · {chosen.wins ?? "Not recorded"} wins · {chosen.trades} trades · {chosen.win_rate?.toFixed(1)}% win rate
        · {chosen.net.toFixed(2)} USDT net</p>
        : (searching || occupied) && bot ? <p>Current strategy: {bot.strategy}. Ranking applies to your next search.</p>
        : <p role="status">No qualifying strategy yet. Keep collecting results in Auto Trade; Single Coin will become available when a strategy qualifies.</p>}
      <p>Ranked when you start. Historical performance does not guarantee the next trade; a fresh signal is still required.</p>
      {chosen && chosen.trades < 20 && <p role="status">Early testnet leader — only {chosen.trades} verified trades.
        This ranking is provisional while you collect more results.</p>}
    </div>
    <div className="scan-actions">
      <div className="field"><label htmlFor="single-budget">Capital budget (USDT)</label>
        <input id="single-budget" type="number" min="0.01" max="100000" step="0.01"
          value={searching ? saved?.amount ?? amount : amount} disabled={busy || searching || occupied}
          onChange={e => setAmount(e.target.value)} /></div>
      <div className="field"><label htmlFor="single-leverage">Leverage</label>
        <select id="single-leverage" value={searching ? String(saved?.leverage ?? leverage) : leverage}
          disabled={busy || searching || occupied} onChange={e => setLeverage(e.target.value)}>
          {[1, 2, 3, 5, 8, 10].map(x => <option key={x} value={x}
            disabled={x > (state?.max_leverage ?? 10)}>{x}x</option>)}</select></div>
      <div className="field"><label htmlFor="single-timeframe">Entry candles</label>
        <select id="single-timeframe" value={searching ? saved?.timeframe ?? timeframe : timeframe}
          disabled={busy || searching || occupied} onChange={e => setTimeframe(e.target.value as "1m" | "5m" | "10m")}>
          <option value="1m">1 minute · ranked strategies</option>
          <option value="5m">5 minutes · ranked strategies</option><option value="10m">10 minutes - ranked strategies</option></select></div>
      {searching ? <button disabled={busy || !connected} onClick={() => void send(true)}>Stop search</button>
        : <button className="btn-primary" disabled={busy || !canStart}
          onClick={() => void send(false)}>Find one coin and trade</button>}
    </div>
    <p>The budget is a sizing cap, not a deposit or guaranteed maximum loss. Position size is reduced
      to fit your budget and account risk limits, leaving margin for fees. A $10 budget may be too small
      for some exchange order minimums; those setups are skipped. Leverage can amplify losses.</p>
    <p>Testnet only · Isolated margin · All active USDT perpetual markets screened · Each strategy retains its required confirmation candles. Candle size is not the trade's holding time.
      Other manual or Auto Trade bots remain separate and may trade on the same account.</p>
    {!connected && <p role="status">Waiting for the engine connection.</p>}
    {connected && !state?.trading_enabled && <p role="status">Resolve the Overview account alerts first.</p>}
    {occupied && <p role="status">Managing the selected position or completing its settlement.</p>}
    {message && <p role="status">{message}</p>}
    {error && <p role="alert">{error}</p>}
    {state?.last_action?.kind === "single_coin" && <p role={state.last_action.ok ? "status" : "alert"}>
      {state.last_action.detail}</p>}
    {bot && <><BotFleet bots={[bot]} /><PositionTable
      positions={(state?.positions ?? []).filter(p => p.id.startsWith(`${bot.id}-`))}
      engineConnected={connected} /></>}
  </section>;
}
