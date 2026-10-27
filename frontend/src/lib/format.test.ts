import { describe, expect, it } from "vitest";
import { formatPct, formatSigned, pnlGlyph, pnlTone } from "./format";

/**
 * Number presentation.
 *
 * Colour is never the only carrier of meaning here. Every profit and loss value
 * also gets an explicit sign and a direction glyph, so the table stays readable
 * with a colour vision deficiency, in greyscale, and on a washed-out screen.
 */

describe("signed values", () => {
  it("always shows a plus on gains", () => {
    expect(formatSigned(26.64, 2)).toBe("+26.64");
  });

  it("shows a minus on losses", () => {
    expect(formatSigned(-23.4, 2)).toBe("-23.40");
  });

  it("leaves zero unsigned", () => {
    expect(formatSigned(0, 2)).toBe("0.00");
  });

  it("keeps the decimal count fixed so the point never moves", () => {
    expect(formatSigned(5, 2)).toBe("+5.00");
    expect(formatSigned(5.129, 2)).toBe("+5.13");
  });
});

describe("percentages", () => {
  it("signs and suffixes", () => {
    expect(formatPct(0.35, 2)).toBe("+0.35%");
    expect(formatPct(-0.53, 2)).toBe("-0.53%");
  });
});

describe("redundant encoding", () => {
  it("gives gains an upward glyph", () => {
    expect(pnlGlyph(1)).toBe("\u25B2");
  });

  it("gives losses a downward glyph", () => {
    expect(pnlGlyph(-1)).toBe("\u25BC");
  });

  it("gives flat a neutral glyph", () => {
    expect(pnlGlyph(0)).toBe("\u2013");
  });

  it("treats zero as neutral, never as a gain", () => {
    // Rendering 0.00 in green is a small lie that erodes trust in every other
    // number on the screen.
    expect(pnlTone(0)).toBe("flat");
  });

  it("tones gains and losses", () => {
    expect(pnlTone(0.01)).toBe("pos");
    expect(pnlTone(-0.01)).toBe("neg");
  });
});
