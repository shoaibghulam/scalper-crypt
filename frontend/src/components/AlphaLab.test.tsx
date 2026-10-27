import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AlphaLab } from "./AlphaLab";
import { alphaCoinHistoryKey } from "../hooks/useAlphaCoinHistory";

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });
it("hides remembered coins from all selectors and reset makes them selectable again", async () => {
  localStorage.setItem(alphaCoinHistoryKey, JSON.stringify(["BTCUSDT", "ETHUSDT"]));
  mount();
  await screen.findByLabelText("Search coins");
  expect(screen.queryByRole("button", { name: "Choose Bitcoin (BTC)" })).not.toBeInTheDocument();
  expect(screen.queryByRole("option", { name: /Ethereum/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("Search multiple coins for passing candidates"));
  fireEvent.click(screen.getByRole("button", { name: "Select all coins" }));
  expect(screen.getByText("2/1000 coins selected", { exact: false })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Reset coin history" }));
  expect(localStorage.getItem(alphaCoinHistoryKey)).toBeNull();
  expect(screen.getByRole("button", { name: "Choose Bitcoin (BTC)" })).toBeInTheDocument();
});

it("remembers submitted coins only after a successful start", async () => {
  mount();
  await screen.findByLabelText("Search coins");
  fireEvent.click(screen.getByLabelText("Search multiple coins for passing candidates"));
  fireEvent.click(screen.getByRole("button", { name: "Select all coins" }));
  expect(localStorage.getItem(alphaCoinHistoryKey)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
  await waitFor(() => expect(JSON.parse(localStorage.getItem(alphaCoinHistoryKey) ?? "[]")).toEqual(["ARBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]));
  expect(screen.queryByRole("button", { name: "Choose Bitcoin (BTC)" })).not.toBeInTheDocument();
  expect(screen.getByText(/No new coins available/)).toBeInTheDocument();
});
it("selects all available coins and adds search matches without losing selection", async () => {
  const fetcher = mount();
  await screen.findByLabelText("Search coins");
  fireEvent.click(screen.getByLabelText("Search multiple coins for passing candidates"));
  fireEvent.change(screen.getByLabelText("Search coins"), { target: { value: "eth" } });
  fireEvent.click(screen.getByRole("button", { name: "Select search results" }));
  expect(screen.getByRole("button", { name: "Choose Bitcoin (BTC)" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "Choose Ethereum (ETH)" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByRole("button", { name: "Select all coins" }));
  fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
  await waitFor(() => expect(fetcher.mock.calls.some(([, options]) => options?.method === "POST")).toBe(true));
  const body = JSON.parse(String(fetcher.mock.calls.find(([, options]) => options?.method === "POST")?.[1]?.body));
  expect(body.symbols).toEqual(["ARBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]);
});
function mount(cuda = true, saved: unknown[] = [], coinsFail = false) {
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/symbols")) return { ok: !coinsFail, json: async () => ({ symbols: ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ARBUSDT"] }) };
    if (url.endsWith("capabilities")) return { ok: true, json: async () => ({ torch: "2.7.1", cuda, gpu: cuda ? "RTX 4050" : null, memory_gb: 6, multi_coin_limit: 1000 }) };
    if (options?.method === "POST") return { ok: true, json: async () => ({ id: "a".repeat(32), status: "running", phase: "Training", progress: 10, config: JSON.parse(String(options.body)) }) };
    if (url.endsWith("jobs")) return { ok: true, json: async () => saved };
    return { ok: true, json: async () => saved[0] };
  });
  vi.stubGlobal("fetch", fetcher);
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AlphaLab /></QueryClientProvider>);
  return fetcher;
}

it("starts GPU comparison only on submission with selected coin and costs", async () => {
  const fetcher = mount();
  await screen.findByText(/GPU ready/);
  await screen.findByRole("button", { name: "Choose Ethereum (ETH)" });
  expect(fetcher.mock.calls.some(([, options]) => options?.method === "POST")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Choose Ethereum (ETH)" }));
  fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
  await waitFor(() => expect(fetcher.mock.calls.find(([, options]) => options?.method === "POST")).toBeTruthy());
  const body = JSON.parse(String(fetcher.mock.calls.find(([, options]) => options?.method === "POST")?.[1]?.body));
  expect(body).toMatchObject({ symbol: "ETHUSDT", method: "compare", device: "cuda", fee_bps: 5, slippage_bps: 2, spread_bps: 2 });
  expect(screen.getByRole("button", { name: "Start discovery" })).toBeDisabled();
});

it("automatically uses CPU without CUDA, and validates an explicit GPU choice", async () => {
  mount(false);
  await screen.findByText(/CPU available/);
  await screen.findByRole("button", { name: "Choose Bitcoin (BTC)" });
  expect(screen.getByRole("button", { name: "Start discovery" })).toBeEnabled();
  fireEvent.click(screen.getByText("Advanced settings", { exact: false, selector: "summary" }));
  fireEvent.change(screen.getByLabelText("Neural training device"), { target: { value: "cuda" } });
  expect(screen.getByRole("button", { name: "Start discovery" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Neural training device"), { target: { value: "cpu" } });
  expect(screen.getByRole("button", { name: "Start discovery" })).toBeEnabled();
  fireEvent.change(screen.getByLabelText("Search method"), { target: { value: "gp" } });
  expect(screen.getByLabelText("Neural training device")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Start discovery" })).toBeEnabled();
});

it("searches by coin name or symbol and submits the chosen pair with a preset", async () => {
  const fetcher = mount();
  await screen.findByLabelText("Search coins");
  fireEvent.change(screen.getByLabelText("Search coins"), { target: { value: "arb" } });
  expect(screen.getByRole("option", { name: "ARB / USDT" })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: /Bitcoin/ })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Available coins"), { target: { value: "ARBUSDT" } });
  fireEvent.click(screen.getByRole("button", { name: /Quick check/ }));
  fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
  await waitFor(() => expect(fetcher.mock.calls.some(([, options]) => options?.method === "POST")).toBe(true));
  const request = JSON.parse(String(fetcher.mock.calls.find(([, options]) => options?.method === "POST")?.[1]?.body));
  expect(request).toMatchObject({ symbol: "ARBUSDT", days: 7, population: 8, generations: 2, minimum_trades: 30 });
});

it("keeps selected coin when a search has no matches and finds full coin names", async () => {
  mount();
  await screen.findByLabelText("Search coins");
  fireEvent.change(screen.getByLabelText("Search coins"), { target: { value: "Ethereum" } });
  expect(screen.getByRole("option", { name: /ETH.*Ethereum/ })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Search coins"), { target: { value: "nonexistent" } });
  expect(screen.getByRole("option", { name: "No matching coins" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Choose Bitcoin (BTC)" })).toHaveAttribute("aria-pressed", "true");
});

it("blocks discovery and offers retry when coin loading fails", async () => {
  const fetcher = mount(true, [], true);
  await screen.findByRole("button", { name: "Retry coin list" });
  expect(screen.getByRole("button", { name: "Start discovery" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Retry coin list" }));
  await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => url.endsWith("/symbols"))).toHaveLength(2));
  expect(fetcher.mock.calls.some(([, options]) => options?.method === "POST")).toBe(false);
});

it("submits the selected coins and prevents an empty multi-coin search", async () => {
  const fetcher = mount();
  await screen.findByLabelText("Search coins");
  fireEvent.click(screen.getByLabelText("Search multiple coins for passing candidates"));
  fireEvent.click(screen.getByRole("button", { name: "Clear coins" }));
  expect(screen.getByRole("button", { name: "Start discovery" })).toBeDisabled();
  fireEvent.click(screen.getByLabelText("ETH / USDT"));
  fireEvent.click(screen.getByLabelText("SOL / USDT"));
  fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
  await waitFor(() => expect(fetcher.mock.calls.some(([, options]) => options?.method === "POST")).toBe(true));
  const body = JSON.parse(String(fetcher.mock.calls.find(([, options]) => options?.method === "POST")?.[1]?.body));
  expect(body.symbols).toEqual(["ETHUSDT", "SOLUSDT"]);
});

it("filters batch failures without discarding them or hiding the full download", async () => {
  const metrics = { net: -1, trade_count: 12, total_fees: 2, return_pct: -1, closed_equity_drawdown_pct: 3, profit_factor: .5 };
  const result = { protocol: "Test", windows: {}, limitations: [], finalists: [{ method: "gp", passed: false, formula: "zero", status: "Rejected", unique_formulas: 10,
    training: metrics, validation: metrics, holdout: metrics, stress_holdout: metrics }] };
  const id = "c".repeat(32);
  mount(true, [{ id, config: { symbol: "BTCUSDT", symbols: ["BTCUSDT", "ETHUSDT"], method: "gp" }, created_at: 1, status: "completed", phase: "Completed", completed_coins: 2, coin_count: 2,
    coin_results: [{ symbol: "BTCUSDT", result }, { symbol: "ETHUSDT", result }] }]);
  await screen.findByLabelText("Saved discovery runs");
  fireEvent.change(screen.getByLabelText("Saved discovery runs"), { target: { value: id } });
  await screen.findByText(/No passing candidates found in the completed results/);
  expect(screen.queryByLabelText("Coin results")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Download full report/ })).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("Show passing candidates only"));
  expect(screen.getByLabelText("Coin results")).toBeInTheDocument();
  expect(screen.getByText("BTCUSDT results")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Coin results"), { target: { value: "ETHUSDT" } });
  expect(screen.getByText("ETHUSDT results")).toBeInTheDocument();
});

it("shows only the passing method within a batch coin", async () => {
  const metrics = { net: 1, trade_count: 40, total_fees: 2, return_pct: 1, closed_equity_drawdown_pct: 3, profit_factor: 1.5 };
  const finalist = { formula: "one", status: "Test verdict", unique_formulas: 10, training: metrics, validation: metrics, holdout: metrics, stress_holdout: metrics };
  const id = "d".repeat(32);
  mount(true, [{ id, config: { symbol: "BTCUSDT", symbols: ["BTCUSDT", "ETHUSDT"], method: "compare" }, created_at: 1, status: "completed", phase: "Completed",
    coin_results: [{ symbol: "ETHUSDT", result: { protocol: "Test", windows: {}, limitations: [], finalists: [{ ...finalist, method: "rl", passed: true }, { ...finalist, method: "gp", passed: false }] } }] }]);
  await screen.findByLabelText("Saved discovery runs");
  fireEvent.change(screen.getByLabelText("Saved discovery runs"), { target: { value: id } });
  await screen.findByText("AI learning result");
  expect(screen.queryByText("Evolve trading rules result")).not.toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("Show passing candidates only"));
  expect(screen.getByText("Evolve trading rules result")).toBeInTheDocument();
});

it("shows failed holdout and saved report without a trading action", async () => {
  const metrics = { net: -1, trade_count: 12, total_fees: 2, return_pct: -1, closed_equity_drawdown_pct: 3, profit_factor: .5 };
  const id = "b".repeat(32);
  mount(true, [{ id, config: { symbol: "BTCUSDT", method: "gp" }, created_at: 1, status: "completed", phase: "Completed",
    result: { protocol: "Test protocol", windows: {}, limitations: ["Research only"], finalists: [{ method: "gp", formula: "add(rsi, zero)", unique_formulas: 16,
      status: "Rejected: loss or insufficient trades", training: metrics, validation: metrics, holdout: metrics, stress_holdout: metrics }] } }]);
  await screen.findByLabelText("Saved discovery runs");
  fireEvent.change(screen.getByLabelText("Saved discovery runs"), { target: { value: id } });
  await screen.findByText("Rejected: loss or insufficient trades");
  expect(screen.getByRole("link", { name: /Download full report/ })).toHaveAttribute("href", `/api/alpha-lab/jobs/${id}`);
  expect(screen.queryByRole("button", { name: /trade/i })).not.toBeInTheDocument();
});
