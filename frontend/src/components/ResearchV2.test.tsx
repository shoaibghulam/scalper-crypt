import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ResearchV2 } from "./ResearchV2";

/**
 * The v2 tab exists to make one thing legible: what a run will cost, and why a
 * fast run was fast. These tests cover that promise rather than the layout - a
 * page that claimed data was already offline when it was not would send the
 * operator into a download they did not expect.
 */

const CATALOG = [
  { id: "ctk-1", name: "CTK 1", timeframe: "1m" },
  { id: "ctk-5", name: "CTK 5", timeframe: "5m" },
  // A frozen single-dataset hypothesis: it refuses on any other coin.
  {
    id: "btc-selective-reversion-1m",
    name: "BTC Selective Reversion",
    timeframe: "1m",
    research_symbols: ["BTCUSDT"],
  },
];
// The REAL shape of /api/research/symbols. The first version of these tests
// stubbed a bare array, which is not what the endpoint returns - so the tests
// confirmed the component's assumption instead of the API, and the tab crashed
// on `symbols.filter is not a function` the moment it met the real server.
const SYMBOLS = {
  source: "Binance USD-M Futures public live-market data",
  symbols: ["BTCUSDT", "ETHUSDT"],
};
const INVENTORY = {
  symbols: [
    {
      symbol: "BTCUSDT",
      months: 13,
      first: "2025-09",
      last: "2026-09",
      candles: 525600,
      megabytes: 33.6,
    },
  ],
  cached_signal_series: 4,
};

function stubFetch(overrides: Record<string, unknown> = {}) {
  const calls: { url: string; body?: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body as string | undefined });
      const match = Object.keys(overrides).find(key => url.includes(key));
      const payload = match
        ? overrides[match]
        : url.includes("/catalog")
          ? CATALOG
          : url.includes("/symbols")
            ? SYMBOLS
            : url.includes("/inventory")
              ? INVENTORY
              : {};
      const status =
        payload && typeof payload === "object" && "__status" in payload
          ? (payload as { __status: number }).__status
          : 200;
      return { ok: status < 400, status, json: async () => payload };
    }),
  );
  return calls;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
});

it("says how much of the selection is already stored offline", async () => {
  stubFetch();
  await act(async () => {
    render(<ResearchV2 />);
  });
  // BTCUSDT is selected by default and is in the inventory, so nothing to fetch.
  await waitFor(() =>
    expect(screen.getByText(/All selected coins are already stored offline/)).toBeInTheDocument(),
  );
  expect(screen.getByText(/1 of 1 selected already held/)).toBeInTheDocument();
});

it("warns that an unstored coin has to be downloaded first", async () => {
  stubFetch();
  await act(async () => {
    render(<ResearchV2 />);
  });
  await waitFor(() =>
    expect(screen.getByRole("checkbox", { name: /ETHUSDT/ })).toBeInTheDocument(),
  );
  fireEvent.click(screen.getByRole("checkbox", { name: /ETHUSDT/ }));
  // ETHUSDT is absent from the inventory, so the estimate must change.
  await waitFor(() =>
    expect(screen.getByText(/1 coin\(s\) need downloading first/)).toBeInTheDocument(),
  );
});

it("marks stored coins so the picker shows what is free to test", async () => {
  stubFetch();
  await act(async () => {
    render(<ResearchV2 />);
  });
  // Find the coin rows by their own label text. A pinned strategy is
  // labelled "BTCUSDT only", so matching the coin by pattern alone would
  // pick up the strategy checkbox too.
  await screen.findByRole("checkbox", { name: /ETHUSDT/ });
  const coinRows = screen
    .getAllByRole("checkbox")
    .map(box => box.closest("label"))
    .filter((label): label is HTMLLabelElement => !!label?.textContent?.endsWith("USDT")
      || !!label?.textContent?.match(/^(BTC|ETH)USDT/));
  const stored = coinRows.find(l => l.textContent?.startsWith("BTCUSDT"));
  const absent = coinRows.find(l => l.textContent?.startsWith("ETHUSDT"));
  expect(stored?.textContent).toContain("offline");
  expect(absent?.textContent).not.toContain("offline");
});

