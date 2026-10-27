import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { engineControl } from "../lib/api";
import { EngineControls } from "./EngineControls";

vi.mock("../lib/api", () => ({ engineControl: vi.fn() }));
beforeEach(() => vi.resetAllMocks());

it("requires an explicit second click before emergency close", async () => {
  vi.mocked(engineControl).mockResolvedValue(undefined);
  render(<EngineControls available halted={false} />);
  fireEvent.click(screen.getByRole("button", { name: "PANIC" }));
  expect(engineControl).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm close" }));
  await waitFor(() => expect(engineControl).toHaveBeenCalledWith("panic"));
  expect(await screen.findByRole("status")).toHaveTextContent("Request accepted");
});

it("does not offer control while the engine is unavailable", () => {
  render(<EngineControls available={false} halted />);
  expect(screen.getByRole("button", { name: "PANIC" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Reconcile and resume" })).toBeDisabled();
});

it("reports a failed control request", async () => {
  vi.mocked(engineControl).mockRejectedValue(new Error("Engine unavailable"));
  render(<EngineControls available halted />);
  fireEvent.click(screen.getByRole("button", { name: "Reconcile and resume" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Engine unavailable");
});
