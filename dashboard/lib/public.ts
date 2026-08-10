/**
 * Public-facing presentation helpers.
 * Sanitizes operational details so they never appear in the public DOM.
 */

const SENSITIVE_PATTERNS: RegExp[] = [
  /gs:\/\/[^\s`'")\]]+/gi,
  /storage\.googleapis\.com\/[^\s`'")\]]+/gi,
  /\/(?:tmp|var|mnt|home|Users|opt|workspace|app)\/[^\s`'")\]]+/gi,
  /[A-Za-z]:\\[^\s`'")\]]+/gi,
  /\bNEXT_PUBLIC_[A-Z0-9_]+\b/g,
  /\b[A-Z][A-Z0-9_]*(?:_KEY|_TOKEN|_SECRET|_PASSWORD|_CREDENTIAL|_IAM)\b/g,
  /\b(?:GCS_BUCKET|BUCKET|CORS|IAM)\b/gi,
  /\blatest\.json\b/gi,
  /\bmarkets\.json\b/gi,
  /\bcollector_status\.json\b/gi,
  /\banalysis_status\.json\b/gi,
  /\bTraceback\b[\s\S]{0,2000}/gi,
  /\bat\s+\S+\s+\(.*?:\d+:\d+\)/gi,
  /\bhttps?:\/\/(?:127\.0\.0\.1|localhost|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[0-1])\.\d+\.\d+)[^\s`'")\]]*/gi,
  /\b(?:Bearer|token|secret|credential|password)\s*[:=]\s*\S+/gi,
];

export function containsSensitiveText(text: string | null | undefined): boolean {
  if (!text) return false;
  return SENSITIVE_PATTERNS.some((re) => {
    re.lastIndex = 0;
    return re.test(text);
  });
}

/** Strip paths, bucket names, env vars, stack traces from any public string. */
export function sanitizePublicText(
  text: string | null | undefined,
  fallback = "Internal detail omitted.",
): string {
  if (!text) return fallback;
  let out = text;
  for (const re of SENSITIVE_PATTERNS) {
    re.lastIndex = 0;
    out = out.replace(re, "[redacted]");
  }
  out = out.replace(/\s{2,}/g, " ").trim();
  if (!out || out === "[redacted]" || /^[\[\]redacted\s.,;:-]+$/i.test(out)) {
    return fallback;
  }
  if (containsSensitiveText(out)) return fallback;
  return out;
}

export function publicDataUnavailableMessage(kind: "overview" | "markets" | "market" | "config" = "overview"): {
  title: string;
  body: string;
} {
  if (kind === "config") {
    return {
      title: "Market data is temporarily unavailable.",
      body: "The dashboard could not load the latest analysis. Please try again later.",
    };
  }
  if (kind === "markets") {
    return {
      title: "Market data is temporarily unavailable.",
      body: "The latest analysis could not be loaded. Please try again later.",
    };
  }
  if (kind === "market") {
    return {
      title: "Market detail is not available.",
      body: "This market could not be loaded from the latest analysis. Please try again later.",
    };
  }
  return {
    title: "Market data is temporarily unavailable.",
    body: "The dashboard could not load the latest analysis. Please try again later.",
  };
}

export function publicAnalysisPendingMessage(): { title: string; body: string } {
  return {
    title: "Analysis data is not available yet.",
    body: "The dashboard is waiting for the next published analysis. Please check back shortly.",
  };
}

export type FreshnessLevel = "current" | "delayed" | "unavailable";

export function analysisFreshnessLevel(status: string): FreshnessLevel {
  if (status === "OK" || status === "RUNNING" || status === "DEGRADED" || status === "COMPLETED") {
    return "current";
  }
  if (status === "STALE") return "delayed";
  return "unavailable";
}

/** Map analysis freshness to public labels. Does not change status detection. */
export function publicFreshnessCopy(
  status: string,
  lastAnalysisAt: string | null,
  relative: string,
): { level: FreshnessLevel; label: string; detail: string } {
  const level = analysisFreshnessLevel(status);
  if (level === "current") {
    return {
      level,
      label: "Data current",
      detail: lastAnalysisAt ? `Updated ${relative}` : "Latest analysis is available",
    };
  }
  if (level === "delayed") {
    return {
      level,
      label: "Data delayed",
      detail: lastAnalysisAt
        ? `Latest analysis ${relative}`
        : "Latest analysis is older than expected",
    };
  }
  return {
    level,
    label: "Data unavailable",
    detail: "No usable analysis is currently available",
  };
}

export function formatRelativeAge(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "unknown";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "unknown";
  const mins = Math.max(0, Math.round((now - t) / 60_000));
  if (mins < 1) return "just now";
  if (mins === 1) return "1 min ago";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours === 1) return "1 hour ago";
  if (hours < 48) return `${hours} hours ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "1 day ago" : `${days} days ago`;
}

export function formatUsdCompact(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `$${(n / 1_000_000).toFixed(digits)}M`;
  if (abs >= 1_000) return `$${(n / 1_000).toFixed(digits)}k`;
  return `$${n.toFixed(0)}`;
}

export function formatDepth(n: number | null | undefined): string {
  return formatUsdCompact(n, 1);
}

export function formatActivity(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `${n.toFixed(1)}/min`;
}

export function rankLabel(letter: string | null | undefined): string {
  switch (letter) {
    case "A":
      return "Strong";
    case "B":
      return "Good";
    case "C":
      return "Watch";
    case "D":
      return "Weak";
    default:
      return "";
  }
}

export function rankTooltip(letter: string | null | undefined): string {
  switch (letter) {
    case "A":
      return "A — strongest research candidates";
    case "B":
      return "B — good candidates";
    case "C":
      return "C — mixed evidence";
    case "D":
      return "D — weak or insufficient evidence";
    default:
      return "Letter rank based on current research score";
  }
}

export function rankSubtext(letter: string | null | undefined, isCandidate: boolean): string {
  if (isCandidate && letter === "A") {
    return "Strong candidate based on current observation window.";
  }
  if (isCandidate && letter === "B") {
    return "Good candidate based on current observation window.";
  }
  if (isCandidate) {
    return "Meets candidate thresholds in the current observation window.";
  }
  if (letter === "C") {
    return "Mixed evidence in the current observation window.";
  }
  return "Weak or insufficient evidence in the current observation window.";
}

export function publicCorruptFilesNotice(count: number): string {
  if (count <= 0) return "";
  if (count === 1) return "1 source file could not be processed.";
  return `${count} source files could not be processed.`;
}

export function publicHealthWarning(raw: string): string {
  const lower = raw.toLowerCase();
  if (
    lower.includes("corrupt") ||
    lower.includes("parquet") ||
    lower.includes("skipped") ||
    containsSensitiveText(raw)
  ) {
    return "Some source data could not be processed. The latest valid analysis is still shown.";
  }
  return sanitizePublicText(raw, "A data quality issue was detected.");
}

export const TOOLTIPS = {
  makerMarkout:
    "Price movement after a maker fill. Positive is favorable to the maker.",
  estimatedFill:
    "Simulates whether aggressive trade flow could clear a small maker quote at the touch. Not actual fill probability. Ranking uses $50 / 30s / Conservative.",
  estimatedEdge:
    "Estimated Fill × (Maker Markout − Maker Fee). Explanatory metric, not expected profit.",
  depth10bp: "Two-sided quote liquidity within 10 basis points of mid.",
  coverage: "Share of the expected observation window containing usable data.",
  sampleQuality:
    "Reliability of the Estimated Fill / markout sample. Insufficient means too few observations — not a measured 0%.",
  tradesPerMin: "Market-level trade prints per minute. Not Estimated Maker Fill.",
  score: "Relative research ranking across markets — not an expected return forecast.",
} as const;
