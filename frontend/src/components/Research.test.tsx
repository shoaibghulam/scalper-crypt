import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Research } from "./Research";

afterEach(() => vi.unstubAllGlobals());

it("downloads one coin once and reuses its candles and order filters for comparisons", async () => {
  const fetch = vi.fn().mockImplementation(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/catalog")) return { ok: true, json: async () => catalog };
    if (url.endsWith("/symbols")) return { ok: true, json: async () => ({ symbols: ["ETHUSDT"] }) };
    if (url.endsWith("/history")) return { ok: true, json: async () => ({
      csv: "downloaded data", symbol: "ETHUSDT", candles: 4320, start: 0, end: 259199999,
      minimum_notional: 20, quantity_step: .01, minimum_quantity: .02,
      source: "Binance Futures", dataset_sha256: "example", note: "Current filters",
    }) };
    const body = JSON.parse(options?.body as string);
    return { ok: true, json: async () => ({ config: body, results: [], limitations: [] }) };
  });
  vi.stubGlobal("fetch", fetch);
  render(<Research />);
  await screen.findByRole("option", { name: /lab-01/ });
  expect(screen.queryByLabelText("Historical CSV")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Coin"), { target: { value: "ethusdt" } });
  fireEvent.change(screen.getByLabelText("Strategy"), { target: { value: "all-lab" } });
  fireEvent.submit(screen.getByRole("button", { name: "Run chronological evaluation" }).closest("form")!);
  await screen.findByText("2/2 experiments completed");
  expect(fetch.mock.calls.filter(([url]) => url.endsWith("/history"))).toHaveLength(1);
  const requests = fetch.mock.calls.filter(([url]) => url.endsWith("/backtest"))
    .map(([, options]) => JSON.parse(options.body));
  expect(requests).toHaveLength(2);
  expect(requests.every(r => r.csv === "downloaded data" && r.minimum_notional === 20
    && r.quantity_step === .01 && r.minimum_quantity === .02)).toBe(true);
  expect(screen.getByRole("button", { name: "Download candles CSV" })).toBeInTheDocument();
});

it("does not run a backtest when the history download fails", async () => {
  const fetch = vi.fn().mockImplementation(async (url: string) => {
    if (url.endsWith("/catalog")) return { ok: true, json: async () => catalog };
    if (url.endsWith("/symbols")) return { ok: true, json: async () => ({ symbols: [] }) };
    return { ok: false, json: async () => ({ detail: "Binance unavailable" }) };
  });
  vi.stubGlobal("fetch", fetch);
  render(<Research />);
  fireEvent.submit(screen.getByRole("button", { name: "Run chronological evaluation" }).closest("form")!);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Binance unavailable"));
  expect(fetch.mock.calls.some(([url]) => url.endsWith("/backtest"))).toBe(false);
});
const catalog = [{ id: "lab-01", name: "EMA cross", timeframe: "1m", research_only: true },
  { id: "lab-02", name: "EMA reclaim", timeframe: "1m", research_only: true }];
