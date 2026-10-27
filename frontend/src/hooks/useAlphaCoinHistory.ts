import { useEffect, useState } from "react";

export const alphaCoinHistoryKey = "scalper-alpha-submitted-coins";
const changedEvent = "scalper-alpha-coins-changed";

function read(): string[] {
  const data: unknown = JSON.parse(localStorage.getItem(alphaCoinHistoryKey) ?? "[]");
  if (!Array.isArray(data)) return [];
  return [...new Set(data.filter((value): value is string => typeof value === "string" && /^[A-Z0-9]{2,25}USDT$/.test(value)))];
}

export function useAlphaCoinHistory() {
  const [coins, setCoins] = useState<string[]>(() => { try { return read(); } catch { return []; } });
  const [error, setError] = useState("");
  useEffect(() => {
    const refresh = () => { try { setCoins(read()); } catch { setError("Could not read coin history in this browser."); } };
    window.addEventListener("storage", refresh);
    window.addEventListener(changedEvent, refresh);
    return () => { window.removeEventListener("storage", refresh); window.removeEventListener(changedEvent, refresh); };
  }, []);

  function remember(symbols: string[]) {
    let current = coins;
    try { current = read(); } catch { /* Preserve in-memory history if storage is unavailable. */ }
    const next = [...new Set([...current, ...symbols])];
    setCoins(next);
    try {
      localStorage.setItem(alphaCoinHistoryKey, JSON.stringify(next));
      setError("");
      window.dispatchEvent(new Event(changedEvent));
    } catch { setError("Search started, but coin history could not be saved. Coins stay hidden only until you reload."); }
  }

  function reset() {
    try {
      localStorage.removeItem(alphaCoinHistoryKey);
      setCoins([]);
      setError("");
      window.dispatchEvent(new Event(changedEvent));
    } catch { setError("Could not reset saved coin history. Check browser storage access and retry."); }
  }
  return { coins, remember, reset, error };
}
