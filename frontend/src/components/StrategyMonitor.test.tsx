import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { StrategyMonitor, type StrategyResult } from "./StrategyMonitor";
import type { AppState } from "../lib/api";

afterEach(() => vi.unstubAllGlobals());
const row: StrategyResult = { id: "lab-01", name: "EMA cross", trades: 20, wins: 5, losses: 15,
  breakeven: 0, win_rate: 25, loss_rate: 75, net: -10, fees: 2, expectancy: -.5,
  profit_factor: .3, recent_trades: 20, recent_losses: 15, flagged: true, disabled: false,
  reason: null, unverified: 3 };
function mount(connected = true) {
  render(<StrategyMonitor connected={connected} state={{ mode: "testnet",
    strategy_monitor: { rows: [row], auto_disable: false, persistent: true },
  } as AppState} />);
}
it("shows review evidence and sends an explicit disable without deleting history", async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true }); vi.stubGlobal("fetch", fetch);
  mount();
  expect(screen.getByText("15/20")).toBeInTheDocument();
  expect(screen.getByText("Review")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Disable lab-01" }));
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ strategy: "lab-01", disabled: true });
  expect(screen.getByText("15/20")).toBeInTheDocument();
});
it("blocks disconnected controls and defaults auto-disabling off", () => {
  mount(false);
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(screen.getByRole("checkbox")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Disable lab-01" })).toBeDisabled();
});

it("filters by summary cards and search without changing global totals", () => {
  render(<StrategyMonitor connected state={{ mode: "testnet", strategy_monitor: {
    rows: [row, { ...row, id: "lab-02", name: "Disabled sample", disabled: true }],
    auto_disable: false, persistent: true,
  } } as AppState} />);
  const disabled = screen.getByRole("button", { name: /Disabled strategies 1/ });
  fireEvent.click(disabled);
  expect(disabled).toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByRole("button", { name: "Disable lab-01" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Enable lab-02" })).toBeInTheDocument();
  expect(screen.getByText("Showing 1 of 2")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("searchbox", { name: "Search strategies" }), { target: { value: "unknown" } });
  expect(screen.getByText(/No strategies match/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(screen.getByText("Showing 2 of 2")).toBeInTheDocument();
});