it("sends the chosen coins and strategies to the v2 endpoint", async () => {
  const calls = stubFetch({
    "/v2/jobs": {
      id: "a".repeat(32),
      status: "running",
      phase: "Starting",
      progress: 0,
      results: [],
    },
  });
  await act(async () => {
    render(<ResearchV2 />);
  });
  await waitFor(() => expect(screen.getByRole("checkbox", { name: /CTK 5/ })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("checkbox", { name: /CTK 5/ }));
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /Run offline backtest/ }));
  });
  const post = calls.find(call => call.url.includes("/v2/jobs") && call.body);
  expect(post).toBeDefined();
  const sent = JSON.parse(post!.body!);
  expect(sent.symbols).toEqual(["BTCUSDT"]);
  expect(sent.strategies).toEqual(["ctk-1", "ctk-5"]);
});

it("reports which results came from cached signals", async () => {
  const job = {
    id: "b".repeat(32),
    status: "completed",
    phase: "Completed",
    progress: 100,
    results: [
      {
        symbol: "BTCUSDT",
        strategy: "ctk-1",
        run_id: "r1",
        signals: 149,
        signals_cached: true,
        net: -6.75,
        gross_profit: -3.55,
        won: 12.4,
        lost: 19.15,
        capital: 100,
        periods: 3,
        capital_deployed: 300,
        trades: 98,
        tp_hits: 55,
        sl_hits: 40,
        fees: 3.2,
      },
      {
        symbol: "BTCUSDT",
        strategy: "ctk-5",
        run_id: "r2",
        signals: 31,
        signals_cached: false,
        net: 2.5,
        gross_profit: 3.6,
        won: 8.1,
        lost: 5.6,
        capital: 100,
        periods: 3,
        capital_deployed: 300,
        trades: 20,
        tp_hits: 12,
        sl_hits: 8,
        fees: 1.1,
      },
    ],
  };
  localStorage.setItem("scalper-research-v2-job", job.id);
  stubFetch({ "/v2/jobs/": job });
  await act(async () => {
    render(<ResearchV2 />);
  });
  await waitFor(() =>
    expect(screen.getByText(/1 of 2 answered from cached signals/)).toBeInTheDocument(),
  );
  // Sorted by net, so the uncached winner sorts above the cached loser.
  const rows = screen.getAllByRole("row").slice(1);
  expect(rows[0].textContent).toContain("ctk-5");
  expect(rows[1].textContent).toContain("cached");
});

it("breaks the result into money won and money lost", async () => {
  // "Net -164" hides whether the strategy won small and often or lost big and
  // rarely, and that is usually the first thing worth knowing.
  const job = {
    id: "e".repeat(32),
    status: "completed",
    phase: "Completed",
    progress: 100,
    results: [
      {
        symbol: "BTCUSDT",
        strategy: "ctk-1",
        run_id: "r1",
        signals: 149,
        signals_cached: true,
        net: -6.75,
        gross_profit: -3.55,
        won: 12.4,
        lost: 19.15,
        capital: 100,
        periods: 3,
        capital_deployed: 300,
        trades: 98,
        tp_hits: 55,
        sl_hits: 40,
        fees: 3.2,
      },
    ],
  };
  localStorage.setItem("scalper-research-v2-job", job.id);
  stubFetch({ "/v2/jobs/": job });
  await act(async () => {
    render(<ResearchV2 />);
  });
  const row = (await screen.findAllByRole("row"))[1];
  expect(row.textContent).toContain("+12.40");
  expect(row.textContent).toContain("-19.15");
  // Net against the capital actually deployed across all three periods.
  expect(row.textContent).toContain("-2.3%");
  expect(row.textContent).toContain("57.9%");
});

