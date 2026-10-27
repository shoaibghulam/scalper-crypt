import { useQuery } from "@tanstack/react-query";
import { fetchScan, fetchStrategies } from "../lib/api";

/**
 * The scan is cached server-side per strategy for twenty seconds and costs
 * about fifty requests to Binance, so polling it harder than that multiplies
 * into exchange traffic without producing newer data.
 */
const REFRESH_MS = 15000;

export function useStrategies() {
  return useQuery({
    queryKey: ["strategies"],
    queryFn: fetchStrategies,
    // Pick up backend upgrades while an existing dashboard tab stays open.
    staleTime: 30_000,
    refetchInterval: 30_000,
    retry: false,
  });
}

export function useScan(strategyId: string) {
  return useQuery({
    queryKey: ["scan", strategyId],
    queryFn: () => fetchScan(strategyId),
    refetchInterval: REFRESH_MS,
    retry: false,
    enabled: Boolean(strategyId),
  });
}
