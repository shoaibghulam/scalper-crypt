import { useState } from "react";
import { verdict } from "../lib/research";

type Metrics = { trades: number; net: number; win_rate: number | null; expectancy: number | null;
  profit_factor: number | null; worst_period_drawdown_pct: number; positive_periods: number; periods: number };
export type Diagnostics = { development: Metrics; development_stress: Metrics; holdout: Metrics;
  holdout_stress: Metrics; status: string; minimum_trades: number; ranking_basis: string; status_note: string };

export function ResearchInsights({ diagnostics, curves }: { diagnostics?: Diagnostics;
  curves: { period: number; stress: boolean; equity_curve?: { at: number; equity: number }[] }[] }) {
  const [period, setPeriod] = useState(1);
  const points = curves.find(r => r.period === period && !r.stress)?.equity_curve ?? [];
  if (!diagnostics) return null;
  const values = points.map(p => p.equity);
  const low = Math.min(...values), high = Math.max(...values);
  const span = high - low || 1;
  const first = points[0]?.at ?? 0, last = points.at(-1)?.at ?? first;
  const path = points.map(p => `${20 + 660 * (p.at - first) / (last - first || 1)},${150 - 120 * (p.equity - low) / span}`).join(" ");
  return <section className="research-insights" aria-label="Research diagnostics">
    <h3>{verdict[diagnostics.status] ?? diagnostics.status}</h3>
    <p>{diagnostics.ranking_basis} Minimum sample: {diagnostics.minimum_trades} trades in each development cost scenario.</p>
    <p>{diagnostics.status_note}</p>
    <div className="research-cards">
      <div><strong>{diagnostics.development.trades}</strong><span>Development trades</span></div>
      <div><strong>{diagnostics.development.net.toFixed(2)} USDT</strong><span>Development net</span></div>
      <div><strong>{diagnostics.development_stress.net.toFixed(2)} USDT</strong><span>Development stressed net</span></div>
      <div><strong>{diagnostics.development_stress.worst_period_drawdown_pct.toFixed(2)}%</strong><span>Worst period closed-equity drawdown (stress)</span></div>
    </div>
    <label>Equity period <select value={period} onChange={e => setPeriod(Number(e.target.value))}>
      <option value={1}>Development 1</option><option value={2}>Development 2</option>
    </select></label>
    {points.length > 0 && <figure>
      <svg viewBox="0 0 700 180" role="img" aria-label={`Period ${period} closed-equity curve; ${points.length - 1} closed trades`}>
        <line x1="20" y1="150" x2="680" y2="150" stroke="currentColor" opacity="0.3" />
        <polyline points={path} fill="none" stroke="currentColor" strokeWidth="2" />
        <text x="20" y="175" fontSize="12">{new Date(first).toISOString().slice(0, 16)}</text>
        <text x="680" y="175" textAnchor="end" fontSize="12">{new Date(last).toISOString().slice(0, 16)}</text>
      </svg>
      <figcaption>Closed equity: {low.toFixed(2)}–{high.toFixed(2)} USDT. UTC time; unrealized drawdown is not included.</figcaption>
    </figure>}
  </section>;
}
