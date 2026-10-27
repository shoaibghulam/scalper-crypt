/**
 * Leverage against the stop, not against a fixed ceiling.
 *
 * A stop-loss and a liquidation are two different mechanisms at two different
 * prices. The stop is an order you placed; liquidation is the exchange closing
 * you when your margin runs out. Whichever price is reached first wins, and
 * past a certain leverage that is always the liquidation.
 *
 * Concretely, at 50x your margin is 2% of the position, so a 2.52% adverse
 * move would lose 126% of what you posted. You cannot lose more than you have,
 * so the exchange closes you at about 1.60% instead. The stop at 2.52% is a
 * price the position never survives to reach - it is decorative, and the
 * downside is no longer bounded by it.
 *
 * Extracted from the component because the rule was wrong in a way no
 * component test would have caught: it took the TIGHTEST stop across the
 * selected symbols as the binding one. A wider stop needs LOWER leverage, so
 * the widest is what binds. The advisory was reading the safest symbol and
 * clearing leverage that the riskiest could not survive.
 */

/**
 * Binance maintenance margin on the smallest USD-M tier, which is the tier
 * every position this platform opens falls into.
 */
export const MAINTENANCE_MARGIN_RATE = 0.004;

/** Liquidation this many stop-widths away is comfortable. */
export const SAFE_HEADROOM = 4;

/**
 * Below this the stop cannot be relied on to fire first, and the engine
 * refuses the trade. Mirrors MIN_LIQUIDATION_STOP_MULTIPLE in the backend,
 * which is the copy that actually enforces it.
 */
export const MIN_HEADROOM = 1.25;

/** How far from entry the exchange would liquidate, as a fraction of price. */
export function liquidationDistance(leverage: number): number {
  return 1 / leverage - MAINTENANCE_MARGIN_RATE;
}

/**
 * How many stop-widths of reserve sit between the stop and liquidation.
 *
 * One means they coincide and the stop protects nothing. At or below zero the
 * liquidation comes first, reported as zero rather than as a negative that
 * would compare as small-but-safe against a threshold.
 */
export function headroom(leverage: number, stopFraction: number): number {
  const distance = liquidationDistance(leverage);
  if (distance <= 0 || stopFraction <= 0) return 0;
  return distance / stopFraction;
}

/** The leverage that keeps the comfortable reserve for this stop distance. */
export function recommendedLeverage(stopFraction: number): number {
  if (stopFraction <= 0) return 1;
  return Math.max(
    1,
    Math.floor(1 / (SAFE_HEADROOM * stopFraction + MAINTENANCE_MARGIN_RATE)),
  );
}

/** The highest leverage the engine will still accept for this stop distance. */
export function maxAllowedLeverage(stopFraction: number): number {
  if (stopFraction <= 0) return 1;
  return Math.max(
    1,
    Math.floor(1 / (MIN_HEADROOM * stopFraction + MAINTENANCE_MARGIN_RATE)),
  );
}

/**
 * The stop that constrains a basket of symbols: the WIDEST one.
 *
 * This is the correction. Leverage has to survive every symbol selected, and a
 * wider stop tolerates less leverage before liquidation overtakes it. Taking
 * the minimum cleared 50x off the safest symbol in the basket while the
 * riskiest was refused by the engine, which is how a bot came to open four
 * positions and fail the fifth.
 */
export function bindingStopFraction(stopPercents: number[]): number {
  if (stopPercents.length === 0) return 0;
  return Math.max(...stopPercents) / 100;
}
