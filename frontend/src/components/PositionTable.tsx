import { memo } from "react";
import type { Position } from "../lib/api";
import { formatPrice, formatUsd, pnlGlyph, pnlTone } from "../lib/format";
import { TpSlBar } from "./TpSlBar";
import "./PositionTable.css";

/**
 * Every open position across every bot, in one flat table.
 *
 * Sorted by age, deliberately, and never by profit. A table that re-sorts as
 * numbers move means the row you are reaching for is somewhere else by the time
 * you click, which in an interface with a close-position button is worse than
 * merely annoying.
 *
 * A real table element, with real header cells. The data is tabular, and a grid
 * of divs would announce as nothing to a screen reader.
 */

interface Props {
  positions: Position[];
  engineConnected: boolean;
}

function EmptyState({ engineConnected }: { engineConnected: boolean }) {
  if (!engineConnected) {
    // The supervisor starts and restarts the engine, so this is a real fault
    // rather than something the operator forgot to do.
    return (
      <div className="empty">
        <h3>Engine not reporting</h3>
        <p>
          The supervisor starts the engine automatically and restarts it if it
          dies, so this means it is failing rather than waiting to be launched.
          Check the terminal running <code>python -m scalper</code>.
        </p>
      </div>
    );
  }

  return (
    <div className="empty">
      <h3>No open positions</h3>
      <p>
        No managed positions are currently reported. Check account alerts and
        use Create Scalp to review confirmed strategy signals.
      </p>
    </div>
  );
}

function PositionTableInner({ positions, engineConnected }: Props) {
  return (
    <section className="positions">
      <div className="positions-head">
        <h2>Positions</h2>
        <span className="positions-count">{positions.length} open</span>
      </div>

      {positions.length === 0 ? (
        <EmptyState engineConnected={engineConnected} />
      ) : (
        <table>
          <thead>
            <tr>
              <th scope="col">Symbol</th>
              <th scope="col">Side</th>
              <th scope="col" title="Strategy entry candles, not trade duration">Timeframe</th>
              <th scope="col" className="col-num">
                Entry
              </th>
              <th scope="col" className="col-num">
                Size
              </th>
              <th scope="col">Stop to target</th>
              <th scope="col" className="col-num">
                Unrealised
              </th>
              <th scope="col">Stop</th>
              <th scope="col">Target</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => {
              const tone = pnlTone(p.unrealised_pnl);
              return (
                <tr key={p.id}>
                  <td>
                    <div className="cell-symbol">{p.symbol}</div>
                    <div className="cell-bot">{p.bot}</div>
                  </td>
                  <td>
                    <span className="side" data-side={p.side}>
                      <span aria-hidden="true">
                        {p.side === "LONG" ? "\u25B2" : "\u25BC"}
                      </span>{" "}
                      {p.side}
                    </span>
                    <span className="leverage num">{p.leverage}x</span>
                  </td>
                  <td className="num" title="Strategy entry candles, not trade duration">{p.timeframe ?? "Unknown"}</td>
                  <td className="col-num num">
                    {formatPrice(p.entry, p.price_decimals)}
                  </td>
                  <td className="col-num num">{formatUsd(p.notional)}</td>
                  <td>
                    <TpSlBar
                      side={p.side}
                      entry={p.entry}
                      stop={p.stop}
                      target={p.target}
                      mark={p.mark}
                      liquidation={p.liquidation ?? undefined}
                      decimals={p.price_decimals}
                      live={engineConnected}
                    />
                  </td>
                  <td className="col-num num pnl" data-tone={tone}>
                    <span aria-hidden="true">{pnlGlyph(p.unrealised_pnl)}</span>{" "}
                    {formatUsd(p.unrealised_pnl)}
                    <span className="pnl-roe">
                      {p.roe_pct > 0 ? "+" : ""}
                      {p.roe_pct.toFixed(1)}% ROE
                    </span>
                  </td>
                  <td>
                    {p.stop_armed ? (
                      <span className="num" title="Protective order held by the exchange">
                        {"\u2713"} armed
                      </span>
                    ) : (
                      // If this ever renders, the position is unprotected and
                      // a crash or disconnect means unbounded downside.
                      <span className="unarmed">{"\u2717"} NO STOP</span>
                    )}
                  </td>
                  <td>{p.target_armed ? "Target armed" : "Target not verified"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}


// Memoised deliberately. App runs three independent one-second timers
// (health, state, and the clock for age displays) and a re-render of App
// re-invokes every child. Without this, a tick that changed nothing this
// component displays still re-runs its whole row map.
export const PositionTable = memo(PositionTableInner);
