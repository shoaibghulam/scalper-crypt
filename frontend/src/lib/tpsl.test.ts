import { describe, expect, it } from "vitest";
import { barGeometry } from "./tpsl";

/**
 * The distance-to-TP/SL bar.
 *
 * Two rules make it honest. The bar spans the real price distance from stop to
 * target, so entry sits at its true proportional offset and a 1:3 reward really
 * looks like 1:3. And the marker tracks mark price, because that is what the
 * exchange uses for liquidation and unrealised PnL; plotting last price makes
 * the bar disagree with the venue.
 *
 * The liquidation tick is the part almost nothing ships. When liquidation falls
 * between entry and the stop, the stop can never fire: the exchange closes the
 * position first, at a worse price.
 */

const LONG = {
  side: "LONG" as const,
  entry: 63520,
  stop: 63180,
  target: 64180,
};

const SHORT = {
  side: "SHORT" as const,
  entry: 3142,
  stop: 3160,
  target: 3106,
};

describe("bar geometry, long", () => {
  it("places the stop at the left edge and the target at the right", () => {
    const g = barGeometry({ ...LONG, mark: 63520 });
    expect(g.stopPos).toBe(0);
    expect(g.targetPos).toBe(100);
  });

  it("places entry at its true proportional offset, not the centre", () => {
    // Risk is 340, reward 660, so entry sits about a third along.
    const g = barGeometry({ ...LONG, mark: 63520 });
    expect(g.entryPos).toBeCloseTo(34, 0);
  });

  it("never centres entry, because that would misrepresent risk and reward", () => {
    const g = barGeometry({ ...LONG, mark: 63520 });
    expect(g.entryPos).not.toBeCloseTo(50, 0);
  });

  it("tracks the mark price", () => {
    const g = barGeometry({ ...LONG, mark: 63742 });
    expect(g.markPos).toBeCloseTo(56.2, 1);
  });

  it("reports the reward to risk ratio", () => {
    const g = barGeometry({ ...LONG, mark: 63520 });
    expect(g.riskReward).toBeCloseTo(1.94, 2);
  });

  it("reports signed distance to each level as a price move", () => {
    const g = barGeometry({ ...LONG, mark: 63742 });
    expect(g.toTargetPct).toBeCloseTo(0.687, 2);
    expect(g.toStopPct).toBeCloseTo(-0.882, 2);
  });
});

describe("bar geometry, short", () => {
  it("still draws loss on the left and profit on the right", () => {
    // A short profits as price falls, but the bar must read the same way in
    // both directions or it cannot be scanned in a mixed table.
    const g = barGeometry({ ...SHORT, mark: 3142 });
    expect(g.stopPos).toBe(0);
    expect(g.targetPos).toBe(100);
  });

  it("moves the marker toward the target as price falls", () => {
    const near = barGeometry({ ...SHORT, mark: 3142 }).markPos;
    const nearer = barGeometry({ ...SHORT, mark: 3120 }).markPos;
    expect(nearer).toBeGreaterThan(near);
  });

  it("computes reward to risk from absolute distances", () => {
    const g = barGeometry({ ...SHORT, mark: 3142 });
    expect(g.riskReward).toBeCloseTo(2.0, 1);
  });
});

describe("out of range marks", () => {
  it("clamps the rendered marker but keeps the true value", () => {
    const g = barGeometry({ ...LONG, mark: 65000 });
    expect(g.markPos).toBe(100);
    expect(g.markPosRaw).toBeGreaterThan(100);
  });

  it("clamps below the stop as well", () => {
    const g = barGeometry({ ...LONG, mark: 62000 });
    expect(g.markPos).toBe(0);
    expect(g.markPosRaw).toBeLessThan(0);
  });
});

describe("liquidation", () => {
  it("is safe when it sits beyond the stop on a long", () => {
    const g = barGeometry({ ...LONG, mark: 63520, liquidation: 62910 });
    expect(g.liquidationInsideStop).toBe(false);
  });

  it("is flagged when it sits between entry and the stop on a long", () => {
    // Price falls, hits liquidation first, and the stop never fires.
    const g = barGeometry({ ...LONG, mark: 63520, liquidation: 63300 });
    expect(g.liquidationInsideStop).toBe(true);
  });

  it("is safe when it sits beyond the stop on a short", () => {
    const g = barGeometry({ ...SHORT, mark: 3142, liquidation: 3200 });
    expect(g.liquidationInsideStop).toBe(false);
  });

  it("is flagged when it sits between entry and the stop on a short", () => {
    const g = barGeometry({ ...SHORT, mark: 3142, liquidation: 3150 });
    expect(g.liquidationInsideStop).toBe(true);
  });

  it("is absent when no liquidation price is known", () => {
    const g = barGeometry({ ...LONG, mark: 63520 });
    expect(g.liquidationPos).toBeNull();
    expect(g.liquidationInsideStop).toBe(false);
  });
});

describe("degenerate input", () => {
  it("refuses a zero width span rather than dividing by zero", () => {
    expect(() =>
      barGeometry({ side: "LONG", entry: 100, stop: 100, target: 100, mark: 100 }),
    ).toThrow(/span/i);
  });
});

describe("nearerLevel", () => {
  const LONG = { side: "LONG" as const, entry: 100, stop: 99.5, target: 101.5 };

  it("names the stop when price has fallen toward it", () => {
    const g = barGeometry({ ...LONG, mark: 99.6 });
    expect(g.nearer).toBe("stop");
  });

  it("names the target when price has risen toward it", () => {
    const g = barGeometry({ ...LONG, mark: 101.4 });
    expect(g.nearer).toBe("target");
  });

  it("reports the distance to whichever level is nearer, unsigned", () => {
    // The label already says which way. A sign on top of that reads as a
    // direction the price has to move, which is the opposite of the meaning.
    const g = barGeometry({ ...LONG, mark: 99.6 });
    expect(g.nearerPct).toBeCloseTo(0.1, 3);
  });

  it("measures distance in price, not in bar position", () => {
    // Entry sits a quarter of the way along this bar, so bar position and
    // price distance disagree. The number an operator acts on is the price.
    const g = barGeometry({ ...LONG, mark: 100 });
    expect(g.nearer).toBe("stop");
    expect(g.nearerPct).toBeCloseTo(0.5, 3);
  });

  it("works the same way for a short", () => {
    const SHORT = { side: "SHORT" as const, entry: 100, stop: 100.5, target: 98.5 };
    expect(barGeometry({ ...SHORT, mark: 100.4 }).nearer).toBe("stop");
    expect(barGeometry({ ...SHORT, mark: 98.6 }).nearer).toBe("target");
  });

  it("reports how far along the trade has travelled", () => {
    // Zero at entry, 100 at the target, negative once price is in the red.
    // This is what the pointer is pointing at.
    expect(barGeometry({ ...LONG, mark: 100 }).progressPct).toBeCloseTo(0, 3);
    expect(barGeometry({ ...LONG, mark: 101.5 }).progressPct).toBeCloseTo(100, 3);
    expect(barGeometry({ ...LONG, mark: 99.5 }).progressPct).toBeCloseTo(-100, 3);
  });

  it("reports progress past the target rather than clamping it", () => {
    // A position through its target has not been closed yet. Showing 100%
    // would hide that the exchange has not filled the take-profit.
    expect(barGeometry({ ...LONG, mark: 102 }).progressPct).toBeGreaterThan(100);
  });
});
