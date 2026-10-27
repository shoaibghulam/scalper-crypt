import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { AutoTrade } from "./AutoTrade";
import { configureAutoTrade, type AppState } from "../lib/api";

vi.mock("../lib/api", () => ({ configureAutoTrade: vi.fn() }));
vi.mock("../hooks/useScan", () => ({ useStrategies: () => ({ data: { strategies: [] } }) }));
vi.mock("./BotFleet", () => ({ BotFleet: () => <div>Auto fleet</div> }));
beforeEach(() => vi.clearAllMocks());
function mount(overrides: Partial<AppState> = {}, connected = true) {
  const state = { mode: "testnet", trading_enabled: true, max_leverage: 10, bots: [],
    auto_trade: { enabled: false }, ...overrides } as AppState;
  render(<QueryClientProvider client={new QueryClient()}>
    <AutoTrade state={state} connected={connected} />
  </QueryClientProvider>);
}
it("starts only after explicit click with chosen margin and leverage", async () => {
  vi.mocked(configureAutoTrade).mockResolvedValue(undefined);
  mount();
  expect(configureAutoTrade).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Margin per position (USDT)"), { target: { value: "25" } });
  fireEvent.change(screen.getByLabelText("Leverage"), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: "Start Auto Trade" }));
  await waitFor(() => expect(configureAutoTrade).toHaveBeenCalledWith("25", 3, false));
  expect(screen.getByText(/Request sent/)).toBeInTheDocument();
});
it.each([{ mode: "live" }, { trading_enabled: false }])("blocks unsafe start %j", overrides => {
  mount(overrides as Partial<AppState>);
  expect(screen.getByRole("button", { name: "Start Auto Trade" })).toBeDisabled();
});
it("blocks invalid amount and leverage above the cap", () => {
  mount();
  expect(screen.getByRole("option", { name: "20x" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Margin per position (USDT)"), { target: { value: "0" } });
  expect(screen.getByRole("button", { name: "Start Auto Trade" })).toBeDisabled();
});
it("allows stop even when new trades are blocked", async () => {
  mount({ trading_enabled: false, auto_trade: { enabled: true, amount: "25", leverage: 3 } });
  fireEvent.click(screen.getByRole("button", { name: "Stop Auto Trade entries" }));
  await waitFor(() => expect(configureAutoTrade).toHaveBeenCalledWith("25", 3, true));
});

it("allows restart while individual bots settle and explains the protection", () => {
  mount({ auto_trade: { enabled: false, bot_ids: ["old"] }, bots: [{
    id: "old", open_positions: 1, pending_closures: 0, entry_pending: false,
  }] as AppState["bots"] });
  expect(screen.getByRole("button", { name: "Start Auto Trade" })).toBeEnabled();
  expect(screen.getByText(/Restarting preserves their positions and history/)).toBeInTheDocument();
  expect(screen.getByText(/no top-20 or top-60 coin cap/)).toBeInTheDocument();
});

it("does not keep showing an old restart refusal after it becomes stale", () => {
  mount({ last_action: { kind: "auto_trade", ok: false, at: Date.now() / 1000 - 120,
    detail: "Previous Auto Trade positions must settle before restarting" } });
  expect(screen.queryByText("Previous Auto Trade positions must settle before restarting")).not.toBeInTheDocument();
});

it("syncs a running fleet without requiring stop", async () => {
  mount({ auto_trade: { enabled: true, amount: "25", leverage: 3 } });
  fireEvent.click(screen.getByRole("button", { name: "Sync strategies" }));
  await waitFor(() => expect(configureAutoTrade).toHaveBeenCalledWith("25", 3, false));
});
it("updates the running fleet with new future-entry settings", async () => {
  mount({ auto_trade: { enabled: true, amount: "25", leverage: 3 } });
  fireEvent.change(screen.getByLabelText("Margin per position (USDT)"), { target: { value: "40" } });
  fireEvent.change(screen.getByLabelText("Leverage"), { target: { value: "5" } });
  fireEvent.click(screen.getByRole("button", { name: "Update all strategies" }));
  await waitFor(() => expect(configureAutoTrade).toHaveBeenCalledWith("40", 5, false));
  expect(screen.getByText(/Open positions retain their original settings/)).toBeInTheDocument();
});
it("blocks updates in safe mode while allowing stop", () => {
  mount({ trading_enabled: false, auto_trade: { enabled: true, amount: "25", leverage: 3 } });
  expect(screen.getByRole("button", { name: "Sync strategies" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Stop Auto Trade entries" })).toBeEnabled();
});