it("explains that the net is a sum of independent periods", async () => {
  // Without this the figure reads as one account running to a negative
  // balance, which is not what happened.
  const job = {
    id: "f".repeat(32),
    status: "completed",
    phase: "Completed",
    progress: 100,
    results: [
      {
        symbol: "BTCUSDT",
        strategy: "ctk-1",
        run_id: "r1",
        signals: 10,
        signals_cached: true,
        net: -164.48,
        gross_profit: -58.35,
        won: 30,
        lost: 194.48,
        capital: 100,
        periods: 3,
        capital_deployed: 300,
        trades: 1255,
        tp_hits: 639,
        sl_hits: 616,
        fees: 106.13,
      },
    ],
  };
  localStorage.setItem("scalper-research-v2-job", job.id);
  stubFetch({ "/v2/jobs/": job });
  await act(async () => {
    render(<ResearchV2 />);
  });
  expect(await screen.findByText(/3 separate periods/)).toBeInTheDocument();
  expect(screen.getByText(/sum of 3 independent runs/)).toBeInTheDocument();
});

it("surfaces a per-symbol failure instead of reporting a clean run", async () => {
  const job = {
    id: "c".repeat(32),
    status: "completed_with_errors",
    phase: "Completed; review unavailable symbols",
    progress: 100,
    results: [],
    errors: [{ symbol: "ETHUSDT", error: "ETHUSDT 1m is missing 2 month(s): 2025-09, 2025-10" }],
  };
  localStorage.setItem("scalper-research-v2-job", job.id);
  stubFetch({ "/v2/jobs/": job });
  await act(async () => {
    render(<ResearchV2 />);
  });
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("missing 2 month(s)");
});

it("survives a coin list wrapped in an envelope", async () => {
  // Regression: reading the envelope as an array put an object into state and
  // the next render threw, taking the whole tab down.
  stubFetch();
  await act(async () => {
    render(<ResearchV2 />);
  });
  expect(await screen.findByRole("checkbox", { name: /ETHUSDT/ })).toBeInTheDocument();
  expect(screen.getByText(/2 of 2 coins match/)).toBeInTheDocument();
});

it("renders when the coin list is a shape nobody expected", async () => {
  // A boundary that cannot be trusted must not be able to crash the page.
  stubFetch({ "/symbols": { unexpected: true } });
  await act(async () => {
    render(<ResearchV2 />);
  });
  expect(screen.getByRole("button", { name: /Run offline backtest/ })).toBeEnabled();
  expect(screen.getByText(/0 of 0 coins match/)).toBeInTheDocument();
});

it("ignores catalog rows that are not strategies", async () => {
  stubFetch({ "/catalog": [{ id: "ctk-1", name: "CTK 1", timeframe: "1m" }, null, 42] });
  await act(async () => {
    render(<ResearchV2 />);
  });
  expect(await screen.findByRole("checkbox", { name: /CTK 1/ })).toBeInTheDocument();
});

it("downloads the selected coins without scoring anything", async () => {
  // The whole point of the separate step: fill the store for coins you have no
  // CSV for, before deciding what to test on them.
  const calls = stubFetch({
    "/v2/download": {
      id: "d".repeat(32),
      status: "running",
      phase: "Starting",
      progress: 0,
      results: [],
    },
  });
  await act(async () => {
    render(<ResearchV2 />);
  });
  await waitFor(() =>
    expect(screen.getByRole("checkbox", { name: /ETHUSDT/ })).toBeInTheDocument(),
  );
  fireEvent.click(screen.getByRole("checkbox", { name: /ETHUSDT/ }));
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /Download 2 coin/ }));
  });
  const post = calls.find(call => call.url.includes("/v2/download") && call.body);
  expect(post).toBeDefined();
  const sent = JSON.parse(post!.body!);
  expect(sent.symbols).toEqual(["BTCUSDT", "ETHUSDT"]);
  expect(sent.days).toBe(365);
  expect(sent.strategies).toBeUndefined();
});

it("can add every matching coin at once", async () => {
  // Selecting N pairs by hand is not a workflow.
  stubFetch();
  await act(async () => {
    render(<ResearchV2 />);
  });
  await waitFor(() => expect(screen.getByText(/2 of 2 coins match/)).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: /Add all 2 matching/ }));
  expect(screen.getByRole("button", { name: /Download 2 coin/ })).toBeEnabled();
});

