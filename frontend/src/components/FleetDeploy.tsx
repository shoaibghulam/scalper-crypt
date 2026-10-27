import { useEffect, useMemo, useState } from "react";
import type { AppState } from "../lib/api";
import { createBot } from "../lib/api";
import "./Research.css";
import "./FleetDeploy.css";

/**
 * One strategy across the whole coin list, in a single action.
 *
 * The arithmetic is the dangerous part. The engine caps a bot at five symbols
 * and every bot posts its OWN margin, so the amount entered here is per coin,
 * not a total. Typing 100 and selecting 500 coins commits 50,000. This tab
 * therefore spends more space on the total than on the form that produces it,
 * and will not arm until the operator has acknowledged that number.
 *
 * One bot per coin, deliberately: a bot holding five symbols reports one
 * blended P&L, and the first question after a deploy like this is always which
 * coin lost the money.
 */

type Strategy = {
  id: string;
  name: string;
  timeframe?: string;
  backtest_only?: boolean;
  research_only?: boolean;
  testnet_only?: boolean;
  manual_only?: boolean;
};

type Scope = "all" | "stored";

const money = (value: number) => value.toLocaleString(undefined, { maximumFractionDigits: 0 });

function strategyList(body: unknown): Strategy[] {
  const rows = (body as { strategies?: unknown })?.strategies;
  if (!Array.isArray(rows)) return [];
  return rows.filter(
    (row): row is Strategy =>
      typeof row === "object" && row !== null && typeof (row as Strategy).id === "string",
  );
}

function coinList(body: unknown): string[] {
  const rows = Array.isArray(body) ? body : (body as { symbols?: unknown })?.symbols;
  if (!Array.isArray(rows)) return [];
  return rows.filter((row): row is string => typeof row === "string");
}

