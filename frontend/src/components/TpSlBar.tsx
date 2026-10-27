import type React from "react";
import { barGeometry, type Side } from "../lib/tpsl";
import { formatPct, formatPrice } from "../lib/format";
import "./TpSlBar.css";

/**
 * How close is this position to being closed, and in which direction.
 *
 * The track spans the true price distance from stop to target, so the risk and
 * reward zones are drawn at their real relative widths. Centring entry would
 * make every position look like an even-money bet.
 *
 * The marker follows mark price, because that is the price the exchange uses
 * for liquidation and unrealised profit. Plotting last price would put this bar
 * subtly out of step with the venue that is going to close the position.
 *
 * The graphic itself is hidden from assistive technology: every value it
 * encodes is also present as text in the same row, and a screen reader
 * announcing a decorative track adds nothing.
 */

interface Props {
  side: Side;
  entry: number;
  stop: number;
  target: number;
  mark: number;
  liquidation?: number;
  decimals: number;
  live?: boolean;
}

export function TpSlBar({ side, entry, stop, target, mark, liquidation, decimals, live = true }: Props) {
  const g = barGeometry({ side, entry, stop, target, mark, liquidation });
  const phase = g.markPosRaw <= 0 ? "stop-reached" : g.markPosRaw >= 100 ? "target-reached"
    : g.progressPct <= -80 ? "near-stop" : g.progressPct >= 80 ? "near-target" : "tracking";
  const status = !live ? "Price feed stale" : phase === "stop-reached"
    ? "Stop level reached · awaiting fill confirmation"
    : phase === "target-reached" ? "Target level reached · awaiting fill confirmation"
    : phase === "near-stop" ? "Approaching stop loss"
    : phase === "near-target" ? "Approaching take profit" : "Tracking mark price";

  return (
    <div className="tpsl" data-phase={phase} data-live={live}>
      <div className="tpsl-endpoints" aria-hidden="true"><span>SL</span><span>TP</span></div>
      <div className="tpsl-track" aria-hidden="true">
        <div className="tpsl-zone-risk" style={{ width: `${g.entryPos}%` }} />
        <div className="tpsl-zone-reward" style={{ width: `${100 - g.entryPos}%` }} />
        <span className="tpsl-entry" style={{ left: `${g.entryPos}%` }} />
        {g.liquidationPos !== null && (
          <span
            className="tpsl-liq"
            data-inside={g.liquidationInsideStop}
            style={{ left: `${g.liquidationPos}%` }}
          />
        )}
        <span className="tpsl-travel" data-profit={g.progressPct >= 0} style={{
          left: `${Math.min(g.entryPos, g.markPos)}%`,
          width: `${Math.abs(g.markPos - g.entryPos)}%`,
        }} />
        {/* Percent transforms resolve against this full-track-width carrier,
            not the tiny dot's own width. Pointer and dot move together. */}
        <span
          className="tpsl-carrier"
          data-nearer={g.nearer}
          style={{ "--pos": g.markPos } as React.CSSProperties}
        ><span key={phase} className="tpsl-mark" /><span className="tpsl-pointer" /></span>
      </div>
      <p className="tpsl-status" aria-live="polite" aria-atomic="true">{status}</p>

      {/* Stated in words as well as drawn. Reading a caret position against an
          unlabelled track is a guess, and the whole question this row answers
          is which level is about to close the position. */}
      <p className="tpsl-nearer" data-nearer={g.nearer}>
        <span aria-hidden="true">{g.nearer === "stop" ? "▼" : "▲"}</span>{" "}
        {g.nearerPct.toFixed(2)}% to {g.nearer}
        <span className="tpsl-progress num">
          {g.progressPct > 0 ? "+" : ""}
          {g.progressPct.toFixed(0)}%
        </span>
      </p>

      <div className="tpsl-scale num">
        <span className="tpsl-toward-stop">{formatPct(g.toStopPct)}</span>
        <span>{formatPrice(mark, decimals)}</span>
        <span className="tpsl-toward-target">{formatPct(g.toTargetPct)}</span>
      </div>

      {g.liquidationInsideStop && (
        // The most important warning in the application. The stop cannot fire:
        // the exchange reaches liquidation first and closes the position at a
        // worse price, with the maintenance margin forfeited.
        <p className="tpsl-warning">
          <span aria-hidden="true">{"\u26A0"}</span>
          LIQUIDATION INSIDE STOP
        </p>
      )}

      <p className="visually-hidden">
        {side} position. Entry {formatPrice(entry, decimals)}, mark{" "}
        {formatPrice(mark, decimals)}. Nearer the {g.nearer}, {g.nearerPct.toFixed(2)}{" "}
        percent away. {formatPct(g.toTargetPct)} to target,{" "}
        {formatPct(g.toStopPct)} to stop. Reward to risk {g.riskReward.toFixed(2)}.
        {g.liquidationInsideStop
          ? " Warning: liquidation would be reached before the stop-loss."
          : ""}
      </p>
    </div>
  );
}
