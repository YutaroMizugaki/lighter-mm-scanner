import PublicErrorState from "@/components/PublicErrorState";
import { getMarketsResult } from "@/lib/api";
import { publicDataUnavailableMessage } from "@/lib/public";
import MarketsClient from "./MarketsClient";

export default async function MarketsPage() {
  const result = await getMarketsResult();

  if (!result.ok) {
    const msg = publicDataUnavailableMessage("markets");
    return <PublicErrorState title={msg.title} body={msg.body} />;
  }

  const markets = result.data.markets ?? [];
  return (
    <section className="panel">
      <div className="section-header">
        <h1 style={{ margin: 0, fontSize: "1.35rem" }}>Markets</h1>
        <p className="section-lead">
          Search, sort, and compare research rankings across Lighter markets. Depth and activity
          are shown alongside Estimated Fill so you can separate liquidity from fill estimates.
        </p>
      </div>
      {markets.length === 0 ? (
        <p className="muted">No markets are available in the latest analysis yet.</p>
      ) : (
        <MarketsClient markets={markets} />
      )}
    </section>
  );
}
