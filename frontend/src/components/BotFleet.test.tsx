import { fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BotFleet } from "./BotFleet";
import type { Bot } from "../lib/api";

/**
 * The dismiss button invalidates the state query, so the panel needs a client
 * in scope. A fresh one per render keeps cached data from leaking between
 * tests and turning an ordering assertion into a coin flip.
 */
function render(ui: React.ReactElement) {
  return rtlRender(
    <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>,
  );
}

/**
 * The regression this panel exists for.
 *
 * Five bots were created at 50x, every symbol was refused by the exchange, and
 * the dashboard showed nothing at all: it rendered positions, and a bot that
 * failed to open one has none. Six recorded rejections were invisible.
 *
 * So the tests that matter here are about failures being impossible to miss,
 * not about layout.
 */

function aBot(overrides: Partial<Bot> = {}): Bot {
  return {
    id: "b1",
    name: "VWAP Band Reversion 2",
    strategy: "vwap-band-reversion",
    state: "running",
    open_positions: 0,
    pnl_today: 0,
    tp_hits: 0,
    sl_hits: 0,
    symbols: ["BTCUSDT", "ETHUSDT"],
    margin_mode: "ISOLATED",
    error: null,
    ...overrides,
  };
}

const REJECTION =
  "INJUSDT: [-4028] Leverage 50 is not valid -- leverage invalid for this " +
  "symbol and notional tier; check leverageBracket";

