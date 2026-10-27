import { memo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Bot } from "../lib/api";
import { dismissBots, removeBot, stopBot } from "../lib/api";
import { formatUsd, pnlTone } from "../lib/format";
import "./BotFleet.css";

/**
 * Every bot, including the ones holding nothing.
 *
 * This exists because of a specific failure. Five bots were created at 50x,
 * every symbol was refused by the exchange, and the dashboard showed a blank
 * screen: the Overview rendered positions, a failed bot has none, so six
 * recorded errors were invisible. The operator had no way to learn that
 * anything had happened at all, let alone what.
 *
 * So a bot in error is the loudest row here, and its message is shown in full
 * rather than truncated. Exchange rejections are specific and actionable -
 * "Leverage 50 is not valid", "liquidation sits only 1.21 stop-widths beyond
 * the stop" - and shortening them to "failed" would throw away the only part
 * that tells you what to change.
 */

interface Props {
  bots: Bot[];
}

function stateLabel(bot: Bot): string {
  if (bot.entry_pending) return "entry outcome unknown";
  if ((bot.pending_closures ?? 0) > 0) return "settlement pending";
  if (bot.state === "error") return "error";
  if (bot.state === "stopped") return "stopped";
  return bot.open_positions > 0 ? "in trade" : "waiting";
}

