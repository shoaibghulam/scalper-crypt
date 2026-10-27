import { useCallback, useEffect, useState } from "react";

/**
 * Saved coin/strategy pairs, kept on the server.
 *
 * These used to live in localStorage, which tied them to one browser on one
 * machine: clearing site data, switching browser, or opening the dashboard
 * from a second machine lost the shortlist that trades get started from. They
 * now live in the research database alongside the backtests they came from.
 *
 * Anything already saved in this browser is migrated once on first load, so
 * the move does not cost anyone their existing list.
 */

export type FavoritePair = { symbol: string; strategy: string };

const storageKey = "scalper-research-favorites";
const migratedKey = "scalper-research-favorites-migrated";
const changedEvent = "scalper-favorites-changed";
const endpoint = "/api/research/favorites";

export const favoriteId = (symbol: string, strategy: string) =>
  JSON.stringify([symbol, strategy]);

function parseId(id: string): FavoritePair | null {
  try {
    const pair: unknown = JSON.parse(id);
    if (!Array.isArray(pair) || pair.length !== 2) return null;
    const [symbol, strategy] = pair;
    if (typeof symbol !== "string" || typeof strategy !== "string") return null;
    if (!symbol || !strategy) return null;
    return { symbol, strategy };
  } catch {
    return null;
  }
}

/** Pairs this browser saved before favourites moved to the server. */
function localLeftovers(): FavoritePair[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(storageKey) ?? "[]");
    if (!Array.isArray(saved)) return [];
    return saved
      .filter((value): value is string => typeof value === "string")
      .map(parseId)
      .filter((pair): pair is FavoritePair => pair !== null);
  } catch {
    return [];
  }
}

async function readPairs(): Promise<FavoritePair[]> {
  const response = await fetch(endpoint);
  if (!response.ok) throw new Error("Could not read saved favorites.");
  const body: unknown = await response.json();
  if (!Array.isArray(body)) return [];
  return body
    .filter(
      (row): row is FavoritePair =>
        typeof row === "object" &&
        row !== null &&
        typeof (row as FavoritePair).symbol === "string" &&
        typeof (row as FavoritePair).strategy === "string",
    )
    .map(({ symbol, strategy }) => ({ symbol, strategy }));
}

async function write(method: "POST" | "DELETE", pair: FavoritePair): Promise<void> {
  const response = await fetch(endpoint, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(pair),
  });
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail =
      typeof body === "object" &&
      body !== null &&
      typeof (body as { detail?: unknown }).detail === "string"
        ? (body as { detail: string }).detail
        : "Could not save favorites.";
    throw new Error(detail);
  }
}

export function useFavorites() {
  const [pairs, setPairs] = useState<FavoritePair[]>([]);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      setPairs(await readPairs());
      setError("");
    } catch {
      // Keep whatever is on screen. An unreadable list is better shown stale
      // than silently emptied, which would look like the favourites were lost.
      setError("Favorites are unavailable while the API is unreachable.");
    }
  }, []);

  useEffect(() => {
    let active = true;
    async function start() {
      // One-time move of anything this browser saved before the change.
      if (!localStorage.getItem(migratedKey)) {
        for (const pair of localLeftovers()) {
          try {
            await write("POST", pair);
          } catch {
            /* A pair the server rejects is not worth blocking the page for. */
          }
        }
        try {
          localStorage.setItem(migratedKey, "1");
        } catch {
          /* Private mode: the migration simply runs again next time. */
        }
      }
      if (active) await refresh();
    }
    void start();
    const onChange = () => void refresh();
    window.addEventListener(changedEvent, onChange);
    return () => {
      active = false;
      window.removeEventListener(changedEvent, onChange);
    };
  }, [refresh]);

  const favorites = pairs.map(({ symbol, strategy }) => favoriteId(symbol, strategy));

  async function toggleFavorite(id: string) {
    const pair = parseId(id);
    if (!pair) return;
    const saved = favorites.includes(id);
    // Optimistic: the row reacts at once and is corrected by the refresh if
    // the server disagrees.
    setPairs(previous =>
      saved ? previous.filter(p => favoriteId(p.symbol, p.strategy) !== id) : [...previous, pair],
    );
    try {
      await write(saved ? "DELETE" : "POST", pair);
      setError("");
      window.dispatchEvent(new Event(changedEvent));
    } catch (cause) {
      // Re-read first, THEN report. A successful refresh clears the error, so
      // setting it beforehand meant a failed save corrected itself on screen
      // and the operator was told nothing had gone wrong.
      await refresh();
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  return { favorites, pairs, toggleFavorite, error, refresh };
}
