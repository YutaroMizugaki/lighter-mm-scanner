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

async function fetchJson<T>(path: string): Promise<T | null> {
  const base = baseUrl();
  if (!base) return null;
  const url = `${base}/${path.replace(/^\//, "")}`;
  try {
    const res = await fetch(url, { next: { revalidate: 60 } });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export async function getOverview(): Promise<Overview | null> {
  return fetchJson<Overview>("latest.json");
}

export async function getMarkets(): Promise<MarketRow[]> {
  const data = await fetchJson<{ markets: MarketRow[] }>("markets.json");
  return data?.markets ?? [];
}

export async function getMarket(symbol: string): Promise<MarketRow | null> {
  return fetchJson<MarketRow>(`market/${encodeURIComponent(symbol)}.json`);
}

export function fmt(n: number | null | undefined, digits = 2, signed = false): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return signed ? n.toFixed(digits) : n.toFixed(digits);
}
