import { useEffect, useState } from 'react';

type CacheCoin = { symbol: string; files: number; bytes: number; first: number; last: number };
type Inventory = { coins: CacheCoin[]; files: number; bytes: number };
const mb = (bytes: number) => `${(bytes / 1048576).toFixed(1)} MiB`;
export function SavedMarketData({ symbol, refreshKey }: { symbol: string; refreshKey?: string }) {
  const [inventory, setInventory] = useState<Inventory>();
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    void fetch('/api/research/cache').then(async response => {
      if (!response.ok) throw new Error('Cache inventory unavailable');
      const body = await response.json();
      if (!Array.isArray(body.coins) || !Number.isFinite(body.bytes)) throw new Error('Invalid inventory');
      if (active) { setInventory(body); setError(''); }
    }).catch(() => { if (active) setError('Storage details unavailable. Refresh to retry; saved files have not been deleted.'); });
    return () => { active = false; };
  }, [refresh, refreshKey]);
  const coin = inventory?.coins.find(item => item.symbol === symbol);
  return <aside className="research-storage" aria-label="Saved market data">
    <div className="research-section-heading"><div><span className="research-eyebrow">LOCAL DATA LIBRARY</span><h4>Download once. Test more strategies.</h4></div>
      <button type="button" onClick={() => setRefresh(n => n + 1)}>Refresh storage</button></div>
    <p>Long-range candles stay saved on this computer after testing. Matching cached days are reused; only missing days or new date boundaries need downloading.</p>
    {error ? <p role="status">{error}</p> : inventory ? <>
      <div className="research-storage-stats"><span><b>{inventory.coins.length}</b> coins cached</span><span><b>{inventory.files.toLocaleString()}</b> files retained</span><span><b>{mb(inventory.bytes)}</b> stored locally</span></div>
      <p className="research-cache-coin"><strong>{symbol}</strong> {coin ? `${coin.files} cached files · ${mb(coin.bytes)} · ${new Date(coin.first).toISOString().slice(0, 10)} to ${new Date(coin.last).toISOString().slice(0, 10)} UTC` : 'No saved long-range candles yet. They will be saved when downloaded.'}</p>
      <small>Date spans describe saved chunks, not guaranteed continuous coverage. Each backtest checks completeness.</small>
    </> : <p role="status">Loading saved data…</p>}
  </aside>;
}
