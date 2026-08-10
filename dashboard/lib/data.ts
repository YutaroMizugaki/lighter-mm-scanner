export type CollectorStatus = {
  run_id: string;
  status: string;
  started_at: string;
  ended_at?: string | null;
  generated_at: string;
  last_successful_sync: string | null;
  last_durable_event_at?: string | null;
  last_sync_attempt_at?: string | null;
  last_sync_error?: string | null;
  consecutive_sync_failures?: number;
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
  status:
    | "NOT_STARTED"
    | "NO_ACTIVE_RUN"
    | "RUNNING"
    | "OK"
    | "DEGRADED"
    | "ERROR"
    | string;
  run_id: string | null;
  generated_at: string;
  started_at?: string | null;
  error?: string | null;
  last_successful_analysis_at?: string | null;
  duration_seconds?: number | null;
  git_sha?: string | null;
  analyzer_version?: string | null;
  start_ms?: number;
  end_ms?: number;
  analysis_end_ms?: number;
  durable_watermark_ms?: number;
  book_rows?: number;
  trade_rows?: number;
  markout_rows?: number;
  markets_analyzed?: number;
  candidates?: number;
  valid_parquet_files?: number;
  corrupt_parquet_files?: number;
  skipped_files?: Array<{ path: string; error: string }>;
  parquet_health_status?: string;
  analysis_window_hours?: number | null;
  run_observation_hours?: number | null;
};

export type DashboardGeneration = {
  analysis_id: string;
  generated_at: string;
};

