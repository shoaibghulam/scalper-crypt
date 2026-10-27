import { memo, useState } from "react";
import { createBot } from "../lib/api";
import type { Candidate, CredentialStatus, MarginMode } from "../lib/api";
import { formatPrice } from "../lib/format";
import {
  MIN_HEADROOM,
  SAFE_HEADROOM,
  bindingStopFraction,
  headroom,
  recommendedLeverage,
} from "../lib/leverage";
import { useQueryClient } from "@tanstack/react-query";
import { useScan, useStrategies } from "../hooks/useScan";
import { useNow } from "../hooks/useNow";
import type { FavoritePair } from "../hooks/useFavorites";
import "./CreateScalp.css";

/**
 * Pick what to trade.
 *
 * The list is every symbol that is economically tradeable right now, ranked.
 * Economic gates are hard: too thin, too quiet, or a stop too tight to clear
 * its own fees means the symbol is excluded and the reason is recorded. Regime
 * and volume are shown rather than enforced, because they describe this minute
 * rather than this symbol, and hiding everything that is not firing right now
 * leaves a blank screen that teaches the operator nothing.
 *
 * Rejections are visible on purpose. A scanner that returns a short list with
 * no explanation cannot be tuned, and an untunable scanner gets loosened by
 * guesswork until the edge is gone.
 */

function costBand(costToStop: number): string {
  if (costToStop <= 0.15) return "good";
  if (costToStop <= 0.25) return "tight";
  return "bad";
}

interface Props {
  initialPair?: FavoritePair;
  engineConnected: boolean;
  credentials: CredentialStatus | undefined;
  /** False while the engine is in safe mode and will refuse everything. */
  tradingEnabled: boolean;
  maxLeverage?: number;
}

/**
 * Why the create button cannot be pressed, stated exactly.
 *
 * Order matters: report the first thing that is actually blocking, not a
 * generic message that happens to be true sometimes. Bot creation is listed
 * last because it is our own gap rather than something the operator can fix.
 */
function blockedReason(
  engineConnected: boolean,
  credentials: CredentialStatus | undefined,
  selectedCount: number,
  tradingEnabled: boolean,
): string {
  if (!engineConnected) {
    return "The engine is not reporting, so nothing can be created.";
  }
  if (credentials && !credentials.valid) {
    return credentials.message;
  }
  if (!tradingEnabled) {
    // Checked before submitting rather than after. The form used to accept a
    // selection, report it sent, and let the engine refuse it in silence.
    return (
      "Trading is disabled: the engine is in safe mode until it can explain " +
      "every position on the account. Resolve the alert on Overview first - " +
      "anything created now would be refused."
    );
  }
  if (selectedCount === 0) {
    return "Select at least one symbol.";
  }
  // Stated plainly, because it is the operator paying it. Taking the market
  // instead of resting roughly doubles the round-trip fee, which is why the
  // stop floor exists.
  return (
    "The engine rechecks the selected strategy and opens only confirmed " +
    "signals at market, in the signal direction, with exchange-held protection. " +
    "Amount is margin per symbol; market entries pay taker fees."
  );
}

/**
 * What the chosen leverage means for the symbols actually selected.
 *
 * The leverage dial goes to 100 because that was asked for, and a dial that
 * goes to 100 with no feedback is a trap: the tightest stop this platform
 * permits is 0.45%, and at 100x liquidation sits 0.60% out, so the two are
 * 0.15% apart. A single wick through both means liquidation instead of a stop,
 * which costs the maintenance margin on top of the loss.
 *
 * So the number is shown rather than the choice being taken away. The engine
 * still refuses anything whose liquidation would arrive first, per symbol.
 */
function LeverageNote({
  leverage,
  candidates,
}: {
  leverage: number;
  candidates: Candidate[];
}) {
  if (candidates.length === 0) return null;

  // The WIDEST stop binds, not the tightest. Leverage has to survive every
  // symbol selected, and a wider stop tolerates less of it before liquidation
  // overtakes it. Reading the minimum cleared 50x off the safest symbol in a
  // basket while the riskiest was refused outright by the engine.
  const binding = bindingStopFraction(candidates.map((c) => c.stop_distance_pct));
  const advised = recommendedLeverage(binding);
  const reserve = headroom(leverage, binding);
  const worst = candidates.reduce((a, b) =>
    b.stop_distance_pct > a.stop_distance_pct ? b : a,
  );

  if (leverage <= advised) {
    return (
      <p className="lev-note" data-band="ok">
        {leverage}x leaves liquidation {reserve.toFixed(1)} stop-widths away on{" "}
        {worst.symbol}, the widest stop selected (
        {worst.stop_distance_pct.toFixed(2)}%). Up to {advised}x is comfortable
        across these symbols.
      </p>
    );
  }

  return (
    <p className="lev-note" data-band={reserve < 1.25 ? "blocked" : "thin"}>
      <span aria-hidden="true">{"⚠"}</span>{" "}
      {reserve < MIN_HEADROOM
        ? `${leverage}x will be refused on ${worst.symbol}: its stop is ${worst.stop_distance_pct.toFixed(2)}% away but liquidation arrives first, so the stop could never fire. Use ${advised}x or less.`
        : `${leverage}x leaves only ${reserve.toFixed(2)} stop-widths before liquidation on ${worst.symbol} (${worst.stop_distance_pct.toFixed(2)}% stop), against ${advised}x for a comfortable ${SAFE_HEADROOM}. A wick through the stop reaches liquidation too.`}
    </p>
  );
}

