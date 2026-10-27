import type { StrategyReport } from "../components/StrategyMonitor";
/**
 * Typed access to the engine API.
 *
 * These shapes mirror the FastAPI response models. Phase 4 of the plan
 * generates this file from the OpenAPI schema so a backend field rename becomes
 * a compile error rather than a blank cell in a table about money.
 */

export type Environment = "testnet" | "paper" | "live";

/**
 * Four states, not a boolean.
 *
 * "unknown" means the engine has not been reached yet, which is different from
 * it being down. "stale" means the link is open but nothing has arrived
 * recently, which without an age check looks exactly like a quiet market.
 */
export type LinkState = "unknown" | "live" | "stale" | "down";

export interface EngineLink {
  connected: boolean;
  state: LinkState;
  seq: number;
  age_seconds: number | null;
  last_message_at: number | null;
}

export interface Health {
  status: string;
  version: string;
  mode: Environment;
  is_real_money: boolean;
  /** The real base URL, not a label. If these disagree, the label is lying. */
  exchange_rest_url: string;
  engine: EngineLink;
}

export interface Totals {
  unrealised_pnl: string;
  realised_today: string;
  /**
   * Account capital, in the three senses that differ.
   *
   * Wallet is deposited money. Equity is wallet plus open profit, and is what
   * the exchange liquidates against. Available is what is left after margin is
   * posted, and is the only one that answers whether another trade can open.
   *
   * Decimal strings, not numbers. These are reconciled against Binance by eye,
   * and a float would round the last place on the way to the screen.
   */
  wallet: string;
  available: string;
  equity: string;
  margin_used: string;
  balance_known?: boolean;
  open_positions: number;
  bots_running: number;
  bots_stopped: number;
  bots_error: number;
  tp_hits_today: number;
  sl_hits_today: number;
}

export interface Position {
  timeframe?: string | null;
  id: string;
  bot: string;
  symbol: string;
  side: "LONG" | "SHORT";
  leverage: number;
  entry: number;
  mark: number;
  stop: number;
  target: number;
  liquidation?: number | null;
  quantity: number;
  notional: number;
  unrealised_pnl: number;
  roe_pct: number;
  margin_ratio: number;
  opened_at: number;
  price_decimals: number;
  /** False means the exchange holds no protective order for this position. */
  stop_armed: boolean;
  target_armed?: boolean;
}

export interface Bot {
  symbol_notes?: Record<string, string>;
  last_checked_at?: number;
  id: string;
  name: string;
  strategy: string;
  state: "running" | "stopped" | "error";
  open_positions: number;
  pnl_today: number;
  pending_closures?: number;
  entry_pending?: boolean;
  continuous?: boolean;
  tp_hits: number;
  sl_hits: number;
  symbols: string[];
  margin_mode: MarginMode;
  /**
   * Why this bot is not trading, verbatim from the exchange where it came
   * from there.
   *
   * The most important field on this type. A bot that failed to open anything
   * has no positions, and a dashboard that renders only positions therefore
   * showed nothing at all: five bots created, six failures recorded, and an
   * empty screen.
   */
  error: string | null;
}

export interface Alert {
  id: string;
  severity: "info" | "warning" | "critical";
  message: string;
  /**
   * Symbols the alert is about, when it is about specific ones.
   *
   * Carried separately from the message so the interface can offer an action
   * on them. Parsing symbols back out of an English sentence would break the
   * first time the sentence was reworded.
   */
  symbols?: string[];
}

/**
 * Whether the exchange credentials are present, and whether they work.
 *
 * Deliberately two booleans. "Configured but rejected" is a different problem
 * from "absent", and telling somebody to add keys they have already added
 * sends them to re-enter a value that was never wrong.
 *
 * Carries a masked preview only. The engine is the sole holder of key material
 * and never puts it on the wire.
 */
export interface CredentialStatus {
  configured: boolean;
  valid: boolean;
  message: string;
  environment: string;
  key_preview: string;
  balance: string | null;
  can_trade: boolean | null;
}

/**
 * One finished trade.
 *
 * Entry and exit are both kept so the result can be re-derived rather than
 * taken on trust, and `fees` is separate from `realised` because at this
 * holding period the fee is a large share of the outcome rather than a
 * rounding detail.
 */
export interface ClosedTrade {
  verified?: boolean;
  symbol: string;
  bot: string;
  strategy: string;
  side: "LONG" | "SHORT";
  quantity: number;
  entry: number;
  exit_price: number;
  leverage: number;
  realised: number;
  fees: number;
  /** "tp", "sl", or null when the exit price matched neither level. */
  outcome: string | null;
  opened_at: number;
  closed_at: number;
  duration_seconds: number;
  is_win: boolean;
  roe_pct: number;
}