describe("BotFleet", () => {
  it("shows a bot that holds no positions at all", () => {
    // The whole point. Before this panel, such a bot rendered nowhere.
    render(<BotFleet bots={[aBot()]} />);
    expect(screen.getByText("VWAP Band Reversion 2")).toBeInTheDocument();
  });

  it("shows the exchange rejection verbatim", () => {
    // Not "failed". The leverage number and the reason are the actionable
    // parts, and an abbreviated message throws away what to change.
    render(<BotFleet bots={[aBot({ state: "error", error: REJECTION })]} />);
    expect(screen.getByText(REJECTION)).toBeInTheDocument();
  });

  it("announces a failure to assistive technology", () => {
    render(<BotFleet bots={[aBot({ state: "error", error: REJECTION })]} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Leverage 50 is not valid");
  });

  it("counts how many bots are failing", () => {
    render(
      <BotFleet
        bots={[
          aBot({ id: "a", state: "error", error: REJECTION }),
          aBot({ id: "b", state: "error", error: REJECTION }),
          aBot({ id: "c" }),
        ]}
      />,
    );
    expect(screen.getByText(/2 in error/)).toBeInTheDocument();
  });

  it("puts failing bots first", () => {
    // Burying a broken bot under working ones is how six went unnoticed.
    render(
      <BotFleet
        bots={[
          aBot({ id: "ok", name: "Working bot", open_positions: 2 }),
          aBot({ id: "bad", name: "Broken bot", state: "error", error: REJECTION }),
        ]}
      />,
    );
    const names = screen.getAllByText(/bot$/i).map((n) => n.textContent);
    expect(names[0]).toBe("Broken bot");
  });

  it("distinguishes waiting from in trade", () => {
    // A bot with no positions is not necessarily broken, and saying so avoids
    // sending the operator looking for a fault that is not there.
    render(<BotFleet bots={[aBot({ id: "w" })]} />);
    expect(screen.getByText("waiting")).toBeInTheDocument();

    render(<BotFleet bots={[aBot({ id: "t", open_positions: 3 })]} />);
    expect(screen.getByText("in trade")).toBeInTheDocument();
  });

  it("lists the symbols the bot was given", () => {
    render(<BotFleet bots={[aBot()]} />);
    expect(screen.getByText("BTCUSDT ETHUSDT")).toBeInTheDocument();
  });

  it("explains itself when there are no bots", () => {
    render(<BotFleet bots={[]} />);
    expect(screen.getByText("No bots yet")).toBeInTheDocument();
  });
});

describe("BotFleet margin mode", () => {
  it("shows which margin mode a bot is using", () => {
    // It decides whether a bad position can reach the rest of the account, so
    // it belongs on the row rather than only in the form that created it.
    render(<BotFleet bots={[aBot({ margin_mode: "CROSSED" })]} />);
    expect(screen.getByText("cross")).toBeInTheDocument();
  });

  it("labels isolated bots too", () => {
    render(<BotFleet bots={[aBot()]} />);
    expect(screen.getByText("isolated")).toBeInTheDocument();
  });
});

describe("BotFleet dismissal", () => {
  it("offers to dismiss a bot that holds nothing", () => {
    // The gap that left eight dead bots on screen with no way to clear them.
    render(<BotFleet bots={[aBot({ state: "error", error: REJECTION })]} />);
    expect(
      screen.getByRole("button", { name: /Dismiss VWAP/ }),
    ).toBeInTheDocument();
  });

  it("does not offer to dismiss a bot holding a position", () => {
    // The engine refuses it, and deleting the record would turn a managed
    // position into an orphan. A button that exists only to be refused is
    // worse than no button.
    render(<BotFleet bots={[aBot({ open_positions: 2 })]} />);
    expect(
      screen.queryByRole("button", { name: /Dismiss VWAP/ }),
    ).not.toBeInTheDocument();
  });

  it("names the bot in the accessible label", () => {
    // Several rows carry the same button text, so the label is the only thing
    // distinguishing them to a screen reader.
    render(<BotFleet bots={[aBot({ name: "Broken bot" })]} />);
    expect(
      screen.getByRole("button", { name: "Dismiss Broken bot" }),
    ).toBeInTheDocument();
  });
});


it("keeps a bot with pending settlement and explains why", () => {
  render(<BotFleet bots={[aBot({ pending_closures: 1 })]} />);
  expect(screen.queryByRole("button", { name: /Dismiss VWAP/ })).not.toBeInTheDocument();
  expect(screen.getByText("settlement pending")).toBeInTheDocument();
});

it('shows a separate waiting reason for every watched symbol', () => {
  render(<BotFleet bots={[aBot({continuous:true, symbols:['DASHUSDT','AVAXUSDT','ONEUSDT','RAYSOLUSDT'],
    symbol_notes:{DASHUSDT:'No band re-entry',AVAXUSDT:'Trend too strong',ONEUSDT:'Waiting for candle',RAYSOLUSDT:'Insufficient target room'},last_checked_at:120})]} />);
  expect(screen.getByText('DASHUSDT')).toBeInTheDocument();
  expect(screen.getByText(/No band re-entry/)).toBeInTheDocument();
  expect(screen.getByText(/Trend too strong/)).toBeInTheDocument();
  expect(screen.getByText(/Insufficient target room/)).toBeInTheDocument();
  expect(screen.getByText(/Last checked:/)).toBeInTheDocument();
});

it("shows four bots initially and expands four at a time", () => {
  render(<BotFleet bots={Array.from({ length: 9 }, (_, n) => aBot({ id: `b${n}`, name: `Bot ${n}` }))} />);
  expect(screen.getByText("Showing 4 of 9 bots")).toBeInTheDocument();
  expect(screen.queryByText("Bot 4")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show more bots (4)" }));
  expect(screen.getByText("Showing 8 of 9 bots")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show fewer bots" }));
  expect(screen.getByText("Showing 4 of 9 bots")).toBeInTheDocument();
});
it("shows four watched symbols and expands the remaining details", () => {
  render(<BotFleet bots={[aBot({ continuous: true, symbols: ["A", "B", "C", "D", "E"] })]} />);
  expect(screen.queryByText("E", { selector: "strong" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show more symbols (1)" }));
  expect(screen.getByText("E", { selector: "strong" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show fewer symbols" }));
  expect(screen.queryByText("E", { selector: "strong" })).not.toBeInTheDocument();
});

afterEach(() => vi.unstubAllGlobals());
it("bulk dismiss includes hidden empty bots and excludes protected bots", async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true });
  vi.stubGlobal("fetch", fetch);
  const bots = Array.from({ length: 7 }, (_, n) => aBot({ id: `b${n}`, name: `Bot ${n}` }));
  bots[0].open_positions = 1;
  bots[1].entry_pending = true;
  bots[2].pending_closures = 1;
  render(<BotFleet bots={bots} />);
  fireEvent.click(screen.getByRole("button", { name: "Dismiss all (4)" }));
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  expect(fetch.mock.calls[0][0]).toBe("/api/bots/dismiss");
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ bot_ids: ["b3", "b4", "b5", "b6"] });
});
