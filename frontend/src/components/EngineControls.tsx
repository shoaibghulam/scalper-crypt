import { useState } from "react";
import { engineControl } from "../lib/api";

export function EngineControls({ available, halted }: { available: boolean; halted: boolean }) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function send(action: "panic" | "resume") {
    setBusy(true);
    setMessage("");
    try {
      await engineControl(action);
      setMessage("Request accepted. Check the engine result before assuming completion.");
      setConfirm(false);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Control request failed");
    } finally { setBusy(false); }
  }
  return <span className="strip-panic">
    {confirm ? <>
      <span>Halt trading and close all managed positions?</span>
      <button disabled={!available || busy} onClick={() => void send("panic")}>Confirm close</button>
      <button disabled={busy} onClick={() => setConfirm(false)}>Cancel</button>
    </> : <button disabled={!available || busy} onClick={() => setConfirm(true)}
      title="Halt new entries and close engine-managed positions; unrelated positions are untouched">
      PANIC
    </button>}
    {halted && <button disabled={!available || busy} onClick={() => void send("resume")}>
      Reconcile and resume
    </button>}
    {message && <span role="status">{message}</span>}
  </span>;
}