function DismissButton({ bot }: { bot: Bot }) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Offered only when the bot holds nothing. The engine refuses otherwise, and
  // a button that exists to be refused is worse than no button.
  if (bot.entry_pending || bot.open_positions > 0 || (bot.pending_closures ?? 0) > 0) return null;

  async function dismiss() {
    setBusy(true);
    setError(null);
    try {
      await removeBot(bot.id);
      await queryClient.invalidateQueries({ queryKey: ["state"] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not dismiss bot");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
    {error && <span role="alert">{error}</span>}
    <button
      type="button"
      className="bot-dismiss"
      onClick={dismiss}
      disabled={busy}
      aria-label={`Dismiss ${bot.name}`}
    >
      {busy ? "..." : "Dismiss"}
    </button>
    </>
  );
}

function StopButton({ bot }: { bot: Bot }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!bot.continuous || bot.state !== "running") return null;
  async function stop() {
    setBusy(true);
    try { await stopBot(bot.id); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Stop failed"); }
    finally { setBusy(false); }
  }
  return <>
    <button disabled={busy} onClick={() => void stop()}
      title="Stop future entries; existing positions keep their exchange protection">Stop entries</button>
    {error && <span role="alert">{error}</span>}
  </>;
}

function BotRow({ bot }: { bot: Bot }) {
  const pnl = bot.pnl_today;
  const [showAllSymbols, setShowAllSymbols] = useState(false);
  const visibleSymbols = showAllSymbols ? bot.symbols : bot.symbols.slice(0, 4);

  return (
    <li data-state={bot.state}>
      <div className="bot-head">
        <span className="bot-name">{bot.name}</span>
        <span className="bot-state" data-state={bot.state}>
          {stateLabel(bot)}
        </span>
        <span className="bot-strategy">{bot.strategy}</span>
        <span className="bot-margin" data-mode={bot.margin_mode}>
          {bot.margin_mode === "CROSSED" ? "cross" : "isolated"}
        </span>
        <span className="bot-pnl num" data-tone={pnlTone(pnl)}>
          {formatUsd(pnl)}
        </span>
        <StopButton bot={bot} />
        <DismissButton bot={bot} />
      </div>

      <div className="bot-meta">
        <span className="num">
          {bot.open_positions} open {"·"} {bot.tp_hits} target {"·"}{" "}
          {bot.sl_hits} stop
        </span>
        {bot.symbols.length > 0 && (
          <span className="bot-symbols num">{visibleSymbols.join(" ")}{!showAllSymbols && bot.symbols.length > 4 ? ` +${bot.symbols.length - 4} more` : ""}</span>
        )}
      </div>

          {bot.symbols.length > 4 && <button type="button" aria-expanded={showAllSymbols}
            onClick={() => setShowAllSymbols(!showAllSymbols)}>
            {showAllSymbols ? "Show fewer symbols" : `Show more symbols (${bot.symbols.length - 4})`}
          </button>}
      {bot.error && (
        // Verbatim and unwrapped. The exchange message is the actionable part.
        <p className="bot-error" role="alert">
          {bot.error}
        </p>
      )}
      {bot.continuous && (
        <div className="bot-meta" aria-label={`Watched symbols for ${bot.name}`}>
          {bot.symbols.length === 0 && <span>Waiting for markets that meet volume and spread requirements.</span>}
          <ul className="bot-watch-list">
            {visibleSymbols.map((symbol) => (
              <li key={symbol}>
                <strong>{symbol}</strong>: {bot.symbol_notes?.[symbol]?.replace(`${symbol}: `, "") ?? "Waiting for the next scheduled signal check"}
              </li>
            ))}
          </ul>
          {(bot.last_checked_at ?? 0) > 0 && (
            <span>Last checked: {new Date(bot.last_checked_at! * 1000).toLocaleTimeString()}</span>
          )}
        </div>
      )}
    </li>
  );
}

function DismissAllButton({ bots }: Props) {
  const queries = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const eligible = bots.filter(b => !b.entry_pending && b.open_positions === 0 && !b.pending_closures);
  async function dismiss() {
    setBusy(true); setError(""); setMessage("");
    try {
      await dismissBots(eligible.map(b => b.id));
      setMessage("Dismiss requested. Protected or pending bots stay; trade history is preserved.");
      await queries.invalidateQueries({ queryKey: ["state"] });
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Dismiss all failed"); }
    finally { setBusy(false); }
  }
  return <div className="fleet-dismiss-all">
    <button type="button" disabled={busy || eligible.length === 0} onClick={() => void dismiss()}
      title="Dismiss all eligible bots in this list, including those hidden by Show more. Open or pending positions stay managed.">
      {busy ? "Dismissing..." : `Dismiss all (${eligible.length})`}</button>
    {message && <span role="status">{message}</span>}{error && <span role="alert">{error}</span>}
  </div>;
}

function BotFleetInner({ bots }: Props) {
  const [visibleCount, setVisibleCount] = useState(4);
  if (bots.length === 0) {
    return (
      <section className="fleet">
        <div className="fleet-head">
          <h2>Bots</h2>
        </div>
        <div className="empty">
          <h3>No bots yet</h3>
          <p>
            Create one from the Create Scalp tab. It will appear here whether or
            not it manages to open a position, along with any reason the
            exchange gives for refusing.
          </p>
        </div>
      </section>
    );
  }

  // Failures first. A bot that cannot trade is the thing most worth reading,
  // and burying it under working bots is how six of them went unnoticed.
  const ordered = [...bots].sort((a, b) => {
    const rank = (bot: Bot) =>
      bot.state === "error" ? 0 : bot.open_positions > 0 ? 1 : 2;
    return rank(a) - rank(b);
  });

  const failing = bots.filter((b) => b.state === "error").length;

  return (
    <section className="fleet">
      <div className="fleet-head">
        <h2>Bots</h2>
        <span className="fleet-count num">
          {bots.length} total
          {failing > 0 && <span className="fleet-failing"> {failing} in error</span>}
        </span>
      </div>
      <DismissAllButton bots={bots} />
      <ul className="fleet-list">
        {ordered.slice(0, visibleCount).map((bot) => (
          <BotRow key={bot.id} bot={bot} />
        ))}
      </ul>
      {bots.length > 4 && <div className="fleet-pagination">
        <span>Showing {Math.min(visibleCount, bots.length)} of {bots.length} bots</span>
        {visibleCount < bots.length && <button type="button" onClick={() => setVisibleCount(n => n + 4)}>
          Show more bots ({Math.min(4, bots.length - visibleCount)})</button>}
        {visibleCount > 4 && <button type="button" onClick={() => setVisibleCount(4)}>Show fewer bots</button>}
      </div>}
    </section>
  );
}

// Memoised deliberately. App runs three independent one-second timers (health,
// state, and the clock for age displays) and a re-render of App re-invokes
// every child.
export const BotFleet = memo(BotFleetInner);
