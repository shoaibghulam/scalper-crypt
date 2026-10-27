import { memo, useState } from "react";
import type { ClosedTrade, History as HistoryData, HistorySummary } from "../lib/api";
import { formatPrice, formatUsd, pnlGlyph, pnlTone } from "../lib/format";
import "./History.css";

/**
 * Every trade that has finished, and what they add up to.
 *
 * Profit factor and expectancy lead; win rate is placed after them and given a
 * quieter treatment. That ordering is the point of the tab. In the cost-aware
 * backtests behind this platform, fourteen of seventy-eight mean-reversion
 * configurations won at least 65 percent of their trades and still lost money
 * over the sample, the worst returning minus 12.5 percent at a 68 percent win
 * rate. A screen that leads with win rate rewards the wrong instinct: cutting
 * winners early raises it while destroying expectancy.
 *
 * Fees get their own figure rather than being folded silently into the net. At
 * a one to five minute holding period the round trip is a large fraction of the
 * edge, and an operator who cannot see that number cannot tell an unprofitable
 * strategy from a profitable one traded too often.
 */

interface Props {
  history: HistoryData | undefined;
}

/** Null means undefined, not zero, and is rendered as such. */
function metric(value: number | null, decimals: number, suffix = ""): string {
  if (value === null) return "—";
  return value.toFixed(decimals) + suffix;
}

