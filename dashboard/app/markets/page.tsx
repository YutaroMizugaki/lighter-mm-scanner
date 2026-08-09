import MarketsClient from "./MarketsClient";
import { getMarkets } from "@/lib/data";

export default async function MarketsPage() {
  const markets = await getMarkets();
  return (
    <section className="card">
      <h2>All Markets</h2>
      <p className="muted">Search / sort / filter over aggregate scores (not raw Parquet).</p>
      <MarketsClient markets={markets} />
    </section>
  );
}