export type Overview = {
  title: string;
  status: string;
  run_id: string | null;
  started_at: string | null;
  observation_hours: number | null;
  run_observation_hours?: number | null;
  analysis_scope?: string | null;
  analysis_window_hours?: number | null;
  observation_target_hours: number | null;
  markets: number;
  markets_analyzed?: number;
  markets_discovered?: number;
  candidates: number;
  coverage_pct: number | null;
  last_update: string | null;
  last_data_at?: string | null;
  last_successful_sync?: string | null;
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

export type EstimatedFillSideRates = {
  optimistic?: number | null;
  conservative?: number | null;
  bid_optimistic?: number | null;
  bid_conservative?: number | null;
  ask_optimistic?: number | null;
  ask_conservative?: number | null;
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
  markout_sample_quality?: string | null;
  estimated_maker_fill_rate_5s_conservative?: number | null;
  estimated_maker_fill_rate_30s_conservative?: number | null;
  estimated_maker_fill_rate_5s_optimistic?: number | null;
  estimated_maker_fill_rate_30s_optimistic?: number | null;
  estimated_maker_fill_samples?: number | null;
  estimated_maker_fill_sample_quality?: string | null;
  estimated_maker_edge_5s_bps?: number | null;
  estimated_maker_edge_30s_bps?: number | null;
  estimated_maker_edge_fee_included?: boolean | null;
  estimated_maker_fill_by_size?: Record<
    string,
    Record<string, EstimatedFillSideRates>
  > | null;
  estimated_maker_fill_order_usd_default?: number | null;
  analysis_scope?: string | null;
  analysis_window_hours?: number | null;
  current_funding_rate: number | null;
  data_coverage_pct: number | null;
  observation_coverage_pct?: number | null;
  usable_quote_coverage_pct?: number | null;
  spread_coverage_pct?: number | null;
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

export type DashboardBundle = {
  prefix: string;
  legacy: boolean;
};

export async function resolveDashboardBundle(): Promise<DashboardBundle> {
  const current = await fetchJson<DashboardGeneration>("current.json");
  if (current.ok && current.data.analysis_id) {
    return {
      prefix: `generations/${current.data.analysis_id}/`,
      legacy: false,
    };
  }
  return { prefix: "", legacy: true };
}

async function resolveGenerationPrefix(): Promise<string> {
  const bundle = await resolveDashboardBundle();
  return bundle.prefix;
}

export async function getOverview(): Promise<Overview | null> {
  const bundle = await resolveDashboardBundle();
  const result = await fetchJson<Overview>(`${bundle.prefix}latest.json`);
  if (result.ok) return result.data;
  if (!bundle.legacy) {
    const legacy = await fetchJson<Overview>("latest.json");
    return legacy.ok ? legacy.data : null;
  }
  return null;
}

export async function getOverviewResult(): Promise<FetchResult<Overview>> {
  const bundle = await resolveDashboardBundle();
  const result = await fetchJson<Overview>(`${bundle.prefix}latest.json`);
  if (result.ok) return result;
  if (!bundle.legacy) return fetchJson<Overview>("latest.json");
  return result;
}

export async function getCollectorStatusResult(): Promise<FetchResult<CollectorStatus>> {
  return fetchJson<CollectorStatus>("collector_status.json");
}

export async function getAnalysisStatusResult(): Promise<FetchResult<AnalysisStatus>> {
  return fetchJson<AnalysisStatus>("analysis_status.json");
}

export async function getMarketsResult(
  bundle?: DashboardBundle,
): Promise<FetchResult<{ markets: MarketRow[] }>> {
  const resolved = bundle ?? (await resolveDashboardBundle());
  const result = await fetchJson<{ markets: MarketRow[] }>(
    `${resolved.prefix}markets.json`,
  );
  if (result.ok) return result;
  if (!resolved.legacy) return fetchJson<{ markets: MarketRow[] }>("markets.json");
  return result;
}

/** Compatibility helper — prefer getMarketsResult() when failure must surface. */
export async function getMarkets(): Promise<MarketRow[]> {
  const result = await getMarketsResult();
  return result.ok ? result.data.markets ?? [] : [];
}

export async function getMarket(
  symbol: string,
  bundle?: DashboardBundle,
): Promise<MarketRow | null> {
  const resolved = bundle ?? (await resolveDashboardBundle());
  const result = await fetchJson<MarketRow>(
    `${resolved.prefix}market/${encodeURIComponent(symbol)}.json`,
  );
  if (result.ok) return result.data;
  if (!resolved.legacy) {
    const legacy = await fetchJson<MarketRow>(`market/${encodeURIComponent(symbol)}.json`);
    return legacy.ok ? legacy.data : null;
  }
  return null;
}

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

export const ESTIMATED_FILL_TOOLTIP =
  "公開板と公開約定データから、Best Bid / Ask に仮想 Maker 注文を置いた場合の約定機会を推定した指標です。実際の queue position、自身の注文履歴、cancel latency は含まれないため、実約定率ではありません。ランキング基準サイズは $50（conservative / 30s）です。";

/** Format an ISO timestamp in Japan Standard Time with an explicit JST suffix. */
export function fmtJst(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return (
    d.toLocaleString("ja-JP", { timeZone: "Asia/Tokyo" }) + " JST"
  );
}

function ageMinutes(stamp: string | null | undefined): number | null {
  if (!stamp) return null;
  const ageMin = (Date.now() - new Date(stamp).getTime()) / 60_000;
  return Number.isNaN(ageMin) ? null : ageMin;
}

function wsIsDegraded(ws: CollectorStatus["ws"]): boolean {
  if (!ws) return false;
  const connected = ws.connected_shards ?? 0;
  const total = ws.total_shards ?? 0;
  const planned = ws.planned_channels ?? ws.subscribed_channels ?? 0;
  const acked = ws.acked_channels ?? ws.subscribed_channels ?? 0;
  if (total > 0 && connected < total) return true;
  if (planned > 0 && acked < planned) return true;
  return false;
}

/**
 * Recompute collector status from durable market-event freshness, sync health, and WS health.
 */
export function effectiveCollectorStatus(
  collector: CollectorStatus,
  okMinutes = 20,
  warnMinutes = 40,
): string {
  const baked = collector.status || "ERROR";
  if (baked === "COMPLETED" || baked === "ERROR") return baked;

  const syncDegraded =
    (collector.consecutive_sync_failures ?? 0) > 0 || Boolean(collector.last_sync_error);
  const operationalDegraded = syncDegraded || wsIsDegraded(collector.ws) || baked === "DEGRADED";

  const eventStamp = collector.last_durable_event_at || null;
  if (!eventStamp) {
    if (syncDegraded) return "DEGRADED";
    return baked === "DEGRADED" ? "DEGRADED" : "STALE";
  }

  const eventAge = ageMinutes(eventStamp);
  if (eventAge === null) return "ERROR";
  if (eventAge > warnMinutes) return "OFFLINE";
  if (eventAge > okMinutes) return "STALE";
  if (operationalDegraded) return "DEGRADED";
  if (baked === "COLLECTING" || baked === "STALE" || baked === "OFFLINE") return "COLLECTING";
  return baked;
}

/**
 * Analysis freshness from analysis_status.json (independent of collector).
 */
export function effectiveAnalysisStatus(
  analysis: AnalysisStatus | null,
  staleMinutes = 30,
  runningStaleMinutes = 30,
): { status: string; stale: boolean } {
  if (!analysis) return { status: "NOT_STARTED", stale: true };
  if (analysis.status === "NOT_STARTED") return { status: "NOT_STARTED", stale: true };
  if (analysis.status === "NO_ACTIVE_RUN") return { status: "NO_ACTIVE_RUN", stale: false };
  if (analysis.status === "ERROR") return { status: "ERROR", stale: false };

  if (analysis.status === "RUNNING") {
    const runningStamp = analysis.started_at || analysis.generated_at || null;
    const runningAge = ageMinutes(runningStamp);
    if (runningAge !== null && runningAge > runningStaleMinutes) {
      return { status: "STALE", stale: true };
    }
    return { status: "RUNNING", stale: false };
  }

  const stamp =
    analysis.last_successful_analysis_at || analysis.generated_at || null;
  const ageMin = ageMinutes(stamp);
  const stale = ageMin !== null && ageMin > staleMinutes;

  if (analysis.status === "DEGRADED") {
    if (stale) return { status: "STALE", stale: true };
    return { status: "DEGRADED", stale: false };
  }
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
    (analysis.status === "OK" || analysis.status === "DEGRADED"
      ? analysis.generated_at
      : null)
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
  const ageMin = ageMinutes(stamp);
  if (ageMin === null) return "ERROR";
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
      return "Market data has not been durably collected for >40m.";
    }
    if (status === "STALE") {
      return "Latest durable market event is older than 20m.";
    }
    if (status === "DEGRADED") {
      return "Collector degraded — check sync failures, durable event freshness, or WebSocket health.";
    }
    return null;
  }
  if (status === "NOT_STARTED") {
    return "Analyzer has not published a status yet.";
  }
  if (status === "NO_ACTIVE_RUN") {
    return "No active collector run is available to analyze.";
  }
  if (status === "ERROR") {
    return "Analyzer run failed — see error details below.";
  }
  if (status === "DEGRADED") {
    return "一部の収集データを読み込めませんでした。破損ファイルを除外して解析を継続しています。";
  }
  if (status === "STALE") {
    return "Analysis results are older than 30m (expected cadence: 15m).";
  }
  return null;
}
