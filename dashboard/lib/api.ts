import type {
  AnalysisStatus,
  CollectorStatus,
  DashboardBundle,
  DashboardGeneration,
  FetchResult,
  MarketRow,
  Overview,
} from "./types";

type FetchPolicy = "live" | "generation";

function baseUrl(): string {
  return (process.env.NEXT_PUBLIC_DATA_BASE_URL || "").replace(/\/$/, "");
}

export async function fetchJson<T>(
  path: string,
  policy: FetchPolicy = "live",
): Promise<FetchResult<T>> {
  const base = baseUrl();
  if (!base) return { ok: false, error: "NEXT_PUBLIC_DATA_BASE_URL is not set" };

  const normalizedPath = path.replace(/^\/+/, "");
  const live = policy === "live";
  const url = live
    ? `${base}/${normalizedPath}?t=${Date.now()}`
    : `${base}/${normalizedPath}`;

  try {
    const res = await fetch(
      url,
      live
        ? {
            cache: "no-store",
            next: { revalidate: 0 },
            headers: { "Cache-Control": "no-cache" },
          }
        : {
            next: { revalidate: false },
          },
    );
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

export async function getOverview(
  bundle?: DashboardBundle,
): Promise<Overview | null> {
  const result = await getOverviewResult(bundle);
  return result.ok ? result.data : null;
}

export async function getOverviewResult(
  bundle?: DashboardBundle,
): Promise<FetchResult<Overview>> {
  const resolved = bundle ?? (await resolveDashboardBundle());

  if (resolved.legacy) {
    return fetchJson<Overview>("latest.json", "live");
  }

  const result = await fetchJson<Overview>(
    `${resolved.prefix}latest.json`,
    "generation",
  );
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

  if (resolved.legacy) {
    return fetchJson<{ markets: MarketRow[] }>("markets.json", "live");
  }

  const result = await fetchJson<{ markets: MarketRow[] }>(
    `${resolved.prefix}markets.json`,
    "generation",
  );
  return result;
}

export async function getCandidatesResult(
  bundle?: DashboardBundle,
): Promise<FetchResult<{ candidates: MarketRow[] }>> {
  const resolved = bundle ?? (await resolveDashboardBundle());

  if (resolved.legacy) {
    return fetchJson<{ candidates: MarketRow[] }>("candidates.json", "live");
  }

  const result = await fetchJson<{ candidates: MarketRow[] }>(
    `${resolved.prefix}candidates.json`,
    "generation",
  );
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
  const encoded = encodeURIComponent(symbol);

  if (resolved.legacy) {
    const legacy = await fetchJson<MarketRow>(`market/${encoded}.json`, "live");
    return legacy.ok ? legacy.data : null;
  }

  const result = await fetchJson<MarketRow>(
    `${resolved.prefix}market/${encoded}.json`,
    "generation",
  );
  return result.ok ? result.data : null;
}
