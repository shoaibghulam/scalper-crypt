import { memo } from "react";
import type { Totals } from "../lib/api";
import { formatUsd, pnlGlyph, pnlTone } from "../lib/format";
import "./KpiTiles.css";

/**
 * The six numbers that answer the only two questions this screen exists for:
 * is anything about to go wrong, and how much am I up or down.
 *
 * Unrealised and realised are never merged. They are computed differently by
 * the exchange, one from mark price and one from last, and a combined figure
 * hides which of the two moved.
 *
 * Dollars lead and return on equity follows in a smaller, quieter treatment.
 * Return on equity alone is leverage-amplified, so a trivial price move reads
 * as a heroic percentage; dollars alone hide how close the position is to
 * liquidation. Both, in that order.
 */

interface Props {
  totals: Totals;
}

function Tile({
  label,
  value,
  tone,
  sub,
  children,
}: {
  label: string;
  value: string;
  tone?: "pos" | "neg" | "flat";
  sub?: string;
  children?: React.ReactNode;
}) {
  return (
    <section className="kpi">
      <h2 className="kpi-label">{label}</h2>
      <p className="kpi-value" data-tone={tone}>
        {tone && tone !== "flat" && (
          <span className="kpi-glyph" aria-hidden="true">
            {pnlGlyph(tone === "pos" ? 1 : -1)}
          </span>
        )}
        {value}
      </p>
      {sub && <p className="kpi-sub">{sub}</p>}
      {children}
    </section>
  );
}

function KpiTilesInner({ totals }: Props) {
  const unrealised = Number(totals.unrealised_pnl);
  const realised = Number(totals.realised_today);
  const equity = Number(totals.equity ?? "0");
  const balanceKnown = totals.balance_known !== false;
  const available = Number(totals.available ?? "0");
  const marginUsed = Number(totals.margin_used ?? "0");
  // What share of the account is committed. The figure that says whether the
  // next trade has room, which a wallet total on its own does not.
  const utilisation = equity > 0 ? (marginUsed / equity) * 100 : 0;
  const botTotal = totals.bots_running + totals.bots_stopped + totals.bots_error;
  const hits = totals.tp_hits_today + totals.sl_hits_today;
  const hitRate = hits > 0 ? Math.round((totals.tp_hits_today / hits) * 100) : null;

  return (
    <div className="kpis">
      {/* Capital first. Every other figure on this strip is a fraction of it,
          and a 2 USDT gain means something different on a 20 USDT account
          than on a 20000 USDT one. */}
      <Tile
        label="Equity"
        value={balanceKnown ? formatUsd(equity) : "Unavailable"}
        sub={balanceKnown ? `${formatUsd(available)} free · ${utilisation.toFixed(0)}% in use` : "Waiting for an account balance refresh"}
      >
        <div className="kpi-segments" aria-hidden="true">
          {marginUsed > 0 && (
            <span className="kpi-segment" data-kind="used" style={{ flex: marginUsed }} />
          )}
          {available > 0 && (
            <span className="kpi-segment" data-kind="free" style={{ flex: available }} />
          )}
        </div>
        {balanceKnown && <p className="visually-hidden">
          {formatUsd(marginUsed)} posted as margin, {formatUsd(available)} available.
        </p>}
      </Tile>
      <Tile
        label="Unrealised"
        value={formatUsd(unrealised)}
        tone={pnlTone(unrealised)}
        sub="open positions, at mark"
      />
      <Tile
        label="Realised today (UTC)"
        value={formatUsd(realised)}
        tone={pnlTone(realised)}
        sub="net of fees"
      />
      <Tile label="Bots" value={String(botTotal)} sub={`${totals.bots_running} running`}>
        <div className="kpi-segments" aria-hidden="true">
          {totals.bots_running > 0 && (
            <span
              className="kpi-segment"
              data-kind="running"
              style={{ flex: totals.bots_running }}
            />
          )}
          {totals.bots_stopped > 0 && (
            <span
              className="kpi-segment"
              data-kind="stopped"
              style={{ flex: totals.bots_stopped }}
            />
          )}
          {totals.bots_error > 0 && (
            <span
              className="kpi-segment"
              data-kind="error"
              style={{ flex: totals.bots_error }}
            />
          )}
        </div>
        <p className="visually-hidden">
          {totals.bots_running} running, {totals.bots_stopped} stopped,{" "}
          {totals.bots_error} in error.
        </p>
      </Tile>
      <Tile
        label="Open positions"
        value={String(totals.open_positions)}
        sub="across all bots"
      />
      <Tile
        label="Take profits"
        value={String(totals.tp_hits_today)}
        sub={`${totals.sl_hits_today} stops${hitRate === null ? "" : ` \u00B7 ${hitRate}% hit rate`}`}
      />
      {/* An Exposure tile belongs here, but the engine does not report total
          notional or weighted leverage yet. A tile permanently reading zero on
          a risk dashboard is a false safety signal, and worse than an absent
          one because it looks like an answered question. It returns when the
          engine supplies the figure. */}
    </div>
  );
}


// Memoised deliberately. App runs three independent one-second timers
// (health, state, and the clock for age displays) and a re-render of App
// re-invokes every child. Without this, a tick that changed nothing this
// component displays still re-runs its whole row map.
export const KpiTiles = memo(KpiTilesInner);
