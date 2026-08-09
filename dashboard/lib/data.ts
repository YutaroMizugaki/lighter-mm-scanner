export type Overview = {
  title: string;
  status: string;
  run_id: string | null;
  started_at: string | null;
  observation_hours: number | null;
  observation_target_hours: number | null;
  markets: number;
  markets_analyzed?: number;
  markets_discovered?: number;
  candidates: number;
  coverage_pct: number | null;
  last_update: string | null;
  git_sha: string | null;
  collector_version: string | null;
  analysis_error?: string | null;
  health_warnings?: string[];
  samples_written?: number;
  top_candidate: {
    symbol: string;
    score: number;
    median_spread_bps: number | null;
    maker_markout_5s_median_bps: number | null;
    maker_markout_30s_median_bps: number | null;
    letter_rank?: string;
  } | null;
  storage_estimate?: Record<string, unknown> | null;
  generated_at: string;
  disclaimer: string;
};

export type MarketRow = {
  symbol: string;
  market_id: number;
  score: number;
  letter_rank: string;
  is_candidate: boolean;
  median_spread_bps: number | null;
  pct_time_spread_ge_5bps: number | null;
  median_two_sided_depth_10bps_usd: number | null;
  trades_per_minute_median: number | null;
  maker_markout_5s_median_bps: number | null;
  maker_markout_30s_median_bps: number | null;
  current_funding_rate: number | null;
  data_coverage_pct: number | null;
  warnings?: string[];
  pros?: string[];
  cons?: string[];
};

function baseUrl(): string {
  return (process.env.NEXT_PUBLIC_DATA_BASE_URL || "").replace(/\/$/, "");
}

export type FetchResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; status?: number };

async function fetchJson<T>(path: string): Promise<FetchResult<T>> {
  const base = baseUrl();
  if (!base) return { ok: false, error: "NEXT_PUBLIC_DATA_BASE_URL is not set" };
  // Cache-bust query: GCS public objects previously defaulted to max-age=3600,
  // so the edge kept serving a frozen latest.json for up to an hour.
  const url = `${base}/${path.replace(/^\//, "")}?t=${Date.now()}`;
  try {
    const res = await fetch(url, {
      cache: "no-store",
      next: { revalidate: 0 },
      headers: { "Cache-Control": "no-cache" },
    });
    if (!res.ok) {
      return {
        ok: false,
        error: `HTTP ${res.status} fetching ${path}`,
        status: res.status,
      };
    }
    return { ok: true, data: (await res.json()) as T };
  } catch (err) {
    const msg = err instanceof Error ? err.message : "network error";
    return { ok: false, error: msg };
  }
}

export async function getOverview(): Promise<Overview | null> {
  const result = await fetchJson<Overview>("latest.json");
  return result.ok ? result.data : null;
}

export async function getOverviewResult(): Promise<FetchResult<Overview>> {
  return fetchJson<Overview>("latest.json");
}

export async function getMarkets(): Promise<MarketRow[]> {
  const result = await fetchJson<{ markets: MarketRow[] }>("markets.json");
  return result.ok ? result.data.markets ?? [] : [];
}

export async function getMarket(symbol: string): Promise<MarketRow | null> {
  const result = await fetchJson<MarketRow>(`market/${encodeURIComponent(symbol)}.json`);
  return result.ok ? result.data : null;
}

export function fmt(n: number | null | undefined, digits = 2, signed = false): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const body = n.toFixed(digits);
  if (!signed) return body;
  return n > 0 ? `+${body}` : body;
}

/** Recompute status from JSON age — baked COLLECTING lies when publish stalls. */
export function effectiveStatus(
  overview: Overview,
  okMinutes = 20,
  warnMinutes = 40,
): string {
  const baked = overview.status || "ERROR";
  if (baked === "COMPLETED" || baked === "ERROR") return baked;
  // Prefer the fresher of last_update / generated_at. A stalled
  // last_successful_flush previously forced OFFLINE even while generated_at
  // (and markets) kept advancing every sync.
  const stamps = [overview.last_update, overview.generated_at].filter(
    (x): x is string => Boolean(x),
  );
  if (!stamps.length) return "ERROR";
  const ageMin = Math.min(
    ...stamps.map((s) => (Date.now() - new Date(s).getTime()) / 60_000),
  );
  if (Number.isNaN(ageMin)) return "ERROR";
  const analyzed = overview.markets_analyzed ?? overview.markets ?? 0;
  const samples = overview.samples_written ?? 0;
  if (ageMin > warnMinutes) return "OFFLINE";
  if (ageMin > okMinutes) return "STALE";
  if (baked === "DEGRADED") return "DEGRADED";
  if (analyzed === 0 && samples > 0) return "DEGRADED";
  // Fresh publish with markets: trust COLLECTING even if baked status lagged.
  if (analyzed > 0 && (baked === "OFFLINE" || baked === "STALE")) return "COLLECTING";
  return baked;
}
