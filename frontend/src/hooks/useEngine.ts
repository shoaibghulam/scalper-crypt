import { useQuery } from "@tanstack/react-query";
import { fetchHealth, fetchState } from "../lib/api";

/**
 * One hertz.
 *
 * The operator asked to see profit and distance-to-target update every second,
 * which is exactly this. Polling at the transport rather than rendering every
 * exchange tick means the interface cannot jitter even if a component is
 * written naively later.
 *
 * Phase 4 replaces polling with a websocket carrying coalesced snapshots at the
 * same cadence, using the query cache as the delta target. The components do
 * not change when that happens.
 */
const REFRESH_MS = 1000;

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: REFRESH_MS,
    // Keep showing the last known values while a refetch is in flight; the
    // staleness indicator communicates age rather than blanking the screen.
    placeholderData: (previous) => previous,
    retry: false,
  });
}

export function useAppState() {
  return useQuery({
    queryKey: ["state"],
    queryFn: fetchState,
    refetchInterval: REFRESH_MS,
    placeholderData: (previous) => previous,
    retry: false,
  });
}
