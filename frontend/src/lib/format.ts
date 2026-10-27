/**
 * Number presentation for a dense numeric console.
 *
 * Fixed decimal counts, explicit signs, and a direction glyph on every profit
 * and loss value. The glyph is not decoration: it is what keeps the table
 * readable when colour is unavailable, whether through a vision deficiency, a
 * greyscale screenshot, or a bad monitor.
 */

export type Tone = "pos" | "neg" | "flat";

export function formatSigned(value: number, decimals: number): string {
  const body = Math.abs(value).toFixed(decimals);
  if (value > 0) return `+${body}`;
  if (value < 0) return `-${body}`;
  // Unsigned at exactly zero. A plus here would imply a gain that did not happen.
  return body;
}

export function formatPct(value: number, decimals = 2): string {
  return `${formatSigned(value, decimals)}%`;
}

export function formatPrice(value: number, decimals: number): string {
  return value.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function formatUsd(value: number, decimals = 2): string {
  const body = Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  if (value > 0) return `+$${body}`;
  if (value < 0) return `-$${body}`;
  return `$${body}`;
}

export function pnlTone(value: number): Tone {
  if (value > 0) return "pos";
  if (value < 0) return "neg";
  return "flat";
}

export function pnlGlyph(value: number): string {
  if (value > 0) return "\u25B2";
  if (value < 0) return "\u25BC";
  return "\u2013";
}

/** Seconds since a timestamp, rendered as the operator needs to read it. */
export function relativeAge(seconds: number): string {
  if (seconds < 1) return "just now";
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}
