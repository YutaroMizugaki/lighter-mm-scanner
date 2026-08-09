export type CollectorStatus = {
  run_id: string;
  status: string;
  started_at: string;
  ended_at?: string | null;
  generated_at: string;
  last_successful_sync: string | null;
  samples_written: number;
  trades_written: number;
  markouts_written: number;
  last_trade_at?: string | null;
  last_usable_book_sample_at?: string | null;
  last_book_row_at?: string | null;
  trades_without_reference_mid?: number;
  ws?: Overview["ws"];
  health_warnings?: string[];
  git_sha?: string | null;
  collector_version?: string | null;
};

export type AnalysisStatus = {
  status: "RUNNING" | "OK" | "ERROR" | string;
  run_id: string | null;
  generated_at: string;
  started_at?: string | null;
  error?: string | null;
  last_successful_analysis_at?: string | null;
  duration_seconds?: number | null;
  start_ms?: number;
  end_ms?: number;
  book_rows?: number;
  trade_rows?: number;
  markout_rows?: number;
  markets_analyzed?: number;
  candidates?: number;
};

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
  last_successful_flush?: string | null;
  last_trade_at?: string | null;
  last_book_sample_at?: string | null;
  last_usable_book_sample_at?: string | null;
  last_book_row_at?: string | null;
  trades_without_reference_mid?: number;
  git_sha: string | null;
  collector_version: string | null;
  analysis_error?: string | null;
  health_warnings?: string[];
  samples_written?: number;
  ws?: {
    connected_shards?: number;
    total_shards?: number;
    planned_channels?: number;
    acked_channels?: number;
    subscribed_channels?: number;
    dropped_connections?: number;
    subscription_errors?: number;
    trade_parse_errors?: number;
    book_resyncs?: number;
    nonce_gaps?: number;
    last_ws_error?: string | null;
  } | null;
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
  trades_per_minute_mean: number | null;
  total_trade_count: number | null;
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

export async function getCollectorStatusResult(): Promise<FetchResult<CollectorStatus>> {
  return fetchJson<CollectorStatus>("collector_status.json");
}

export async function getAnalysisStatusResult(): Promise<FetchResult<AnalysisStatus>> {
  return fetchJson<AnalysisStatus>("analysis_status.json");
}

export async function getMarketsResult(): Promise<
  FetchResult<{ markets: MarketRow[] }>
> {
  return fetchJson<{ markets: MarketRow[] }>("markets.json");
}

/** Compatibility helper — prefer getMarketsResult() when failure must surface. */
export async function getMarkets(): Promise<MarketRow[]> {
  const result = await getMarketsResult();
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

/** Format an ISO timestamp in Japan Standard Time with an explicit JST suffix. */
export function fmtJst(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return (
    d.toLocaleString("ja-JP", { timeZone: "Asia/Tokyo" }) + " JST"
  );
}

/**
 * Recompute collector status from collector_status.json flush age.
 */
export function effectiveCollectorStatus(
  collector: CollectorStatus,
  okMinutes = 20,
  warnMinutes = 40,
): string {
  const baked = collector.status || "ERROR";
  if (baked === "COMPLETED" || baked === "ERROR") return baked;
  const stamp = collector.last_successful_sync || collector.generated_at || null;
  if (!stamp) return "ERROR";
  const ageMin = (Date.now() - new Date(stamp).getTime()) / 60_000;
  if (Number.isNaN(ageMin)) return "ERROR";
  if (ageMin > warnMinutes) return "OFFLINE";
  if (ageMin > okMinutes) return "STALE";
  if (baked === "DEGRADED") return "DEGRADED";
  return baked;
}

/**
 * Analysis freshness from analysis_status.json (independent of collector).
 */
export function effectiveAnalysisStatus(
  analysis: AnalysisStatus | null,
  staleMinutes = 30,
): { status: string; stale: boolean } {
  if (!analysis) return { status: "UNKNOWN", stale: true };
  if (analysis.status === "ERROR") return { status: "ERROR", stale: false };
  if (analysis.status === "RUNNING") return { status: "RUNNING", stale: false };
  const stamp =
    analysis.last_successful_analysis_at || analysis.generated_at || null;
  if (!stamp) return { status: analysis.status, stale: true };
  const ageMin = (Date.now() - new Date(stamp).getTime()) / 60_000;
  const stale = ageMin > staleMinutes;
  if (stale && analysis.status === "OK") {
    return { status: "STALE", stale: true };
  }
  if (analysis.status === "OK") return { status: "OK", stale: false };
  return { status: analysis.status, stale };
}

/** Timestamp to display for last successful analysis (OK or preserved after ERROR). */
export function analysisDisplayTimestamp(
  analysis: AnalysisStatus | null,
): string | null {
  if (!analysis) return null;
  return (
    analysis.last_successful_analysis_at ||
    (analysis.status === "OK" ? analysis.generated_at : null)
  );
}

/**
 * Recompute status from analysis latest.json generated_at (legacy overview).
 */
export function effectiveStatus(
  overview: Overview,
  okMinutes = 20,
  warnMinutes = 40,
): string {
  const baked = overview.status || "ERROR";
  if (baked === "COMPLETED" || baked === "ERROR") return baked;
  const stamp = overview.last_successful_flush || overview.last_update || null;
  if (!stamp) return "ERROR";
  const ageMin = (Date.now() - new Date(stamp).getTime()) / 60_000;
  if (Number.isNaN(ageMin)) return "ERROR";
  const analyzed = overview.markets_analyzed ?? overview.markets ?? 0;
  const samples = overview.samples_written ?? 0;
  if (ageMin > warnMinutes) return "OFFLINE";
  if (ageMin > okMinutes) return "STALE";
  if (baked === "DEGRADED") return "DEGRADED";
  if (analyzed === 0 && samples > 0) return "DEGRADED";
  // Fresh flush with markets: trust COLLECTING even if baked status lagged.
  if (analyzed > 0 && (baked === "OFFLINE" || baked === "STALE")) return "COLLECTING";
  return baked;
}

export function statusHealthNote(
  status: string,
  scope: "collector" | "analysis" = "collector",
): string | null {
  if (scope === "collector") {
    if (status === "OFFLINE") {
      return "Collector sync has not succeeded for >40m.";
    }
    if (status === "STALE") {
      return "Collector sync is older than 20m.";
    }
    return null;
  }
  if (status === "ERROR") {
    return "Analyzer run failed — see analysis_status.json error.";
  }
  if (status === "STALE") {
    return "Analysis results are older than 30m (expected cadence: 15m).";
  }
  if (status === "UNKNOWN") {
    return "analysis_status.json missing or unreadable.";
  }
  return null;
}
