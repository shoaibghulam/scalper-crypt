import type { ExternalPosition } from "../lib/api";
import "./ExternalPositions.css";

/**
 * Positions on this account that this engine did not open.
 *
 * An operator may run a second bot - a CLI one - or place a trade by hand
 * against the same Binance account. Reconciliation already finds those and
 * deliberately leaves them alone, but it only ever reported them as a list of
 * symbols inside a blocking alert, so the one screen that shows every open
 * position was the one place they could not be seen.
 *
 * Read-only by construction. There is no close button and no adopt button
 * here: this engine has no record of why the position was opened, and a
 * control that acts on one would be acting blind. Adoption stays where it
 * belongs, in the alert that explains what adopting means.
 */

const money = (value: number) => `${value >= 0 ? "+" : "-"}${Math.abs(value).toFixed(2)}`;

export function ExternalPositions({ positions }: { positions: ExternalPosition[] }) {
  // Nothing at all rather than an empty table: on a normal account this
  // panel should be invisible.
  if (!positions.length) return null;

  return (
    <section className="external-positions" aria-label="Positions opened outside this engine">
      <div className="external-heading">
        <h3>Other positions on this account</h3>
        <span className="external-note">
          Opened by another bot or by hand &mdash; not managed by this engine, and shown
          read-only.
        </span>
      </div>
      <div className="external-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">Coin</th>
              <th scope="col">Side</th>
              <th scope="col">Size</th>
              <th scope="col">Entry</th>
              <th scope="col">Mark</th>
              <th scope="col">Unrealised</th>
              <th scope="col">Liquidation</th>
            </tr>
          </thead>
          <tbody>
            {positions.map(position => (
              <tr key={position.symbol}>
                <th scope="row">{position.symbol}</th>
                <td data-side={position.side}>{position.side}</td>
                <td>{position.quantity}</td>
                <td>{position.entry_price}</td>
                {/* A dash, never a zero: the venue not reporting a mark is
                    not the same as the position being flat. */}
                <td>{position.mark_price ?? "—"}</td>
                <td
                  className={
                    position.unrealised == null
                      ? undefined
                      : position.unrealised >= 0
                        ? "external-profit"
                        : "external-loss"
                  }
                >
                  {position.unrealised == null ? "—" : money(position.unrealised)}
                </td>
                <td>{position.liquidation_price ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
