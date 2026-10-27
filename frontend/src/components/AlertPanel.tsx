import { memo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Alert, LastAction } from "../lib/api";
import { adoptPositions } from "../lib/api";
import "./AlertPanel.css";

/**
 * What is stopping the engine, with the button that fixes it.
 *
 * The orphan warning used to be a dead end. It said to adopt or close four
 * positions and gave no way to adopt them, so the only route back to trading
 * was closing real positions by hand on Binance. A blocking message with no
 * action is an instruction to go somewhere else.
 *
 * Rendered only when non-empty. A panel that permanently reads "no alerts"
 * trains the eye to skip the place where a real one will appear.
 */

interface Props {
  alerts: Alert[];
  lastAction?: LastAction | null;
}

/**
 * Whether the exchange has already told us these positions carry no stop.
 *
 * Plain adopt can then never succeed, so continuing to offer it as the
 * primary control is how a working refusal gets reported as a broken button.
 */
function refusedForMissingStops(lastAction?: LastAction | null): boolean {
  return (
    lastAction?.kind === "adopt" &&
    lastAction.ok === false &&
    /no stop/i.test(lastAction.detail ?? "")
  );
}

function AdoptAction({ symbols, lastAction }: {
  symbols: string[];
  lastAction?: LastAction | null;
}) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function adopt(protect: boolean) {
    setBusy(true);
    setError(null);
    try {
      await adoptPositions({ symbols, name: "Adopted positions", protect });
      // The engine reports the outcome in its next snapshot. It reads the
      // protective orders back from the exchange and refuses anything it
      // cannot manage, so claiming success from the acknowledgement alone
      // would be claiming more than the acknowledgement means.
      await queryClient.invalidateQueries({ queryKey: ["state"] });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  const noStops = refusedForMissingStops(lastAction);

  return (
    <div className="alert-action">
      {/* Primary, because it is the one that works on an unprotected
          position - which is what actually reaches this screen when another
          bot opens trades without stops. It places a real order, so it says
          so in the label. */}
      <button
        type="button"
        className="btn-primary"
        onClick={() => adopt(true)}
        disabled={busy}
      >
        {busy ? "Adopting..." : "Adopt and attach a stop"}
      </button>
      {/* Plain adopt only READS existing protection. Disabled once the
          exchange has told us there is none, because clicking it again
          cannot produce a different answer. */}
      <button
        type="button"
        className="btn-ghost"
        onClick={() => adopt(false)}
        disabled={busy || noStops}
        title={
          noStops
            ? "These positions carry no stop on the exchange, so adopting them as-is is impossible."
            : undefined
        }
      >
        {`Adopt ${symbols.length} position${symbols.length === 1 ? "" : "s"} as-is`}
      </button>
      <p className="alert-note">
        {error ??
          (noStops
            ? "The exchange holds no stop for these positions, so adopting them " +
              "as-is is refused - recording protection that does not exist is " +
              "worse than leaving them listed. Use Adopt and attach a stop: it " +
              "places a real protective order, sized from volatility, then adopts."
            : "Adopt as-is reads the stop and target back from the exchange and " +
              "refuses any position that has none. Attach a stop places a real " +
              "protective order, sized from volatility, then adopts.")}
      </p>
    </div>
  );
}

/**
 * What the engine did about the last thing that was clicked.
 *
 * Rendered whether it succeeded or not. The failure case is the one that
 * matters: an adopt refused for a missing stop used to leave the screen
 * unchanged, which is indistinguishable from a button that does nothing.
 */
function ActionResult({ action }: { action: LastAction }) {
  return (
    <p className="action-result" data-ok={action.ok} role="status">
      <span aria-hidden="true">{action.ok ? "\u2713" : "\u2717"}</span>{" "}
      {action.detail}
    </p>
  );
}

function AlertPanelInner({ alerts, lastAction }: Props) {
  if (alerts.length === 0 && !lastAction) return null;

  if (alerts.length === 0) {
    return lastAction ? <ActionResult action={lastAction} /> : null;
  }

  return (
    <>
      {/* Above the alerts, and for EVERY kind of command. It used to render
          only inside the orphan alert and only for an adopt, so a refused bot
          creation - the exact thing the orphan was blocking - was invisible. */}
      {lastAction && <ActionResult action={lastAction} />}
      <ul className="alerts">
      {alerts.map((alert) => (
        <li key={alert.id} data-severity={alert.severity}>
          <span className="alert-message">{alert.message}</span>
          {alert.id === "orphans" && alert.symbols && alert.symbols.length > 0 && (
            <AdoptAction symbols={alert.symbols} lastAction={lastAction} />
          )}
        </li>
        ))}
      </ul>
    </>
  );
}

// Memoised deliberately. App runs three independent one-second timers (health,
// state, and the clock for age displays) and a re-render of App re-invokes
// every child.
export const AlertPanel = memo(AlertPanelInner);
