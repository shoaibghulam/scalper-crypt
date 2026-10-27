import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { TpSlBar } from "./TpSlBar";

it.each([
  ["LONG", 90, 120, 118, "Approaching take profit"],
  ["LONG", 90, 120, 91, "Approaching stop loss"],
  ["SHORT", 110, 80, 82, "Approaching take profit"],
  ["SHORT", 110, 80, 109, "Approaching stop loss"],
] as const)("shows correct proximity for %s", (side, stop, target, mark, message) => {
  render(<TpSlBar side={side} entry={100} stop={stop} target={target} mark={mark} decimals={2} />);
  expect(screen.getByText(message)).toBeInTheDocument();
});

it("distinguishes a touched level from a confirmed fill and updates across ticks", () => {
  const props = { side: "LONG" as const, entry: 100, stop: 90, target: 120, decimals: 2 };
  const { rerender } = render(<TpSlBar {...props} mark={100} />);
  expect(screen.getByText("Tracking mark price")).toBeInTheDocument();
  rerender(<TpSlBar {...props} mark={121} />);
  expect(screen.getByText("Target level reached · awaiting fill confirmation")).toBeInTheDocument();
  rerender(<TpSlBar {...props} mark={89} />);
  expect(screen.getByText("Stop level reached · awaiting fill confirmation")).toBeInTheDocument();
});

it("does not present a stale price as a live trigger", () => {
  render(<TpSlBar side="LONG" entry={100} stop={90} target={120} mark={121} decimals={2} live={false} />);
  expect(screen.getByText("Price feed stale")).toBeInTheDocument();
  expect(screen.queryByText(/awaiting fill confirmation/)).not.toBeInTheDocument();
});

it("keeps the liquidation warning visible alongside the animation", () => {
  render(<TpSlBar side="LONG" entry={100} stop={90} target={120}
    mark={91} liquidation={95} decimals={2} />);
  expect(screen.getByText(/LIQUIDATION INSIDE STOP/)).toBeInTheDocument();
  expect(screen.getByText("Approaching stop loss")).toBeInTheDocument();
});

it("retains price and risk information as accessible text", () => {
  render(<TpSlBar side="LONG" entry={100} stop={90} target={120} mark={110} decimals={2} />);
  expect(screen.getByText(/LONG position\. Entry/)).toHaveTextContent("Reward to risk 2.00");
  expect(screen.getByText("+50%")).toBeInTheDocument();
});
