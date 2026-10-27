import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { createBot, type AppState } from '../lib/api';
import type { FavoritePair } from '../hooks/useFavorites';
import { BotFleet } from './BotFleet';

export function FavoriteTrade({ pair, name, state, connected, available, onClose }: {
  pair: FavoritePair; name: string; state?: AppState; connected: boolean; available: boolean; onClose: () => void;
}) {
  const [amount, setAmount] = useState('10');
  const [leverage, setLeverage] = useState('3');
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState('');
  const query = useQueryClient();
  const bots = (state?.bots ?? []).filter(bot => bot.strategy === pair.strategy && bot.symbols.includes(pair.symbol));
  const active = bots.some(bot => bot.state === 'running' || bot.open_positions > 0 || bot.entry_pending || bot.pending_closures);
  const valid = Number.isFinite(Number(amount)) && Number(amount) > 0 && Number(amount) <= 100000
    && Number.isInteger(Number(leverage)) && Number(leverage) >= 1 && Number(leverage) <= (state?.max_leverage ?? 10);
  const canStart = connected && available && state?.trading_enabled && state.credentials?.valid && state.credentials.can_trade
    && valid && !busy && !sent && !active;
  return <section className="research-job-panel" aria-label="Trade favorite">
    <h3>Trade {pair.symbol} / {name}</h3>
    <p>Watch this coin for a confirmed entry using this strategy. The bot keeps searching after trades close until you stop it.</p>
    <form onSubmit={async event => {
      event.preventDefault();
      if (!canStart) return;
      setBusy(true); setError('');
      try {
        await createBot({ symbols: [pair.symbol], strategy: pair.strategy, amount, leverage: Number(leverage), continuous: true, margin_mode: 'ISOLATED' });
        setSent(true);
        void query.invalidateQueries({ queryKey: ['state'] });
      } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
      finally { setBusy(false); }
    }}>
      <div className="research-settings-grid">
        <label>Amount (USDT)<input type="number" min="0.01" max="100000" step="0.01" required value={amount} disabled={busy || sent || active} onChange={event => setAmount(event.target.value)} /></label>
        <label>Leverage<input type="number" min="1" max={state?.max_leverage ?? 10} step="1" required value={leverage} disabled={busy || sent || active} onChange={event => setLeverage(event.target.value)} /></label>
      </div>
      <p className="research-helper">Isolated margin. Amount is a sizing budget; account risk limits and exchange minimums still apply.</p>
      <button type="submit" disabled={!canStart}>{busy ? 'Sending…' : 'Start searching for a trade'}</button>
      <button type="button" disabled={busy} onClick={onClose}>Close setup</button>
    </form>
    {!connected && <p role="status">Waiting for the engine connection.</p>}
    {connected && (!state?.trading_enabled || !state.credentials?.valid || !state.credentials.can_trade) && <p role="status">Resolve the account alerts in Overview before starting.</p>}
    {!available && <p role="status">This strategy is unavailable for trading in the current environment.</p>}
    {active && <p role="status">A bot already watches or manages this coin / strategy pair.</p>}
    {sent && <p role="status">Request sent. Waiting for the engine result below. Closing this setup does not stop the bot.</p>}
    {sent && state?.last_action?.kind === 'create_bot' && <p role={state.last_action.ok ? 'status' : 'alert'}>Latest bot request result: {state.last_action.detail}</p>}
    {error && <p role="alert">{error}</p>}
    {bots.length > 0 && <BotFleet bots={bots} />}
  </section>;
}
