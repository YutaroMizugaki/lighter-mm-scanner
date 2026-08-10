import MarketsClient from "./MarketsClient";
import { getMarketsResult } from "@/lib/api";

export default async function MarketsPage() {
  const result = await getMarketsResult();

  if (!result.ok) {
    return (
      <section className="card">
        <h2>Failed to load markets.json</h2>
        <p>Dashboard data fetch failed.</p>
        <p className="muted" style={{ fontSize: "0.85rem" }}>
          {result.error}
          {result.status != null ? ` (HTTP ${result.status})` : ""}
        </p>
      </section>
    );
  }

  const markets = result.data.markets ?? [];
  return (
    <section className="card">
      <h2>All Markets</h2>
      <p className="muted">Search / sort / filter over aggregate scores (not raw Parquet).</p>
      {markets.length === 0 ? (
        <p className="muted">No markets in aggregate yet (collector has published an empty list).</p>
      ) : (
        <MarketsClient markets={markets} />
      )}
    </section>
  );
}
