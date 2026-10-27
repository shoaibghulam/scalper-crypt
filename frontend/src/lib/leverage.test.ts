import { describe, expect, it } from "vitest";
import {
  bindingStopFraction,
  headroom,
  liquidationDistance,
  maxAllowedLeverage,
  recommendedLeverage,
} from "./leverage";

/**
 * The bug these were extracted to fix.
 *
 * A bot was created at 50x across five symbols. Four opened; LSKUSDT was
 * refused by the engine because at 50x liquidation arrives at 1.60% while its
 * stop sat at 2.52%, so the stop could never fire. The warning in the form had
 * said 50x was fine - it was measuring the TIGHTEST stop in the basket, and
 * the widest is the one that binds.
 */

describe("liquidation versus the stop", () => {
  it("puts liquidation nearer than a 2.52% stop at 50x", () => {
    // The real case. At 50x the margin is 2% of the position, so a 2.52% move
    // would lose 126% of what was posted - impossible, so the exchange closes
    // first and the stop is decorative.
    expect(liquidationDistance(50)).toBeCloseTo(0.016, 4);
    expect(liquidationDistance(50)).toBeLessThan(0.0252);
  });

  it("leaves the stop able to fire at 10x", () => {
    expect(liquidationDistance(10)).toBeCloseTo(0.096, 4);
    expect(liquidationDistance(10)).toBeGreaterThan(0.0252);
  });

  it("reports headroom below one when liquidation comes first", () => {
    expect(headroom(50, 0.0252)).toBeLessThan(1);
  });

  it("never reports a negative headroom", () => {
    // A negative would compare as small-but-safe against a threshold.
    expect(headroom(500, 0.0252)).toBe(0);
  });
});

describe("bindingStopFraction", () => {
  it("takes the widest stop, not the tightest", () => {
    // The correction. Leverage must survive every symbol in the basket, and a
    // wider stop tolerates less leverage before liquidation overtakes it.
    expect(bindingStopFraction([0.45, 1.2, 2.52])).toBeCloseTo(0.0252, 6);
  });

  it("does not pick the safest symbol in the basket", () => {
    // The exact shape of the bug: returning 0.45% here cleared 50x while the
    // 2.52% symbol was refused by the engine.
    expect(bindingStopFraction([0.45, 2.52])).not.toBeCloseTo(0.0045, 6);
  });

  it("handles a single symbol", () => {
    expect(bindingStopFraction([0.6])).toBeCloseTo(0.006, 6);
  });

  it("returns zero for an empty basket", () => {
    expect(bindingStopFraction([])).toBe(0);
  });
});

describe("leverage advice", () => {
  it("advises 9x for the stop that was refused at 50x", () => {
    expect(recommendedLeverage(0.0252)).toBe(9);
  });

  it("allows up to 28x before the engine refuses it", () => {
    expect(maxAllowedLeverage(0.0252)).toBe(28);
  });

  it("never advises more than the engine would allow", () => {
    for (const stop of [0.0045, 0.006, 0.01, 0.0252, 0.05]) {
      expect(recommendedLeverage(stop)).toBeLessThanOrEqual(maxAllowedLeverage(stop));
    }
  });

  it("advises less leverage as the stop widens", () => {
    expect(recommendedLeverage(0.05)).toBeLessThan(recommendedLeverage(0.0045));
  });

  it("keeps the comfortable reserve at whatever it advises", () => {
    for (const stop of [0.0045, 0.01, 0.0252, 0.05]) {
      expect(headroom(recommendedLeverage(stop), stop)).toBeGreaterThanOrEqual(4);
    }
  });

  it("leaves the stop able to fire at the allowed maximum", () => {
    for (const stop of [0.0045, 0.01, 0.0252, 0.05]) {
      expect(headroom(maxAllowedLeverage(stop), stop)).toBeGreaterThanOrEqual(1.25);
    }
  });

  it("never returns less than one", () => {
    expect(recommendedLeverage(0.9)).toBeGreaterThanOrEqual(1);
    expect(maxAllowedLeverage(0.9)).toBeGreaterThanOrEqual(1);
  });
});
