/**
 * Geometry for the distance-to-TP/SL bar.
 *
 * The bar answers one question at a glance: how close is this position to being
 * closed, and in which direction. Everything here exists to keep that answer
 * truthful.
 *
 * The span is the real price distance from stop to target, so entry sits at its
 * true proportional offset. Normalising entry to the centre would make every
 * position look like 1:1 regardless of its actual reward-to-risk, which is a
 * lie told by a lot of trading UIs.
 *
 * The marker follows mark price rather than last price. Binance computes
 * liquidation and unrealised PnL from mark, so plotting last would put our bar
 * a few ticks out of agreement with the venue that is actually going to close
 * the position.
 */

export type Side = "LONG" | "SHORT";

export interface BarInput {
  side: Side;
  entry: number;
  stop: number;
  target: number;
  mark: number;
  liquidation?: number;
}

export interface BarGeometry {
  /** Percent along the bar, 0 at the stop and 100 at the target. */
  stopPos: number;
  entryPos: number;
  targetPos: number;
  /** Clamped to the bar for rendering. */
  markPos: number;
  /** Unclamped, so callers can tell that price has run past a level. */
  markPosRaw: number;
  liquidationPos: number | null;
  /**
   * True when liquidation would be reached before the stop.
   *
   * The stop is then decorative: the exchange closes the position first, at a
   * worse price and with the maintenance margin forfeited. This is the single
   * most valuable warning the dashboard can render.
   */
  liquidationInsideStop: boolean;
  riskReward: number;
  /** Signed price move required to reach each level, as a percentage. */
  toTargetPct: number;
  toStopPct: number;
  /**
   * Which level price would reach first, and how far away it is.
   *
   * Measured in price, not in position along the bar. The bar is drawn to the
   * true stop-to-target span, so a 2R trade puts entry a third of the way
   * along: a marker sitting left of centre can still be closer to the target
   * in the only units that matter, which are the ones the operator acts on.
   *
   * The distance is unsigned because the label already carries the direction.
   */
  nearer: "stop" | "target";
  nearerPct: number;
  /**
   * How far the trade has travelled: 0 at entry, 100 at the target, -100 at
   * the stop.
   *
   * Each side is measured against its own distance, so this is R-multiples in
   * percent rather than one span. Dividing a loss by the reward distance would
   * report a stopped-out 1:3 trade as -33, which reads as a third of a loss
   * when it is a whole one.
   *
   * Unclamped on purpose. Past 100 means price has run through the target
   * without the exchange filling the take-profit, which is worth seeing.
   */
  progressPct: number;
}

const clamp = (value: number, low: number, high: number) =>
  Math.min(high, Math.max(low, value));

export function barGeometry(input: BarInput): BarGeometry {
  const { side, entry, stop, target, mark, liquidation } = input;

  const span = target - stop;
  if (span === 0) {
    throw new Error("bar span is zero: stop and target cannot be equal");
  }

  // One formula covers both directions. For a short the span is negative and
  // so are the offsets, so the ratio still runs 0 at the stop to 100 at the
  // target. Loss therefore stays on the left in both cases, which is what
  // makes a mixed table scannable.
  const positionOf = (price: number) => ((price - stop) / span) * 100;

  const markPosRaw = positionOf(mark);

  let liquidationInsideStop = false;
  if (liquidation !== undefined) {
    // Whichever level price reaches first is the one that closes the position.
    // A long falls toward both, so the higher one binds; a short rises, so the
    // lower one binds.
    liquidationInsideStop =
      side === "LONG" ? liquidation > stop : liquidation < stop;
  }

  const toTargetPct = ((target - mark) / mark) * 100;
  const toStopPct = ((stop - mark) / mark) * 100;
  const targetIsNearer = Math.abs(toTargetPct) < Math.abs(toStopPct);

  return {
    stopPos: 0,
    entryPos: positionOf(entry),
    targetPos: 100,
    markPos: clamp(markPosRaw, 0, 100),
    markPosRaw,
    liquidationPos:
      liquidation === undefined ? null : clamp(positionOf(liquidation), 0, 100),
    liquidationInsideStop,
    riskReward: Math.abs(target - entry) / Math.abs(entry - stop),
    toTargetPct,
    toStopPct,
    nearer: targetIsNearer ? "target" : "stop",
    nearerPct: Math.abs(targetIsNearer ? toTargetPct : toStopPct),
    progressPct:
      (side === "LONG" ? mark >= entry : mark <= entry)
        ? ((mark - entry) / (target - entry)) * 100
        : ((mark - entry) / (entry - stop)) * 100,
  };
}