function mockFetch(post: unknown) {
  const fetch = vi.fn().mockImplementation((url: string) => Promise.resolve(
    url.endsWith("/catalog") ? { ok: true, json: async () => catalog } : post));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
it("does not run without an uploaded dataset", async () => {
  const fetch = mockFetch({});
  render(<Research />);
  fireEvent.change(screen.getByLabelText("Historical data"), { target: { value: "csv" } });
  expect(screen.getByRole("button", { name: "Run chronological evaluation" })).toBeDisabled();
  await screen.findByRole("option", { name: /lab-01/ });
  expect(fetch.mock.calls.some(([url]) => url.endsWith("/backtest") || url.endsWith("/history"))).toBe(false);
});
it("shows backend validation errors without claiming success", async () => {
  const fetch = mockFetch({ ok: false, status: 422,
    json: async () => ({ detail: "Provide at least 1,200 consecutive one-minute candles" }) });
  render(<Research />);
  fireEvent.change(screen.getByLabelText("Historical data"), { target: { value: "csv" } });
  const file = new File(["bad"], "test.csv", { type: "text/csv" });
  Object.defineProperty(file, "text", { value: async () => "bad" });
  fireEvent.change(screen.getByLabelText("Historical CSV"), { target: { files: [file] } });
  for (const input of screen.getAllByRole("spinbutton")) {
    expect((input as HTMLInputElement).checkValidity()).toBe(true);
  }
  // jsdom's synthetic FileList does not satisfy native required-file validation.
  fireEvent.submit(screen.getByRole("button", { name: "Run chronological evaluation" }).closest("form")!);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("1,200"));
  expect(screen.queryByText("Completed experiment")).not.toBeInTheDocument();
  expect(fetch.mock.calls.some(([url]) => url === "/api/research/backtest")).toBe(true);
});
it("shows zero-trade results as undefined metrics and preserves run settings", async () => {
  mockFetch({ ok: true, json: async () => ({
    config: { strategy: "ema-trend-pullback", capital: 10, leverage: 3 },
    dataset_sha256: "example", limitations: ["No funding modeled"],
    results: [{ period: 1, stress: false, start: 0, end: 59999, net: 0,
      trade_count: 0, win_rate_pct: null, expectancy: null, profit_factor: null,
      closed_equity_drawdown_pct: 0, total_fees: 0, rejected_entries: 2, unfilled_entries: 0 }],
  }) });
  render(<Research />);
  fireEvent.change(screen.getByLabelText("Historical data"), { target: { value: "csv" } });
  const file = new File(["data"], "test.csv");
  Object.defineProperty(file, "text", { value: async () => "data" });
  fireEvent.change(screen.getByLabelText("Historical CSV"), { target: { files: [file] } });
  fireEvent.submit(screen.getByRole("button", { name: "Run chronological evaluation" }).closest("form")!);
  await screen.findByText("Completed experiment");
  expect(screen.getAllByRole("cell", { name: "N/A" })).toHaveLength(3);
  expect(screen.getByText("No funding modeled")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Starting capital (USDT)"), { target: { value: "100" } });
  expect(screen.getByText(/10 USDT starting capital/)).toBeInTheDocument();
});
it("compares candidates sequentially using identical economics", async () => {
  const fetch = vi.fn().mockImplementation(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/catalog")) return { ok: true, json: async () => catalog };
    const request = JSON.parse(options?.body as string);
    return { ok: true, json: async () => ({ config: request, dataset_sha256: "same",
      results: [], limitations: [] }) };
  });
  vi.stubGlobal("fetch", fetch);
  render(<Research />);
  fireEvent.change(screen.getByLabelText("Historical data"), { target: { value: "csv" } });
  await screen.findByRole("option", { name: /lab-01/ });
  fireEvent.change(screen.getByLabelText("Strategy"), { target: { value: "all-lab" } });
  const file = new File(["same candles"], "test.csv");
  Object.defineProperty(file, "text", { value: async () => "same candles" });
  fireEvent.change(screen.getByLabelText("Historical CSV"), { target: { files: [file] } });
  fireEvent.submit(screen.getByRole("button", { name: "Run chronological evaluation" }).closest("form")!);
  await screen.findByText("2/2 experiments completed");
  const requests = fetch.mock.calls.filter(([url]) => url.endsWith("/backtest"))
    .map(([, options]) => JSON.parse(options.body));
  expect(requests.map(r => r.strategy)).toEqual(["lab-01", "lab-02"]);
  expect(requests[0].csv).toBe(requests[1].csv);
  expect(requests[0].capital).toBe(requests[1].capital);
  expect(screen.getByRole("button", { name: "View lab-01" })).toBeInTheDocument();
});

it("uses catalog history requirements for every strategy in a video comparison", async () => {
  const entries = [...catalog, { id: "funded-price-action", name: "Funded Price Action",
    timeframe: "5m", research_only: true, warmup_candles: 1260,
    assumptions: ["Stop beyond confirmation candle; 2R target."] }];
  const fetch = vi.fn().mockImplementation(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/catalog")) return { ok: true, json: async () => entries };
    const request = JSON.parse(options?.body as string);
    return { ok: true, json: async () => ({ config: request, dataset_sha256: "same",
      results: [], limitations: [] }) };
  });
  vi.stubGlobal("fetch", fetch);
  render(<Research />);
  fireEvent.change(screen.getByLabelText("Historical data"), { target: { value: "csv" } });
  await screen.findByRole("option", { name: /funded-price-action/ });
  fireEvent.change(screen.getByLabelText("Strategy"), { target: { value: "funded-price-action" } });
  expect(screen.getByText("Stop beyond confirmation candle; 2R target.")).toBeInTheDocument();
  expect(screen.queryByText(/Our crypto adaptation uses/)).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Strategy"), { target: { value: "all-library" } });
  const file = new File(["same candles"], "test.csv");
  Object.defineProperty(file, "text", { value: async () => "same candles" });
  fireEvent.change(screen.getByLabelText("Historical CSV"), { target: { files: [file] } });
  fireEvent.submit(screen.getByRole("button", { name: "Run chronological evaluation" }).closest("form")!);
  await screen.findByText("3/3 experiments completed");
  const requests = fetch.mock.calls.filter(([url]) => url.endsWith("/backtest"))
    .map(([, options]) => JSON.parse(options.body));
  expect(requests.map(r => r.warmup)).toEqual([1260, 1260, 1260]);
});