/**
 * Null is a real answer here.
 *
 * With no trades there is no win rate, and with no losing trade there is no
 * profit factor. Rendering those as 0 or as infinity would both be claims the
 * data does not support.
 */
export interface HistorySummary {
  trades: number;
  win_rate: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  net: number;
  fees: number;
  tp_hits: number;
  sl_hits: number;
  wins: number;
  losses: number;
}

export interface History {
  trades: ClosedTrade[];
  summary: HistorySummary;
  /** False while history lives in engine memory only. */
  persistent: boolean;
}

/**
 * What the last operator command did.
 *
 * Null until one has run. A refusal is a result: the Adopt button appeared
 * dead for an unprotected position because the engine refused it correctly
 * and said so only to its log.
 */
export interface LastAction {
  kind: string;
  ok: boolean;
  detail: string;
  /** Unix seconds. Lets the UI retire a stale result and redraw a repeat. */
  at: number;
}

/**
 * A position on the account that this engine did not open.
 *
 * A second bot - a CLI one - or a hand-placed trade. Reported separately from
 * `positions` so it can be SEEN without ever being counted as ours.
 */
export interface ExternalPosition {
  symbol: string;
  side: "LONG" | "SHORT";
  quantity: number;
  entry_price: number;
  /** Null when the venue did not report one. Not the same as zero. */
  mark_price: number | null;
  unrealised: number | null;
  liquidation_price: number | null;
}

export interface AppState {
  strategy_monitor?: StrategyReport | null;
  seq: number;
  mode: Environment;
  bots: Bot[];
  positions: Position[];
  external_positions?: ExternalPosition[];
  alerts: Alert[];
  credentials: CredentialStatus;
  totals: Totals;
  history: History;
  last_action: LastAction | null;
  /**
   * Whether the engine will accept a trade at all.
   *
   * False while it is in safe mode - typically because the exchange holds a
   * position it cannot explain. The form needs this BEFORE it submits: it
   * previously accepted five symbols, said "sent to the engine", and the
   * engine refused the lot without the screen changing.
   */
  trading_enabled: boolean;
  max_leverage?: number;
  max_positions?: number;
  auto_trade?: { enabled: boolean; amount?: string; leverage?: number; bot_ids?: string[];
    coverage?: { universe: number; eligible: number; checked_at: number; completed: boolean } };
  single_coin?: { strategy?: string; selection?: { name: string; wins?: number; trades: number; win_rate: number; net: number }; enabled: boolean; amount?: string; leverage?: number; timeframe?: "1m" | "5m" | "10m"; bot_id?: string };
  halted?: boolean;
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new ApiError(`${path} responded ${response.status}`, response.status);
  }
  return (await response.json()) as T;
}

export const fetchHealth = () => get<Health>("/api/health");
export const fetchState = () => get<AppState>("/api/state");

export type Regime = "trend" | "range" | "no_trade";

export interface Candidate {
  symbol: string;
  price: number;
  quote_volume_24h: number;
  change_24h_pct: number;
  spread_bps: number;
  atr_pct: number;
  atr_percentile: number;
  adx: number;
  plus_di: number;
  minus_di: number;
  efficiency_ratio: number;
  rvol_5m: number;
  rvol_1m: number;
  regime: Regime;
  vwap: number;
  sigma: number;
  sigma_distance: number;
  rsi_1m: number;
  delta_pct: number;
  stop_distance_pct: number;
  /** Round-trip fees as a fraction of the stop. Above 0.25 is not tradeable. */
  cost_to_stop: number;
  breakeven_win_rate: number;
  /** The strategy entry conditions hold on the last closed bar. */
  setup_ready: boolean;
  expires_at?: number | null;
  setup_note: string;
  direction: string | null;
  score: number;
}

export interface ScanRejection {
  symbol: string;
  stage: string;
  reason: string;
}

export interface ScanStats {
  universe_size: number;
  shortlist_size: number;
  candidate_count: number;
  ready_count: number;
  duration_s: number;
  rejection_counts: Record<string, number>;
}

export interface ScanResponse {
  ready: boolean;
  error: string | null;
  strategy?: StrategyInfo;
  age_seconds?: number;
  scanned_at?: number;
  stats: Partial<ScanStats>;
  candidates: Candidate[];
  rejections: ScanRejection[];
}

/**
 * A bot type.
 *
 * `assumptions` is not decoration. Where a strategy was adapted from a source
 * that specified something different, or did not specify it at all, that is
 * recorded here and shown, so nobody later mistakes our choice for the
 * original.
 */
