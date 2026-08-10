import type {
  AnalysisStatus,
  CollectorStatus,
  DashboardBundle,
  DashboardGeneration,
  FetchResult,
  MarketRow,
  Overview,
} from "./types";

function baseUrl(): string {
  return (process.env.NEXT_PUBLIC_DATA_BASE_URL || "").replace(/\/$/, "");
}

export async function fetchJson<T>(path: string): Promise<FetchResult<T>> {
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