it("shows a job started somewhere else and offers to stop it", async () => {
  // The 409 this replaces named no job and offered no way out.
  stubFetch({
    "/inventory": {
      ...INVENTORY,
      active_job: {
        id: "9".repeat(32),
        kind: "backtest",
        phase: "Signals: BTCUSDT - ctk-1 (1/3)",
        progress: 41,
        symbol: "1 symbol(s)",
        version: 2,
      },
    },
  });
  await act(async () => {
    render(<ResearchV2 />);
  });
  expect(await screen.findByText(/Another research job is running/)).toBeInTheDocument();
  expect(screen.getByText(/Signals: BTCUSDT - ctk-1/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Cancel it/ })).toBeEnabled();
  // Starting a second run is impossible, and the button must not imply that
  // the job in progress is this page's own.
  // Both the download and the backtest are blocked, so both say so.
  const blocked = screen.getAllByRole("button", { name: /Blocked by another job/ });
  expect(blocked).toHaveLength(2);
  blocked.forEach(button => expect(button).toBeDisabled());
  expect(screen.queryByRole("button", { name: /^Running/ })).not.toBeInTheDocument();
});

it("cancels the blocking job through its own id", async () => {
  const blocking = "9".repeat(32);
  const calls = stubFetch({
    "/inventory": {
      ...INVENTORY,
      active_job: {
        id: blocking,
        kind: "download",
        phase: "Downloading BTCUSDT 2026-09",
        progress: 10,
        symbol: "2 coin(s)",
        version: 2,
      },
    },
  });
  await act(async () => {
    render(<ResearchV2 />);
  });
  await act(async () => {
    fireEvent.click(await screen.findByRole("button", { name: /Cancel it/ }));
  });
  expect(calls.some(c => c.url.includes(`${blocking}/cancel`))).toBe(true);
});

it("does not offer to cancel a v1 job it does not own", async () => {
  stubFetch({
    "/inventory": {
      ...INVENTORY,
      active_job: {
        id: "8".repeat(32),
        kind: "backtest",
        phase: "Testing ctk-1",
        progress: 5,
        symbol: "BTCUSDT",
        version: 1,
      },
    },
  });
  await act(async () => {
    render(<ResearchV2 />);
  });
  expect(await screen.findByText(/Another research job is running/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Cancel it/ })).not.toBeInTheDocument();
});

it("warns before the run that a pinned strategy cannot use every coin", async () => {
  // Previously this surfaced one failure per coin, after the download had
  // already finished, and read like five things going wrong.
  stubFetch();
  await act(async () => {
    render(<ResearchV2 />);
  });
  await waitFor(() =>
    expect(screen.getByRole("checkbox", { name: /ETHUSDT/ })).toBeInTheDocument(),
  );
  fireEvent.click(screen.getByRole("checkbox", { name: /ETHUSDT/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: /BTC Selective Reversion/ }));
  await waitFor(() =>
    expect(screen.getByText(/1 coin \/ strategy pair will be skipped/)).toBeInTheDocument(),
  );
  expect(screen.getByText(/only runs on BTCUSDT/)).toBeInTheDocument();
});

it("marks a pinned strategy in the picker", async () => {
  stubFetch();
  await act(async () => {
    render(<ResearchV2 />);
  });
  const pinned = await screen.findByRole("checkbox", { name: /BTC Selective Reversion/ });
  expect(pinned.closest("label")?.textContent).toContain("BTCUSDT only");
  const open = screen.getByRole("checkbox", { name: /CTK 5/ });
  expect(open.closest("label")?.textContent).not.toContain("only");
});

it("says nothing when every selected coin can run every strategy", async () => {
  stubFetch();
  await act(async () => {
    render(<ResearchV2 />);
  });
  await waitFor(() => expect(screen.getByText(/2 of 2 coins match/)).toBeInTheDocument());
  fireEvent.click(screen.getByRole("checkbox", { name: /BTC Selective Reversion/ }));
  // Only BTCUSDT is selected by default, so nothing is excluded.
  expect(screen.queryByText(/will be skipped/)).not.toBeInTheDocument();
});

