import { EngineControls } from "./EngineControls";
import type { Health } from "../lib/api";
import { relativeAge } from "../lib/format";
import "./StatusStrip.css";

/**
 * The trust layer.
 *
 * Two independent indicators, never collapsed into one. Browser-to-API failing
 * means you cannot see the truth. Engine-to-Binance failing means the bot is
 * trading blind. The second is far worse, and a single green dot would hide it
 * behind the first.
 *
 * A dot alone is also insufficient. Age is shown as a number, because a feed
 * that silently froze looks identical to a quiet market until you can see how
 * old the data is.
 */

const STALE_AFTER_MS = 3000;

interface Props {
  health: Health | undefined;
  apiReachable: boolean;
  dataUpdatedAt: number;
  now: number;
  halted?: boolean;
}

export function StatusStrip({ health, apiReachable, dataUpdatedAt, now, halted = false }: Props) {
  const ageMs = dataUpdatedAt ? now - dataUpdatedAt : Number.POSITIVE_INFINITY;
  const stale = ageMs > STALE_AFTER_MS;

  const apiState = !apiReachable ? "down" : stale ? "degraded" : "live";
  const mode = health?.mode ?? "unknown";

  // The engine reports its own four-state view. Mapping stale to degraded
  // rather than to down matters: an open but silent link and a closed one are
  // different faults, and flattening them hides which is happening.
  const engine = health?.engine;
  const engineDot =
    apiState === "live" && engine?.state === "live"
      ? "live"
      : engine?.state === "stale"
        ? "degraded"
        : "down";

  const engineLabel =
    apiState !== "live"
      ? "status unavailable"
      : engine === undefined
      ? "unknown"
      : engine.state === "live"
        ? `live · ${relativeAge(engine.age_seconds ?? 0)}`
        : engine.state === "stale"
          ? `stale · ${relativeAge(engine.age_seconds ?? 0)}`
          : engine.state === "down"
            ? "disconnected"
            : "starting";

  return (
    <header className="strip" data-stale={stale}>
      <span className="strip-mode">{mode.toUpperCase()}</span>

      <span className="strip-signal">
        <span className="strip-dot" data-state={apiState} />
        <span className="strip-label">api</span>
        {apiReachable ? (
          <span className="num">{relativeAge(ageMs / 1000)}</span>
        ) : (
          <span className="num">unreachable</span>
        )}
      </span>

      <span className="strip-signal">
        <span className="strip-dot" data-state={engineDot} />
        <span className="strip-label">engine</span>
        <span className="num">{engineLabel}</span>
      </span>

      <span className="strip-url" title="The actual base URL in use">
        {health?.exchange_rest_url ?? ""}
      </span>

      <span className="strip-spacer" />

      <EngineControls available={apiState === "live" && engine?.state === "live"} halted={halted} />
    </header>
  );
}
