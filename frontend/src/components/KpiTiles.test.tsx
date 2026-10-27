import { render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { KpiTiles } from "./KpiTiles";
import type { Totals } from "../lib/api";

it("distinguishes unavailable balance from a verified zero and recovers", () => {
  const totals: Totals = {
    wallet: "0", available: "0", equity: "0", margin_used: "0",
    unrealised_pnl: "0", realised_today: "0", open_positions: 0,
    bots_running: 0, bots_stopped: 0, bots_error: 0,
    tp_hits_today: 0, sl_hits_today: 0, balance_known: false,
  };
  const { rerender } = render(<KpiTiles totals={totals} />);
  const equity = screen.getByText("Equity").closest("section")!;
  expect(within(equity).getByText("Unavailable")).toBeInTheDocument();
  expect(within(equity).queryByText(/free/)).not.toBeInTheDocument();
  rerender(<KpiTiles totals={{ ...totals, balance_known: true }} />);
  expect(within(equity).getByText("$0.00")).toBeInTheDocument();
  expect(within(equity).queryByText("Unavailable")).not.toBeInTheDocument();
});
