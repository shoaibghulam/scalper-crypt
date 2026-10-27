import type { AppState } from '../lib/api';
import { useState } from 'react';
import { FavoriteTrade } from './FavoriteTrade';
import { favoriteId, useFavorites, type FavoritePair } from '../hooks/useFavorites';
import { useStrategies } from '../hooks/useScan';
import './Research.css';

export function Favorites({ state, connected = false }: { state?: AppState; connected?: boolean }) {
  const [tradePair, setTradePair] = useState<FavoritePair>();
  const { pairs, toggleFavorite, error } = useFavorites();
  const catalog = useStrategies();
  const tradeStrategy = catalog.data?.strategies.find(item => item.id === tradePair?.strategy);
  const tradeAvailable = !!tradeStrategy && !tradeStrategy.backtest_only && !catalog.isError
    && state?.strategy_monitor?.rows.find(item => item.id === tradePair?.strategy)?.disabled !== true
    && (!(tradeStrategy.testnet_only || tradeStrategy.research_only) || state?.mode === 'testnet');
  return <section className="research-insights annual-research" aria-label="Favorite strategies and coins">
    <h2>Favorite strategies &amp; coins</h2>
    <p>Your saved coin / strategy pairs from all backtests, stored on the server and shared by every browser.</p>
    <p>Click Trade, enter your amount and leverage, then start searching for an entry on that exact coin and strategy.</p>
    {tradePair && <FavoriteTrade key={favoriteId(tradePair.symbol, tradePair.strategy)} pair={tradePair} name={tradeStrategy?.name ?? tradePair.strategy} state={state} connected={connected} available={tradeAvailable} onClose={() => setTradePair(undefined)} />}
    {error && <p role="alert">{error}</p>}
    {catalog.isError && <p role="alert">Could not load trading strategies. Favorites remain saved; reload to try again.</p>}
    {!pairs.length ? <p> No favorites yet. Star a coin / strategy result in Backtest &amp; Research to save it here.</p>
      : <div className="research-table-scroll"><table>
        <caption>{pairs.length} saved coin / strategy pairs</caption>
        <thead><tr><th scope="col">Coin</th><th scope="col">Strategy</th><th scope="col">Status</th><th scope="col">Actions</th></tr></thead>
        <tbody>{pairs.map(pair => {
          const strategy = catalog.data?.strategies.find(item => item.id === pair.strategy);
          const disabled = state?.strategy_monitor?.rows.find(item => item.id === pair.strategy)?.disabled === true;
          const testnetOnly = strategy?.testnet_only || strategy?.research_only;
          const unavailable = !strategy || strategy.backtest_only || catalog.isError || disabled || (testnetOnly && state?.mode !== 'testnet');
          const status = catalog.isError ? 'Catalog unavailable' : !catalog.data ? 'Loading strategy'
            : !strategy ? 'Strategy unavailable' : strategy.backtest_only ? 'Backtest only' : disabled ? 'Disabled in Strategy Monitor'
            : testnetOnly ? 'Testnet only' : 'Available';
          return <tr key={favoriteId(pair.symbol, pair.strategy)}>
            <th scope="row">{pair.symbol}</th><td>{strategy?.name ?? pair.strategy}</td><td>{status}</td>
            <td><div className="research-coin-actions">
              <button type="button" disabled={unavailable} aria-label={`Trade ${pair.symbol} / ${strategy?.name ?? pair.strategy}`} onClick={() => setTradePair(pair)}>Trade</button>
              <button type="button" aria-label={`Remove favorite ${pair.symbol} / ${strategy?.name ?? pair.strategy}`} onClick={() => toggleFavorite(favoriteId(pair.symbol, pair.strategy))}>Remove favorite</button>
            </div></td>
          </tr>;
        })}</tbody>
      </table></div>}
  </section>;
}