/**
 * What the margin choice actually costs.
 *
 * Cross is routinely described as safer because liquidation sits further away,
 * and that is true of the price level while being the wrong way round about
 * the money: what backs the position is the whole wallet rather than the
 * margin posted for it. Both halves are stated so the choice is made with the
 * trade-off visible rather than the reassuring half of it.
 */
function MarginNote({ mode }: { mode: MarginMode }) {
  if (mode === "ISOLATED") {
    return (
      <p className="margin-note" data-mode="ISOLATED">
        Isolated: a liquidation costs only the margin posted for that position.
        Liquidation sits nearer, and the rest of the account is untouched.
      </p>
    );
  }
  return (
    <p className="margin-note" data-mode="CROSSED">
      Cross: your whole wallet balance backs every position. Liquidation sits
      further away, but what is at stake is the account rather than one
      position{"\u2019"}s margin.
    </p>
  );
}

function CreateScalpInner({
  engineConnected,
  credentials,
  tradingEnabled,
  maxLeverage = 10,
  initialPair,
}: Props) {
  const strategies = useStrategies();
  const [strategyId, setStrategyId] = useState(initialPair?.strategy ?? "");
  const activeId = strategyId || strategies.data?.default || "";
  const scan = useScan(activeId);
  const now = useNow();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(() => new Set(initialPair ? [initialPair.symbol] : []));
  const [amount, setAmount] = useState("100");
  const [leverage, setLeverage] = useState("5");
  const [name, setName] = useState("");
  const [marginMode, setMarginMode] = useState<MarginMode>("ISOLATED");
  const [continuous, setContinuous] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // What the last submission actually did. The engine accepts the request and
  // then decides, so the only honest confirmation names the symbols that were
  // sent and points at where the outcome will appear.
  const [sent, setSent] = useState<string[] | null>(null);

  const canTrade = Boolean(
    engineConnected && credentials?.valid && credentials.can_trade && tradingEnabled,
  );
  const candidates = (scan.data?.candidates ?? []).map((candidate) =>
    candidate.setup_ready && candidate.expires_at != null && now > candidate.expires_at * 1000
      ? { ...candidate, setup_ready: false, setup_note: "Signal expired; wait for a new close" }
      : candidate,
  );
  const scanFresh = Boolean(scan.data?.ready && !scan.data.error && !scan.isError &&
    scan.data.strategy?.id === activeId && scan.data.scanned_at &&
    now - scan.data.scanned_at * 1000 <= 60_000);
  const validSelection = selected.size > 0 && selected.size <= 5 &&
    [...selected].every((symbol) => candidates.some((c) => c.symbol === symbol && (continuous || c.setup_ready)));
  const validAmount = Number.isFinite(Number(amount)) && Number(amount) > 0 && Number(amount) <= 100_000;
  const validLeverage = Number(leverage) <= maxLeverage;
  const canSubmit = canTrade && scanFresh && validSelection && validAmount && validLeverage && !submitting;

  async function submit() {
    if (!canSubmit) return;
    setSubmitting(true);
    setSubmitError(null);
    setSent(null);
    const symbols = [...selected];
    try {
      await createBot({
        symbols: [...selected],
        amount,
        leverage: Number(leverage),
        strategy: activeId,
        name: name.trim(),
        margin_mode: marginMode,
        continuous,
      });
      setSelected(new Set());
      setName("");
      setSent(symbols);
      // The engine reports the result in its next snapshot, so refresh the
      // state rather than claiming success from the acknowledgement alone.
      await queryClient.invalidateQueries({ queryKey: ["state"] });
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : String(error));
    } finally {
      setSubmitting(false);
    }
  }

  const stats = scan.data?.stats ?? {};

  // Not memoised: filtering forty items is cheap, and the dependency would be
  // a fresh array literal whenever the scan has not loaded, so the memo would
  // not hold anyway.
  const readyCount = candidates.filter((c) => c.setup_ready).length;

  function toggle(symbol: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(symbol)) {
        next.delete(symbol);
      } else if (next.size < 5) {
        next.add(symbol);
      }
      return next;
    });
  }

  if (strategies.isError || scan.isError) {
    return (
      <section className="scan">
        <p className="offline" role="alert">
          Could not load the strategy or market scan. Refresh to retry.
        </p>
      </section>
    );
  }

  if (!scan.data) {
    return (
      <section className="scan">
        <p className="scan-summary">
          Scanning the market. This takes a few seconds: it reads every USDT
          perpetual, then pulls candles for the survivors.
        </p>
      </section>
    );
  }

  const excluded = Math.max(0, (stats.universe_size ?? 0) - candidates.length);
  const chosen = candidates.filter((c) => selected.has(c.symbol));
  const active =
    scan.data?.strategy ??
    strategies.data?.strategies.find((s) => s.id === activeId);

  return (
    <section className="scan">
      {initialPair && activeId === initialPair.strategy && <p role="status">
        Opened from favorites: {initialPair.symbol} / {active?.name ?? initialPair.strategy}.
        {selected.has(initialPair.symbol) && !candidates.some(candidate => candidate.symbol === initialPair.symbol)
          ? ' This coin is not eligible in the current scan. Review the exclusions below or rescan.'
          : ' Review your selection and settings before creating a bot.'}
      </p>}
      <div className="scan-head">
        <h2>Create Scalp</h2>
        <div className="scan-stats">
          <span>
            universe <b>{stats.universe_size ?? 0}</b>
          </span>
          <span>
            liquid <b>{stats.shortlist_size ?? 0}</b>
          </span>
          <span>
            tradeable <b>{candidates.length}</b>
          </span>
          <span>
            ready now <b>{readyCount}</b>
          </span>
          <span>
            took <b>{stats.duration_s ?? 0}s</b>
          </span>
        </div>
        <div className="field">
          <label htmlFor="strategy">Bot type</label>
          <select
            id="strategy"
            value={activeId}
            onChange={(e) => {
              setStrategyId(e.target.value);
              // A different strategy means a different candidate list, so a
              // selection carried across would be against the wrong coins.
              setSelected(new Set());
            }}
          >
            {(strategies.data?.strategies ?? []).filter(s => !s.backtest_only).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}{s.research_only ? " (Testnet experimental)" : ""}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          className="btn-ghost"
          onClick={() => scan.refetch()}
          disabled={scan.isFetching}
        >
          {scan.isFetching ? "Scanning..." : "Rescan"}
        </button>
      </div>

      {active && (
        <div className="strategy-note">
          <p className="strategy-summary">{active.summary}</p>
          {active.research_only && <p>Experimental strategy: testnet testing only.
            Backtest results do not guarantee profitable trades. Shared execution and risk checks apply.</p>}
          {active.assumptions.length > 0 && (
            <details>
              <summary>
                {active.assumptions.length} assumption
                {active.assumptions.length === 1 ? "" : "s"} in this adaptation
              </summary>
              <ul>
                {active.assumptions.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {readyCount === 0 && (
        <p className="scan-summary">
          No symbol currently meets this strategy's entry conditions.
          You can select waiting symbols and enable Continuous mode to watch for a future signal.
          No position opens until the engine confirms a fresh setup.
        </p>
      )}
      {!scanFresh && (
        <p className="offline" role="alert">
          {scan.data.error ?? "The scan is stale or unavailable. Wait for a fresh scan before submitting."}
        </p>
      )}

      <label>
        <input type="checkbox" checked={continuous} disabled={submitting}
          onChange={(event) => setContinuous(event.target.checked)} />
        Continuous mode: wait for a confirmed signal, then re-enter after settlement.
      </label>
      <p>Account limits are checked before every entry. Default cap: 10x leverage,
        1% planned risk per trade, 50% margin use, and a 3% daily equity-loss limit.</p>
      <table>
        <thead>
          <tr>
            <th scope="col">
              <span className="visually-hidden">Select</span>
            </th>
            <th scope="col">Symbol</th>
            <th scope="col">Regime</th>
            <th scope="col" className="col-num">
              Price
            </th>
            <th scope="col" className="col-num">
              ATR
            </th>
            <th scope="col" className="col-num">
              Spread
            </th>
            <th scope="col" className="col-num">
              Stop
            </th>
            <th scope="col" className="col-num">
              Fees/stop
            </th>
            <th scope="col" className="col-num">
              Need
            </th>
            <th scope="col">Setup</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((c: Candidate) => (
            <tr key={c.symbol} data-ready={c.setup_ready}>
              <td>
                <input
                  type="checkbox"
                  checked={selected.has(c.symbol)}
                  disabled={submitting || !scanFresh ||
                    (!selected.has(c.symbol) && selected.size >= 5)}
                  onChange={() => toggle(c.symbol)}
                  aria-label={"Select " + c.symbol}
                />
              </td>
              <td className="cell-symbol">
                {c.symbol}
                {c.setup_ready && (
                  <span className="ready-badge">
                    {" "}
                    {c.direction ?? "READY"}
                  </span>
                )}
              </td>
              <td>
                <span className="regime" data-regime={c.regime}>
                  {c.regime.replace("_", " ")}
                </span>
              </td>
              <td className="col-num num">{formatPrice(c.price, 4)}</td>
              <td className="col-num num">{c.atr_pct.toFixed(2)}%</td>
              <td className="col-num num">{c.spread_bps.toFixed(1)}bp</td>
              <td className="col-num num">{c.stop_distance_pct.toFixed(2)}%</td>
              <td
                className="col-num num cost"
                data-band={costBand(c.cost_to_stop)}
                title="Round-trip fees as a share of the stop distance. Above 25 percent is not tradeable."
              >
                {(c.cost_to_stop * 100).toFixed(0)}%
              </td>
              <td
                className="col-num num"
                title="Estimated break-even win rate for the displayed strategy levels and cost model"
              >
                {(c.breakeven_win_rate * 100).toFixed(0)}%
              </td>
              <td className="setup-note">{c.setup_note}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="scan-actions">
        <div className="field field-name">
          <label htmlFor="bot-name">Bot name</label>
          <input
            id="bot-name"
            type="text"
            maxLength={64}
            placeholder={active ? `${active.name} ${"…"}` : "Optional"}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="amount">Margin per symbol (USDT)</label>
          <input
            id="amount"
            type="number"
            min="0.01"
            max="100000"
            step="0.01"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="margin-mode">Margin</label>
          <select
            id="margin-mode"
            value={marginMode}
            onChange={(e) => setMarginMode(e.target.value as MarginMode)}
          >
            <option value="ISOLATED">Isolated</option>
            <option value="CROSSED" disabled>Cross (disabled by risk controls)</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="leverage">Leverage</label>
          <select
            id="leverage"
            value={leverage}
            onChange={(e) => setLeverage(e.target.value)}
          >
            {[1, 2, 3, 5, 8, 10, 15, 20, 25, 35, 50, 75, 100].map((x) => (
              <option key={x} value={x} disabled={x > maxLeverage}>
                {x}x
              </option>
            ))}
          </select>
          <small>Account leverage cap: {maxLeverage}x</small>
        </div>
        <button
          type="button"
          className="btn-primary"
          onClick={submit}
          disabled={!canSubmit}
        >
          {submitting
            ? "Opening..."
            : `Create bot with ${selected.size} symbol${selected.size === 1 ? "" : "s"}`}
        </button>
        {/* The reason is stated precisely rather than generically. An earlier
            version always blamed missing credentials, which sent an operator
            who had already added working keys off to re-enter them. */}
        <p className="scan-summary">
          {submitError ?? (!validAmount ? "Enter a positive margin up to 100,000 USDT." :
            canTrade && scanFresh && selected.size > 0 && !validSelection
              ? "Selected symbols are waiting. Enable Continuous mode to watch them, or wait for a confirmed signal."
              : continuous && canTrade && scanFresh && validSelection
                ? "Creates a watching bot. It opens only after fresh signal, data and account checks pass."
                :
            blockedReason(
              engineConnected,
              credentials,
              selected.size,
              tradingEnabled,
            ))}
        </p>
        {sent && !submitError && (
          // Deliberately not "created successfully". The web process only
          // relays the instruction; the engine holds the credentials and
          // decides, and it may be refused per symbol by the exchange. Saying
          // more than was actually established is how five failed bots came to
          // look like five working ones.
          <p className="scan-sent" role="status">
            <span aria-hidden="true">{"\u2713"}</span> Sent to the engine:{" "}
            <b>{sent.join(", ")}</b> at {leverage}x. It opens each one or
            records why it could not. Watch the <b>Bots</b> panel on Overview -
            any exchange rejection appears there in full.
          </p>
        )}
        <MarginNote mode={marginMode} />
        <LeverageNote leverage={Number(leverage)} candidates={chosen} />
      </div>

      <details className="rejections">
        <summary>Why {excluded} symbols were excluded</summary>
        <ul>
          {(scan.data.rejections ?? []).map((r) => (
            <li key={r.symbol + r.stage}>
              <code>{r.symbol}</code>
              <em>[{r.stage}]</em> {r.reason}
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}


// Memoised deliberately. App runs three independent one-second timers
// (health, state, and the clock for age displays) and a re-render of App
// re-invokes every child. Without this, a tick that changed nothing this
// component displays still re-runs its whole row map.
export const CreateScalp = memo(CreateScalpInner);
