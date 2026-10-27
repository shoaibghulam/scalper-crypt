import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { configureSingleCoin, type AppState } from "../lib/api";
import { SingleCoin } from "./SingleCoin";

vi.mock("../lib/api", () => ({ configureSingleCoin: vi.fn() }));
vi.mock("./BotFleet", () => ({ BotFleet: () => <div>Search bot</div> }));
vi.mock("./PositionTable", () => ({ PositionTable: () => <div>Managed position</div> }));
beforeEach(() => vi.clearAllMocks());
function mount(overrides: Partial<AppState> = {}) {
  const state = { mode: "testnet", trading_enabled: true, max_leverage: 10,
    bots: [], positions: [], ...overrides } as AppState;
  render(<QueryClientProvider client={new QueryClient()}>
    <SingleCoin state={state} connected />
  </QueryClientProvider>);
}
it.each(["5m", "10m"] as const)("sends selected %s timeframe only on click", async (timeframe) => {
  vi.mocked(configureSingleCoin).mockResolvedValue(undefined);
  mount({ strategy_monitor: { auto_disable: false, persistent: true, rows: [{
    id: "rolling-breakout-retest", name: "Qualified breakout", single_coin_timeframe: timeframe,
    trades: 30, wins: 21, losses: 9, breakeven: 0, win_rate: 70, loss_rate: 30,
    net: 5, fees: 1, expectancy: .2, profit_factor: 2, recent_trades: 20,
    recent_losses: 6, flagged: false, disabled: false, reason: null, unverified: 0,
  }] } });
  expect(screen.getByLabelText("Capital budget (USDT)")).toHaveValue(10);
  expect(configureSingleCoin).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Entry candles"), { target: { value: timeframe } });
  fireEvent.click(screen.getByRole("button", { name: "Find one coin and trade" }));
  await waitFor(() => expect(configureSingleCoin).toHaveBeenCalledWith("10", 3, timeframe, false));
});
it.each([{ mode: "live" }, { trading_enabled: false }])("blocks unsafe start %j", overrides => {
  mount(overrides as Partial<AppState>);
  expect(screen.getByRole("button", { name: "Find one coin and trade" })).toBeDisabled();
});
it("blocks a second search while a previous position is managed", () => {
  mount({ single_coin: { enabled: false, bot_id: "s" },
    bots: [{ id: "s", open_positions: 1 }] as AppState["bots"] });
  expect(screen.getByRole("button", { name: "Find one coin and trade" })).toBeDisabled();
});
it("can stop a search even while trading is disabled", async () => {
  mount({ trading_enabled: false, single_coin: { enabled: true, amount: "10", leverage: 3, timeframe: "1m" } });
  fireEvent.click(screen.getByRole("button", { name: "Stop search" }));
  await waitFor(() => expect(configureSingleCoin).toHaveBeenCalledWith("10", 3, "1m", true));
});

it("explains insufficient evidence and does not silently choose a default strategy", () => {
  mount();
  expect(screen.getByText(/No qualifying strategy yet/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Find one coin and trade" })).toBeDisabled();
});

it("selects the early testnet leader and labels the small sample", () => {
  const row = { id: "lab-01", name: "Early winner", single_coin_timeframe: "1m" as const,
    trades: 1, wins: 1, losses: 0, breakeven: 0, win_rate: 100, loss_rate: 0,
    net: 1, fees: .1, expectancy: 1, profit_factor: null, recent_trades: 1,
    recent_losses: 0, flagged: false, disabled: false, reason: null, unverified: 0 };
  mount({ strategy_monitor: { auto_disable: false, persistent: true, rows: [
    { ...row, id: "lab-02", name: "Lower win rate", trades: 30, win_rate: 70 }, row,
    { ...row, id: "lab-03", name: "Disabled winner", disabled: true, net: 10 },
  ] } });
  expect(screen.getByText("Early winner")).toBeInTheDocument();
  expect(screen.queryByText("Lower win rate")).not.toBeInTheDocument();
  expect(screen.queryByText("Disabled winner")).not.toBeInTheDocument();
  expect(screen.getByText(/Early testnet leader/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Find one coin and trade" })).toBeEnabled();
});

it("ranks by win count rather than profit amount or win percentage", () => {
  const row = { id: "lab-01", name: "Most wins", single_coin_timeframe: "1m" as const,
    trades: 10, wins: 7, losses: 3, breakeven: 0, win_rate: 70, loss_rate: 30,
    net: 1, fees: .1, expectancy: .1, profit_factor: 2, recent_trades: 10,
    recent_losses: 3, flagged: false, disabled: false, reason: null, unverified: 0 };
  mount({ strategy_monitor: { auto_disable: false, persistent: true, rows: [
    { ...row, id: "lab-02", name: "Bigger profit", trades: 1, wins: 1, win_rate: 100, net: 100 }, row,
  ] } });
  expect(screen.getByText("Most wins")).toBeInTheDocument();
  expect(screen.queryByText("Bigger profit")).not.toBeInTheDocument();
  expect(screen.getByText(/7 wins/)).toBeInTheDocument();
});