function duration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function closedAt(timestamp: number): string {
  return new Date(timestamp * 1000).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function localDate(timestamp: number): string {
  const date = new Date(timestamp * 1000);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function summarize(trades: ClosedTrade[]): HistorySummary {
  const verified = trades.filter(t => t.verified !== false);
  const wins = verified.filter(t => t.realised > 0);
  const losses = verified.filter(t => t.realised < 0);
  const net = verified.reduce((sum, t) => sum + t.realised, 0);
  const loss = -losses.reduce((sum, t) => sum + t.realised, 0);
  return {
    trades: verified.length, wins: wins.length, losses: losses.length, net,
    fees: verified.reduce((sum, t) => sum + t.fees, 0),
    win_rate: verified.length ? wins.length / verified.length * 100 : null,
    expectancy: verified.length ? net / verified.length : null,
    profit_factor: loss ? wins.reduce((sum, t) => sum + t.realised, 0) / loss : null,
    tp_hits: verified.filter(t => t.outcome === "tp").length,
    sl_hits: verified.filter(t => t.outcome === "sl").length,
  };
}

/**
 * How profit factor reads.
 *
 * Below 1.0 the strategy loses money. The 1.3 floor is the threshold this
 * platform uses as its own validation gate, and it is only meaningful over a
 * few hundred trades: a green badge over nine of them means nothing, which is
 * why the sample size sits directly beneath it.
 */
function profitFactorBand(value: number | null): string {
  if (value === null) return "none";
  if (value < 1) return "bad";
  if (value < 1.3) return "thin";
  return "good";
}

function Summary({ summary }: { summary: HistorySummary }) {
  const gross = summary.net + summary.fees;
  // What share of the gross edge the exchange took. Only meaningful when the
  // strategy actually made something before costs.
  const feeShare = gross > 0 ? (summary.fees / gross) * 100 : null;
  const thinSample = summary.trades > 0 && summary.trades < 30;
  const unclassified = Math.max(0, summary.trades - summary.tp_hits - summary.sl_hits);

  return (
    <div className="hist-summary">
      <section className="hist-metric hist-metric-lead">
        <h3>Profit factor</h3>
        <p
          className="hist-metric-value"
          data-band={profitFactorBand(summary.profit_factor)}
        >
          {metric(summary.profit_factor, 2)}
        </p>
        <p className="hist-metric-sub">
          {summary.trades} trade{summary.trades === 1 ? "" : "s"}
          {thinSample ? " · too few to judge" : ""}
        </p>
      </section>

      <section className="hist-metric hist-metric-lead">
        <h3>Expectancy</h3>
        <p
          className="hist-metric-value"
          data-tone={
            summary.expectancy === null ? undefined : pnlTone(summary.expectancy)
          }
        >
          {summary.expectancy === null ? "—" : formatUsd(summary.expectancy)}
        </p>
        <p className="hist-metric-sub">per trade, net of fees; excludes funding</p>
      </section>

      <section className="hist-metric">
        <h3>Net</h3>
        <p className="hist-metric-value" data-tone={pnlTone(summary.net)}>
          {formatUsd(summary.net)}
        </p>
        <p className="hist-metric-sub">{formatUsd(gross)} before fees</p>
      </section>

      <section className="hist-metric">
        <h3>Fees paid</h3>
        <p className="hist-metric-value hist-metric-cost">{formatUsd(-summary.fees)}</p>
        <p className="hist-metric-sub">
          {feeShare === null
            ? "no gross profit to measure against"
            : `${feeShare.toFixed(0)}% of gross profit`}
        </p>
      </section>

      <section className="hist-metric hist-metric-quiet">
        <h3>Win rate</h3>
        <p className="hist-metric-value">{metric(summary.win_rate, 0, "%")}</p>
        <p className="hist-metric-sub">
          {summary.wins}W / {summary.losses}L {"·"} not a measure of edge
        </p>
      </section>

      <section className="hist-metric hist-metric-quiet">
        <h3>Exits</h3>
        <p className="hist-metric-value">
          {summary.trades} closed
        </p>
        <p className="hist-metric-sub">{summary.tp_hits} target / {summary.sl_hits} stop / {unclassified} unclassified</p>
        {unclassified > 0 && <p className="hist-metric-sub">Unclassified: exit order type not verified; P&amp;L remains recorded.</p>}
      </section>
    </div>
  );
}

function outcomeLabel(outcome: string | null): string {
  if (outcome === "tp") return "target";
  if (outcome === "sl") return "stop";
  return "unclassified";
}

function Row({ trade }: { trade: ClosedTrade }) {
  const tone = pnlTone(trade.realised);
  const decimals = trade.entry < 10 ? 4 : 2;

  return (
    <tr>
      <td className="num hist-time"><time dateTime={new Date(trade.closed_at * 1000).toISOString()}>
        <span className="hist-date">{localDate(trade.closed_at)}</span>{closedAt(trade.closed_at)}
      </time></td>
      <td>
        <div className="cell-symbol">{trade.symbol}</div>
        <div className="cell-bot">{trade.bot}{trade.verified === false ? " ? Unverified estimate; excluded from totals" : ""}</div>
      </td>
      <td>
        <span className="side" data-side={trade.side}>
          <span aria-hidden="true">{trade.side === "LONG" ? "▲" : "▼"}</span>{" "}
          {trade.side}
        </span>
        <span className="leverage num">{trade.leverage}x</span>
      </td>
      <td className="col-num num">
        {formatPrice(trade.entry, decimals)}
        <span className="hist-exit">
          {"→"} {formatPrice(trade.exit_price, decimals)}
        </span>
      </td>
      <td className="col-num num">{duration(trade.duration_seconds)}</td>
      <td>
        {/* Shown as unknown rather than guessed. An exit that matched neither
            level was closed by something else, most often by hand, and filing
            it under stops would corrupt the exit counts above. */}
        <span className="hist-outcome" data-outcome={trade.outcome ?? "other"}>
          {outcomeLabel(trade.outcome)}
        </span>
      </td>
      <td className="col-num num hist-fee">{formatUsd(-trade.fees)}</td>
      <td className="col-num num pnl" data-tone={tone}>
        <span aria-hidden="true">{pnlGlyph(trade.realised)}</span>{" "}
        {formatUsd(trade.realised)}
        <span className="pnl-roe">
          {trade.roe_pct > 0 ? "+" : ""}
          {trade.roe_pct.toFixed(1)}% ROE
        </span>
      </td>
    </tr>
  );
}

function HistoryInner({ history }: Props) {
  const [search, setSearch] = useState("");
  const [strategy, setStrategy] = useState("");
  const [result, setResult] = useState("");
  const [side, setSide] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  if (!history) {
    return (
      <section className="history">
        <p className="skeleton">Waiting for the engine to report.</p>
      </section>
    );
  }

  const invalidDates = Boolean(from && to && from > to);
  const filtered = Boolean(search || strategy || result || side || from || to);
  const trades = history.trades.filter(t => {
    const date = localDate(t.closed_at);
    return !invalidDates && (!from || date >= from) && (!to || date <= to)
      && (!strategy || t.strategy === strategy) && (!side || t.side === side)
      && (!search || `${t.symbol} ${t.bot}`.toLowerCase().includes(search.trim().toLowerCase()))
      && (!result || (t.verified !== false && (result === "win" ? t.realised > 0
        : result === "loss" ? t.realised < 0 : t.realised === 0)));
  });
  const summary = filtered ? summarize(trades) : history.summary;
  const strategies = [...new Set(history.trades.map(t => t.strategy))].sort();

  return (
    <section className="history">
      <div className="hist-head">
        <h2>History</h2>
        {!history.persistent && (
          // Said plainly. An empty table after a restart otherwise reads as
          // "this strategy has never traded", which is a different and far more
          // encouraging claim than "the record was lost".
          <span className="hist-warning">
            In memory only {"—"} cleared when the engine restarts
          </span>
        )}
      </div>

      <div className="hist-filters" role="group" aria-label="History filters">
        <label>Search symbol or bot<input type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder="e.g. BTCUSDT" /></label>
        <label>Strategy<select value={strategy} onChange={e => setStrategy(e.target.value)}>
          <option value="">All strategies</option>{strategies.map(s => <option key={s} value={s}>{s}</option>)}
        </select></label>
        <label>Result<select value={result} onChange={e => setResult(e.target.value)}>
          <option value="">All results</option><option value="win">Wins</option><option value="loss">Losses</option><option value="flat">Break-even</option>
        </select></label>
        <label>Side<select value={side} onChange={e => setSide(e.target.value)}>
          <option value="">Both sides</option><option value="LONG">Long</option><option value="SHORT">Short</option>
        </select></label>
        <label>From date<input type="date" value={from} onChange={e => setFrom(e.target.value)} /></label>
        <label>To date<input type="date" value={to} onChange={e => setTo(e.target.value)} /></label>
        <button type="button" disabled={!filtered} onClick={() => {
          setSearch(""); setStrategy(""); setResult(""); setSide(""); setFrom(""); setTo("");
        }}>Clear filters</button>
      </div>
      <p className="hist-filter-note" role="status">Showing {trades.length} of {history.trades.length} loaded trades.
        Dates use your local timezone ({Intl.DateTimeFormat().resolvedOptions().timeZone}).
        {filtered ? " Summary reflects filtered verified trades." : " Summary shows all saved verified trades."}</p>
      {invalidDates && <p role="alert">From date must be on or before To date.</p>}
      {history.trades.length === 0 ? (
        <div className="empty">
          <h3>No closed trades yet</h3>
          <p>
            A trade appears here once its exchange fills are verified, whether it
            hit its target, its stop, or was closed by hand. Open positions stay
            on the Overview tab until then.
          </p>
        </div>
      ) : (
        <>
          <Summary summary={summary} />
          {trades.length === 0 && <p role="status">No trades match these filters.</p>}
          <table>
            <thead>
              <tr>
                <th scope="col">Closed</th>
                <th scope="col">Symbol</th>
                <th scope="col">Side</th>
                <th scope="col" className="col-num">
                  Entry {"→"} Exit
                </th>
                <th scope="col" className="col-num">
                  Held
                </th>
                <th scope="col">Exit</th>
                <th scope="col" className="col-num">
                  Fees
                </th>
                <th scope="col" className="col-num">
                  Net
                </th>
              </tr>
            </thead>
            <tbody>
              {trades.map((trade) => (
                <Row key={`${trade.symbol}-${trade.closed_at}`} trade={trade} />
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}

// Memoised deliberately. App runs three independent one-second timers (health,
// state, and the clock for age displays) and a re-render of App re-invokes
// every child. Without this, a tick that changed nothing this component
// displays still re-runs its whole row map.
export const History = memo(HistoryInner);
