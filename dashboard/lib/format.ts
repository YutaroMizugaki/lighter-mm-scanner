export function fmt(n: number | null | undefined, digits = 2, signed = false): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const body = n.toFixed(digits);
  if (!signed) return body;
  return n > 0 ? `+${body}` : body;
}

/** Format a 0–1 fraction as percent. Null/undefined → em dash (not 0%). */
export function fmtPctFraction(
  n: number | null | undefined,
  digits = 0,
): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

/** Estimated Fill display: insufficient → label; else percent; null → em dash. */
export function fmtEstimatedFill(
  rate: number | null | undefined,
  quality?: string | null,
  digits = 0,
): string {
  if (quality === "insufficient") return "Insufficient";
  if (rate === null || rate === undefined || Number.isNaN(rate)) return "—";
  return fmtPctFraction(rate, digits);
}

/** Paper MM USD: not simulated / unavailable → em dash. */
export function fmtPaperUsd(
  value: number | null | undefined,
  status?: string | null,
  signed = false,
): string {
  if (status && status !== "ok") return "—";
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const body = value.toFixed(2);
  if (!signed) return body === "0.00" ? "$0" : `$${body}`;
  const prefix = value > 0 ? "+$" : value < 0 ? "-$" : "$";
  return `${prefix}${Math.abs(value).toFixed(2)}`;
}

export function fmtPaperBp(
  value: number | null | undefined,
  status?: string | null,
): string {
  if (status && status !== "ok") return "—";
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value > 0 ? `+${value.toFixed(1)}bp` : `${value.toFixed(1)}bp`;
}

export function fmtPaperCount(
  value: number | null | undefined,
  status?: string | null,
): string {
  if (status && status !== "ok") return "—";
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return String(value);
}

export function fmtSampleQuality(q: string | null | undefined): string {
  if (!q) return "—";
  if (q === "insufficient") return "Insufficient";
  if (q === "preliminary") return "Preliminary";
  if (q === "reliable") return "Reliable";
  return q;
}

/** Format an ISO timestamp in Japan Standard Time with an explicit JST suffix. */
export function fmtJst(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return (
    d.toLocaleString("ja-JP", { timeZone: "Asia/Tokyo" }) + " JST"
  );
}