it("compares only the new one-minute group on the same downloaded history", async () => {
  const fetch = vi.fn().mockImplementation(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/catalog")) return { ok: true, json: async () => [...catalog,
      { ...catalog[0], id: "minute-01", name: "Aroon release" },
      { ...catalog[0], id: "minute-02", name: "Vortex transition" },
    ] };
    if (url.endsWith("/symbols")) return { ok: true, json: async () => ({ symbols: ["ETHUSDT"] }) };
    if (url.endsWith("/history")) return { ok: true, json: async () => ({
      csv: "same history", symbol: "ETHUSDT", candles: 4320, start: 0, end: 259199999,
      minimum_notional: 20, quantity_step: .01, minimum_quantity: .02,
      source: "Binance Futures", dataset_sha256: "same", note: "Current filters",
    }) };
    return { ok: true, json: async () => ({ config: JSON.parse(options?.body as string),
      results: [], limitations: [] }) };
  });
  vi.stubGlobal("fetch", fetch);
  render(<Research />);
  await screen.findByRole("option", { name: /minute-01/ });
  fireEvent.change(screen.getByLabelText("Strategy"), { target: { value: "all-minute" } });
  fireEvent.submit(screen.getByRole("button", { name: "Run chronological evaluation" }).closest("form")!);
  await screen.findByText("2/2 experiments completed");
  const requests = fetch.mock.calls.filter(([url]) => url.endsWith("/backtest"))
    .map(([, options]) => JSON.parse(options.body));
  expect(requests.map(r => r.strategy)).toEqual(["minute-01", "minute-02"]);
  expect(requests.every(r => r.csv === "same history")).toBe(true);
  expect(fetch.mock.calls.filter(([url]) => url.endsWith("/history"))).toHaveLength(1);
});

it("loads saved experiments with validation hidden until explicitly revealed", async () => {
  const metric = { trades: 40, net: 2, win_rate: 60, expectancy: .05, profit_factor: 1.3,
    worst_period_drawdown_pct: 2, positive_periods: 2, periods: 2 };
  const fetch = vi.fn().mockImplementation(async (url: string) => {
    if (url.endsWith('/catalog')) return { ok: true, json: async () => catalog };
    if (url.endsWith('/symbols')) return { ok: true, json: async () => ({ symbols: [] }) };
    if (url.endsWith('/runs')) return { ok: true, json: async () => [
      { id: 'saved', saved_at: 1789776000, strategy: 'lab-01', dataset_label: 'ETHUSDT' },
    ] };
    return { ok: true, json: async () => ({ run_id: 'saved', dataset_sha256: 'hash',
      config: { strategy: 'lab-01', capital: 10, leverage: 3 }, limitations: [], results: [],
      diagnostics: { development: metric, development_stress: metric,
        holdout: { ...metric, net: -99 }, holdout_stress: { ...metric, net: -100 },
        status: 'shortlist_candidate', minimum_trades: 30, ranking_basis: 'development only', status_note: 'heuristic' },
    }) };
  });
  vi.stubGlobal('fetch', fetch);
  render(<Research />);
  fireEvent.click(await screen.findByRole('button', { name: 'Open lab-01', hidden: true }));
  await screen.findByText('Completed experiment');
  expect(screen.queryByText('-99.00 / -100.00')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Reveal validation results' }));
  expect(screen.getByText('-99.00 / -100.00')).toBeInTheDocument();
  expect(screen.getByText(/Saved experiment: saved/)).toBeInTheDocument();
});
