import { StrategyMonitor } from "./components/StrategyMonitor";
import { useEffect, useState } from "react";
import { AlertPanel } from "./components/AlertPanel";
import { AutoTrade } from "./components/AutoTrade";
import { SingleCoin } from "./components/SingleCoin";
import { Research } from "./components/Research";
import { ResearchV2 } from "./components/ResearchV2";
import { FleetDeploy } from "./components/FleetDeploy";
import { Favorites } from "./components/Favorites";
import { AlphaLab } from "./components/AlphaLab";
import { QuantLab } from "./components/QuantLab";
import { BotFleet } from "./components/BotFleet";
import { CreateScalp } from "./components/CreateScalp";
import { History } from "./components/History";
import { KpiTiles } from "./components/KpiTiles";
import { ExternalPositions } from "./components/ExternalPositions";
import { PositionTable } from "./components/PositionTable";
import { StatusStrip } from "./components/StatusStrip";
import { useAppState, useHealth } from "./hooks/useEngine";
import { useNow } from "./hooks/useNow";
import "./App.css";

const STALE_AFTER_MS = 3000;

// Ordered as the work is done: watch what is open, close the loop on what
// finished, open something new. Labels live beside the ids so adding a tab is
// one entry rather than an id list plus a ternary chain that has to agree
// with it.
const TABS = [
  { id: "overview", label: "Overview" },
  { id: "strategies", label: "Strategy Monitor" },
  { id: "history", label: "History" },
  { id: "create", label: "Create Scalp" },
  { id: "fleet", label: "Fleet Deploy" },
  { id: "auto", label: "Auto Trade" },
  { id: "single", label: "Single Coin" },
  { id: "research", label: "Backtest & Research" },
  { id: "research2", label: "Research v2 (offline)" },
  { id: "alpha", label: "Alpha Lab" },
  { id: "quant", label: "Strategy Research" },
  { id: "favorites", label: "Favorites" },
] as const;

type TabId = (typeof TABS)[number]["id"];

/**
 * The overview.
 *
 * Deliberately no candlestick chart here. A dozen canvases repainting on every
 * tick costs tens of milliseconds of main thread per second, and at the size
 * that would fit in a row none of them is readable anyway. Charts belong in the
 * bot detail view, one at a time.
 */
export function App() {
  const health = useHealth();
  const state = useAppState();
  const now = useNow();
  const [tab, setTab] = useState<TabId>("overview");

  const mode = health.data?.mode;

  // The environment accent is applied at the document root so it reaches the
  // whole chrome, and the tab title carries it too: the mode has to be
  // unambiguous even when the dashboard is not the focused window.
  useEffect(() => {
    if (!mode) return;
    document.documentElement.dataset.env = mode;
    document.title = `${mode.toUpperCase()} \u00B7 The Scalper Crypt`;
  }, [mode]);

  const apiReachable = !health.isError;
  const ageMs = health.dataUpdatedAt ? now - health.dataUpdatedAt : Infinity;
  const stateAgeMs = state.dataUpdatedAt ? now - state.dataUpdatedAt : Infinity;
  const stale = !apiReachable || state.isError || ageMs > STALE_AFTER_MS ||
    stateAgeMs > STALE_AFTER_MS || health.data?.engine.state !== "live";

  return (
    <div className="app" data-stale={stale}>
      <StatusStrip
        halted={state.data?.halted}
        health={health.data}
        apiReachable={apiReachable}
        dataUpdatedAt={health.dataUpdatedAt}
        now={now}
      />

      {!apiReachable && (
        <p className="offline" role="alert">
          Cannot reach the API. It runs as a separate process from the trading
          engine, so this does not mean trading has stopped. Start it with{" "}
          <code>python -m scalper.api</code>.
        </p>
      )}

      <nav className="tabs" aria-label="Sections">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            aria-current={tab === id ? "page" : undefined}
            data-active={tab === id}
          >
            {label}
          </button>
        ))}
      </nav>

      {tab === "strategies" ? <main><StrategyMonitor state={state.data} connected={!stale} /></main>
        : tab === "alpha" ? <main><AlphaLab /></main>
        : tab === "quant" ? <main data-independent="true"><QuantLab /></main>
        : tab === "research" ? <main><Research state={state.data} connected={!stale && health.data?.engine.state === "live"} /></main>
        : tab === "research2" ? <main><ResearchV2 state={state.data} connected={!stale} /></main>
        : tab === "fleet" ? <main><FleetDeploy state={state.data} connected={!stale} /></main>
        : tab === "favorites" ? <main><Favorites state={state.data} connected={!stale} /></main>
        : tab === "single" ? <main><SingleCoin state={state.data} connected={!stale} /></main>
        : tab === "auto" ? <main><AutoTrade state={state.data} connected={!stale} /></main> : tab === "create" ? (
        <main>
          <CreateScalp
            engineConnected={!stale && health.data?.engine.state === "live"}
            credentials={state.data?.credentials}
            tradingEnabled={!stale && (state.data?.trading_enabled ?? false)}
            maxLeverage={state.data?.max_leverage}
          />
        </main>
      ) : tab === "history" ? (
        <main>
          <History history={state.data?.history} />
        </main>
      ) : state.data ? (
        <>
          <KpiTiles totals={state.data.totals} />
          <main>
            <AlertPanel
              alerts={state.data.alerts}
              lastAction={state.data.last_action}
            />
            {/* Above the positions, because a bot that failed to open one has
                no row below and used to leave the screen looking empty. */}
            {/* Above the fleet on purpose: a few hundred bot cards put
                anything below them off the bottom of the page. Renders
                nothing when the account has no outside positions. */}
            <ExternalPositions positions={state.data.external_positions ?? []} />
            <BotFleet bots={state.data.bots} />
            <PositionTable
              positions={state.data.positions}
              engineConnected={!stale}
            />
          </main>
        </>
      ) : (
        <p className="skeleton">Loading engine state...</p>
      )}
    </div>
  );
}