it("reports skipped pairs as skipped rather than as failures", async () => {
  const job = {
    id: "7".repeat(32),
    status: "completed",
    phase: "Completed",
    progress: 100,
    results: [],
    skipped: [
      { symbol: "DOGEUSDT", strategy: "btc-selective-reversion-1m", reason: "single dataset" },
    ],
  };
  localStorage.setItem("scalper-research-v2-job", job.id);
  stubFetch({ "/v2/jobs/": job });
  await act(async () => {
    render(<ResearchV2 />);
  });
  expect(await screen.findByText(/Skipped 1 pair/)).toBeInTheDocument();
  // A deliberate restriction is not an error and must not be styled as one.
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("keeps the run button usable when the inventory cannot be read", async () => {
  // The store is a cache, not a dependency: a failed inventory read must not
  // block a run that would download whatever it needs anyway.
  stubFetch({ "/inventory": null });
  await act(async () => {
    render(<ResearchV2 />);
  });
  expect(screen.getByRole("button", { name: /Run offline backtest/ })).toBeEnabled();
  expect(screen.getByText(/1 coin\(s\) need downloading first/)).toBeInTheDocument();
});

it("selects every searched strategy at once and the count follows the search", async () => {
  stubFetch({});
  await act(async () => {
    render(<ResearchV2 />);
  });
  await waitFor(() => expect(screen.getByRole("checkbox", { name: /CTK 5/ })).toBeInTheDocument());
  // The coin picker has its own Clear, so scope to the strategy panel.
  const panel = within(screen.getByText(/Strategies \(/).closest("details") as HTMLElement);

  fireEvent.click(panel.getByRole("button", { name: /Select all/ }));
  expect(screen.getByRole("checkbox", { name: /CTK 5/ })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /CTK 1/ })).toBeChecked();

  fireEvent.click(panel.getByRole("button", { name: /^Clear$/ }));
  expect(screen.getByRole("checkbox", { name: /CTK 5/ })).not.toBeChecked();

  // Narrowing the search narrows what "Select all" offers to take.
  fireEvent.change(panel.getByLabelText(/Search strategies/), { target: { value: "CTK 5" } });
  expect(panel.getByRole("button", { name: /Select all 1$/ })).toBeInTheDocument();
  fireEvent.click(panel.getByRole("button", { name: /Select all/ }));
  expect(screen.getByRole("checkbox", { name: /CTK 5/ })).toBeChecked();
});

/** Two coins x two strategies, one profitable pair each, for the filter tests. */
function fourResults() {
  const base = {
    run_id: "r",
    signals: 40,
    signals_cached: false,
    capital: 100,
    periods: 3,
    capital_deployed: 300,
    trades: 20,
    tp_hits: 12,
    sl_hits: 8,
    fees: 1,
  };
  return [
    { ...base, symbol: "BTCUSDT", strategy: "ctk-1", net: 5, gross_profit: 6, won: 9, lost: 4 },
    { ...base, symbol: "BTCUSDT", strategy: "ctk-5", net: -3, gross_profit: -2, won: 4, lost: 7 },
    { ...base, symbol: "ETHUSDT", strategy: "ctk-1", net: -8, gross_profit: -7, won: 2, lost: 10 },
    { ...base, symbol: "ETHUSDT", strategy: "ctk-5", net: 1, gross_profit: 2, won: 6, lost: 5 },
  ];
}

async function renderResults(state?: unknown, catalog: unknown[] = CATALOG) {
  const job = {
    id: "f".repeat(32),
    status: "completed",
    phase: "Completed",
    progress: 100,
    results: fourResults(),
  };
  localStorage.setItem("scalper-research-v2-job", job.id);
  const calls = stubFetch({ "/v2/jobs/": job, "/catalog": catalog });
  await act(async () => {
    render(<ResearchV2 state={state as never} connected />);
  });
  await waitFor(() => expect(screen.getAllByRole("row").length).toBe(5));
  return calls;
}

const bodyText = () => screen.getAllByRole("row").slice(1).map(r => r.textContent ?? "");

it("filters the results table down to one coin", async () => {
  // A run of 20 coins x 30 strategies is 600 rows; scrolling is not a way to
  // read it.
  await renderResults();
  fireEvent.change(screen.getByLabelText("Filter results"), { target: { value: "eth" } });
  const shown = bodyText();
  expect(shown).toHaveLength(2);
  expect(shown.every(text => text.includes("ETHUSDT"))).toBe(true);
});

it("filters the results table by strategy as well as coin", async () => {
  await renderResults();
  fireEvent.change(screen.getByLabelText("Filter results"), { target: { value: "ctk-5" } });
  const shown = bodyText();
  expect(shown).toHaveLength(2);
  expect(shown.every(text => text.includes("ctk-5"))).toBe(true);
});

it("narrows the results to the pairs that actually made money", async () => {
  // The point of the run is finding those, and they are a minority.
  await renderResults();
  fireEvent.click(screen.getByRole("button", { name: /Profitable only/ }));
  const shown = bodyText();
  expect(shown).toHaveLength(2);
  expect(shown[0]).toContain("BTCUSDT");
  expect(shown[1]).toContain("ETHUSDT");
  fireEvent.click(screen.getByRole("button", { name: /Profitable only/ }));
  expect(bodyText()).toHaveLength(4);
});

it("says so rather than showing an empty table when nothing matches", async () => {
  await renderResults();
  fireEvent.change(screen.getByLabelText("Filter results"), { target: { value: "solana" } });
  expect(screen.getByText(/No results match/)).toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});

it("reports how much of the run the filter is hiding", async () => {
  await renderResults();
  fireEvent.change(screen.getByLabelText("Filter results"), { target: { value: "btc" } });
  expect(screen.getByText(/Showing 2 of 4/)).toBeInTheDocument();
});

/**
 * A result row is where the decision gets made, so both actions that follow a
 * good result - save it, trade it - belong on the row itself rather than after
 * a retype into another tab.
 */
vi.mock("./FavoriteTrade", () => ({
  FavoriteTrade: ({ pair, name }: { pair: { symbol: string; strategy: string }; name: string }) => (
    <div>Trade setup {pair.symbol} / {pair.strategy} as {name}</div>
  ),
}));

it("saves a result row as a favorite through the API", async () => {
  const calls = await renderResults();
  const star = screen.getByRole("button", { name: "Favorite BTCUSDT / CTK 1" });
  expect(star).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(star);
  await waitFor(() => expect(star).toHaveAttribute("aria-pressed", "true"));
  // Saved on the server, not in this browser: the v1 tab and the Favorites tab
  // have to see the same pair.
  const saved = calls.find(c => c.url.includes("/favorites") && c.body);
  expect(saved && JSON.parse(saved.body as string)).toEqual({
    symbol: "BTCUSDT",
    strategy: "ctk-1",
  });
});

it("opens a trade setup for the row's exact coin and strategy", async () => {
  await renderResults({ mode: "testnet", strategy_monitor: { rows: [] } });
  fireEvent.click(screen.getByRole("button", { name: "Trade ETHUSDT / CTK 5" }));
  expect(screen.getByText("Trade setup ETHUSDT / ctk-5 as CTK 5")).toBeInTheDocument();
});

it("will not offer to trade a strategy the engine refuses to run", async () => {
  // Backtest-only and disabled strategies would fail at the engine; saying so
  // on the row beats a rejected bot.
  await renderResults(
    { mode: "testnet", strategy_monitor: { rows: [{ id: "ctk-5", disabled: true }] } },
    [
      { id: "ctk-1", name: "CTK 1", timeframe: "1m", backtest_only: true },
      { id: "ctk-5", name: "CTK 5", timeframe: "5m" },
    ],
  );
  expect(screen.getByRole("button", { name: "Trade BTCUSDT / CTK 1" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Trade ETHUSDT / CTK 5" })).toBeDisabled();
  // Saving one is still fine - a favorite is a note, not an order.
  expect(screen.getByRole("button", { name: "Favorite BTCUSDT / CTK 1" })).toBeEnabled();
});

it("gives take-profit and stop-loss hits their own columns", async () => {
  // They were a parenthetical next to the win rate, which is the one number
  // that cannot be scanned down a column.
  await renderResults();
  const headers = screen.getAllByRole("columnheader").map(h => h.textContent);
  expect(headers).toContain("TP hits");
  expect(headers).toContain("SL hits");
  const first = within(screen.getAllByRole("row")[1]).getAllByRole("cell").map(c => c.textContent);
  expect(first).toContain("12");
  expect(first).toContain("8");
});

it("selects every coin already stored offline, in one click and by name", async () => {
  // The button existed as "Only what is stored", which says neither how many
  // there are nor that it is the fast path: stored coins need no download.
  stubFetch({
    "/inventory": {
      symbols: [
        { symbol: "BTCUSDT", months: 1, first: "2026-08", last: "2026-09", candles: 1, megabytes: 1 },
        { symbol: "ETHUSDT", months: 1, first: "2026-08", last: "2026-09", candles: 1, megabytes: 1 },
      ],
      cached_signal_series: 0,
    },
  });
  await act(async () => {
    render(<ResearchV2 />);
  });
  const add = await screen.findByRole("button", { name: "Add all 2 stored offline" });
  fireEvent.click(add);
  expect(screen.getByText("2 selected")).toBeInTheDocument();
  expect(screen.getByText(/All selected coins are already stored offline/)).toBeInTheDocument();
});

it("adds the stored coins to a selection instead of replacing it", async () => {
  // Its neighbour adds; replacing would silently throw away a list built up
  // across several searches, which the helper text promises it will not.
  stubFetch({
    "/symbols": { source: "x", symbols: ["BTCUSDT", "ETHUSDT", "SOLUSDT"] },
    "/inventory": {
      symbols: [
        { symbol: "ETHUSDT", months: 1, first: "2026-08", last: "2026-09", candles: 1, megabytes: 1 },
      ],
      cached_signal_series: 0,
    },
  });
  await act(async () => {
    render(<ResearchV2 />);
  });
  fireEvent.click(await screen.findByRole("checkbox", { name: /SOLUSDT/ }));
  fireEvent.click(screen.getByRole("button", { name: "Add all 1 stored offline" }));
  expect(screen.getByText("3 selected")).toBeInTheDocument();
});

it("keeps Trade usable when the strategy catalog did not load", async () => {
  // A failed or slow catalog fetch is not evidence that a strategy cannot be
  // traded. Disabling on it made every Trade button dead while the API was
  // restarting, with nothing on screen to say why.
  await renderResults(undefined, []);
  expect(screen.getByRole("button", { name: "Trade BTCUSDT / ctk-1" })).toBeEnabled();
});

it("says why a Trade button is disabled instead of just greying out", async () => {
  await renderResults(
    { mode: "testnet", strategy_monitor: { rows: [{ id: "ctk-5", disabled: true }] } },
    [
      { id: "ctk-1", name: "CTK 1", timeframe: "1m", backtest_only: true },
      { id: "ctk-5", name: "CTK 5", timeframe: "5m" },
    ],
  );
  expect(screen.getByRole("button", { name: "Trade BTCUSDT / CTK 1" })).toHaveAttribute(
    "title",
    "Backtest only",
  );
  expect(screen.getByRole("button", { name: "Trade ETHUSDT / CTK 5" })).toHaveAttribute(
    "title",
    "Disabled in Strategy Monitor",
  );
});

it("reads a validation refusal instead of saying only that something failed", async () => {
  // FastAPI puts validation errors in `detail` as a LIST of objects. The
  // reader only understood a string, so "528 coins is over the limit" reached
  // the operator as "Request failed." - or as a bare 422 in the console.
  stubFetch({
    "/v2/jobs": {
      __status: 422,
      detail: [
        { type: "too_long", loc: ["body", "symbols"], msg: "List should have at most 600 items" },
      ],
    },
  });
  await act(async () => {
    render(<ResearchV2 />);
  });
  fireEvent.click(await screen.findByRole("button", { name: "Run offline backtest" }));
  expect(await screen.findByText(/symbols: List should have at most 600 items/)).toBeInTheDocument();
});
