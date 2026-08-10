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
