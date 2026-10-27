import { useEffect, useState } from "react";

/**
 * A ticking clock for age displays.
 *
 * Staleness has to be computed against the present, not against the moment the
 * data arrived, or a frozen feed keeps claiming it was updated one second ago
 * forever. That failure is worse than showing nothing, because it looks
 * healthy.
 */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}