export function FleetDeploy({
  state,
  connected = false,
}: {
  state?: AppState;
  connected?: boolean;
}) {
  const [catalog, setCatalog] = useState<Strategy[]>([]);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [stored, setStored] = useState<string[]>([]);
  const [strategy, setStrategy] = useState("");
  const [amount, setAmount] = useState("");
  const [leverage, setLeverage] = useState("3");
  const [scope, setScope] = useState<Scope>("all");
  const [timeframe, setTimeframe] = useState("all");
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ ok: string[]; failed: { symbol: string; why: string }[] }>();

  useEffect(() => {
    void fetch("/api/strategies")
      .then(async r => setCatalog(r.ok ? strategyList(await r.json()) : []))
      .catch(() => setCatalog([]));
    void fetch("/api/research/symbols")
      .then(async r => setSymbols(r.ok ? coinList(await r.json()) : []))
      .catch(() => setSymbols([]));
    void fetch("/api/research/v2/inventory")
      .then(async r => {
        const body = r.ok ? await r.json() : null;
        const rows = (body as { symbols?: { symbol: string }[] } | null)?.symbols;
        setStored(Array.isArray(rows) ? rows.map(row => row.symbol) : []);
      })
      .catch(() => setStored([]));
  }, []);

  /** Only strategies the engine will actually run live. */
  const deployable = useMemo(
    () =>
      catalog.filter(
        s =>
          !s.backtest_only &&
          !s.research_only &&
          !s.manual_only &&
          (timeframe === "all" || s.timeframe === timeframe) &&
          !state?.strategy_monitor?.rows.find(row => row.id === s.id)?.disabled,
      ),
    [catalog, timeframe, state],
  );
  const timeframes = useMemo(
    () => [...new Set(catalog.map(s => s.timeframe).filter(Boolean))].sort() as string[],
    [catalog],
  );

  useEffect(() => {
    if (!deployable.some(s => s.id === strategy)) setStrategy(deployable[0]?.id ?? "");
  }, [deployable, strategy]);

  const chosen = deployable.find(s => s.id === strategy);
  const coins = scope === "stored" ? stored : symbols;
  const perCoin = Number(amount);
  const lev = Number(leverage);
  const valid =
    Number.isFinite(perCoin) &&
    perCoin > 0 &&
    Number.isInteger(lev) &&
    lev >= 1 &&
    lev <= (state?.max_leverage ?? 10);
  const margin = valid ? perCoin * coins.length : 0;
  const notional = margin * (valid ? lev : 0);

  const accountReady =
    connected &&
    !!state?.trading_enabled &&
    !!state.credentials?.valid &&
    !!state.credentials.can_trade;
  const canDeploy = accountReady && valid && acknowledged && !!chosen && coins.length > 0 && !busy;

  async function deploy() {
    if (!chosen) return;
    setBusy(true);
    setDone(undefined);
    const ok: string[] = [];
    const failed: { symbol: string; why: string }[] = [];
    // Sequential: one rejected symbol must not take the rest down with it.
    for (const symbol of coins) {
      try {
        await createBot({
          symbols: [symbol],
          strategy: chosen.id,
          amount,
          leverage: lev,
          continuous: true,
          margin_mode: "ISOLATED",
        });
        ok.push(symbol);
      } catch (cause) {
        failed.push({ symbol, why: cause instanceof Error ? cause.message : String(cause) });
      }
    }
    setDone({ ok, failed });
    setBusy(false);
    setAcknowledged(false);
  }

  return (
    <section
      className="research-insights fleet-deploy"
      aria-label="Deploy one strategy to every coin"
    >
      <h2>Fleet Deploy</h2>
      <p>
        Run one strategy on every coin at once. The amount is charged <strong>per coin</strong>,
        because each coin becomes its own bot with its own margin.
      </p>

      <div className="fleet-form">
        <label>
          Strategy
          <select value={strategy} onChange={e => setStrategy(e.target.value)}>
            {deployable.map(s => (
              <option key={s.id} value={s.id}>
                {s.name}
                {s.timeframe ? " (" + s.timeframe + ")" : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          Timeframe
          <select value={timeframe} onChange={e => setTimeframe(e.target.value)}>
            <option value="all">Any</option>
            {timeframes.map(t => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label>
          Amount per coin
          <input
            type="number"
            min="1"
            step="1"
            value={amount}
            placeholder="100"
            onChange={e => setAmount(e.target.value)}
          />
        </label>
        <label>
          Leverage
          <input
            type="number"
            min="1"
            max={state?.max_leverage ?? 10}
            step="1"
            value={leverage}
            onChange={e => setLeverage(e.target.value)}
          />
        </label>
        <label>
          Coins
          <select value={scope} onChange={e => setScope(e.target.value as Scope)}>
            <option value="all">Every listed coin ({symbols.length})</option>
            <option value="stored">Only coins stored offline ({stored.length})</option>
          </select>
        </label>
      </div>

      <dl className="fleet-total">
        <div>
          <dt>Coins</dt>
          <dd>{coins.length} coins</dd>
        </div>
        <div>
          <dt>Bots created</dt>
          <dd>{coins.length}</dd>
        </div>
        <div className="fleet-total-headline">
          <dt>Margin committed</dt>
          <dd>{money(margin)} USDT</dd>
        </div>
        <div>
          <dt>Position size controlled</dt>
          <dd>{money(notional)} USDT</dd>
        </div>
        <div>
          <dt>Runs on</dt>
          <dd>{chosen?.timeframe ?? "—"} candles</dd>
        </div>
      </dl>

      <label className="fleet-ack">
        <input
          type="checkbox"
          checked={acknowledged}
          onChange={e => setAcknowledged(e.target.checked)}
        />
        I understand this commits {money(margin)} USDT of margin across {coins.length} separate
        bots, not {money(perCoin || 0)} in total.
      </label>

      <button type="button" disabled={!canDeploy} onClick={() => void deploy()}>
        {busy ? "Deploying " + coins.length + " bots..." : "Deploy to " + coins.length + " coins"}
      </button>

      {!accountReady && (
        <p role="status">
          Resolve the account alerts in Overview before deploying. Nothing can be armed from here
          while the engine or the credentials are not ready.
        </p>
      )}
      {!valid && amount !== "" && (
        <p role="alert">Enter a positive amount and a leverage the account allows.</p>
      )}

      {done && (
        <div className="fleet-result">
          <p role="status">
            Started {done.ok.length} of {done.ok.length + done.failed.length} bots.
          </p>
          {done.failed.length > 0 && (
            <ul role="alert">
              {done.failed.map(f => (
                <li key={f.symbol}>
                  {f.symbol}: {f.why}
                </li>
              ))}
            </ul>
          )}
          <p className="research-helper">
            Accepted by the engine, not confirmed filled. Check Strategy Monitor and Overview for
            what actually opened.
          </p>
        </div>
      )}
    </section>
  );
}