export interface StrategyInfo {
  backtest_only?: boolean;
  research_only?: boolean;
  testnet_only?: boolean;
  manual_only?: boolean;
  id: string;
  name: string;
  summary: string;
  assumptions: string[];
}

export interface StrategyList {
  default: string;
  strategies: StrategyInfo[];
}

export const fetchStrategies = () => get<StrategyList>("/api/strategies");

export const fetchScan = (strategyId: string, force = false) =>
  get<ScanResponse>(
    `/api/scan?strategy=${encodeURIComponent(strategyId)}${force ? "&force=true" : ""}`,
  );

/**
 * How the position is margined.
 *
 * ISOLATED caps a loss at the margin posted for that one position. CROSSED
 * backs it with the whole wallet, which pushes liquidation further away at the
 * cost of putting the account behind the trade rather than one position's
 * margin.
 *
 * Binance spells it CROSSED. The interface says "Cross" because that is what
 * everyone calls it, and the backend accepts both.
 */
export type MarginMode = "ISOLATED" | "CROSSED";

export interface CreateBotRequest {
  continuous?: boolean;
  symbols: string[];
  amount: string;
  leverage: number;
  strategy: string;
  margin_mode?: MarginMode;
  /** Blank falls back to a name derived from the strategy. */
  name?: string;
}

export interface AdoptRequest {
  symbols: string[];
  name?: string;
  /**
   * Attach a stop to any named position that has none, then adopt it.
   *
   * Off by default, and deliberately a separate request rather than a
   * fallback: it places a real order on the exchange. A plain adopt still
   * refuses an unprotected position, because recording a stop that does not
   * exist is the one thing this dashboard must never do.
   */
  protect?: boolean;
}

/**
 * Forget a bot that will never trade again.
 *
 * A record operation: no orders are placed or cancelled. The engine refuses
 * while the bot still holds a position, because deleting the record would turn
 * a managed position into an orphan and disable trading on the next restart.
 */
export async function removeBot(botId: string): Promise<void> {
  const response = await fetch(`/api/bots/${encodeURIComponent(botId)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new ApiError(detail || `remove failed (${response.status})`, response.status);
  }
}

/**
 * Claim positions the exchange holds that the engine cannot explain.
 *
 * The engine reads the protective orders back from the exchange and refuses
 * any position it cannot manage honestly, so this returning 202 does not mean
 * every symbol was adopted. The next snapshot says what actually happened.
 */
export async function adoptPositions(request: AdoptRequest): Promise<void> {
  const response = await fetch("/api/bots/adopt", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new ApiError(detail || `adopt failed (${response.status})`, response.status);
  }
}

/**
 * Ask the engine to open a bot.
 *
 * Returns 202, not 201. The web process relays the instruction; the engine
 * decides and acts. Reporting a created resource here would claim an order was
 * placed by a process that holds no credentials and cannot place one.
 */
export async function createBot(request: CreateBotRequest): Promise<void> {
  const response = await fetch("/api/bots", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new ApiError(detail || `create bot failed (${response.status})`, response.status);
  }
}

export async function configureAutoTrade(amount: string, leverage: number, stop = false): Promise<void> {
  const response = await fetch(`/api/auto-trade/${stop ? "stop" : "start"}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: stop ? undefined : JSON.stringify({ amount, leverage }),
  });
  if (!response.ok) throw new ApiError(await response.text(), response.status);
}

export async function configureSingleCoin(amount: string, leverage: number, timeframe: "1m" | "5m" | "10m", stop = false): Promise<void> {
  const response = await fetch(`/api/single-coin/${stop ? "stop" : "start"}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: stop ? undefined : JSON.stringify({ amount, leverage, timeframe }),
  });
  if (!response.ok) throw new ApiError(await response.text(), response.status);
}


export async function engineControl(action: "panic" | "resume"): Promise<void> {
  const response = await fetch(`/api/control/${action}`, { method: "POST" });
  if (!response.ok) throw new ApiError(await response.text(), response.status);
}


export async function stopBot(botId: string): Promise<void> {
  const response = await fetch(`/api/bots/${encodeURIComponent(botId)}/stop`, { method: "POST" });
  if (!response.ok) throw new ApiError(await response.text(), response.status);
}

export async function dismissBots(botIds: string[]): Promise<void> {
  const response = await fetch("/api/bots/dismiss", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bot_ids: botIds }) });
  if (!response.ok) throw new ApiError(await response.text() || "Dismiss all failed", response.status);
}
