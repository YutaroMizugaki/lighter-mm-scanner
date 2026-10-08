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
        <h1 style={{ margin: 0, fontSize: "1.35rem" }}>市場一覧</h1>
        <p className="section-lead">
          Lighterの市場を検索・並べ替えできます。流動性や取引量と、約定シミュレーション値を分けて確認できます。
        </p>
      </div>
      {markets.length === 0 ? (
        <p className="muted">最新の分析に表示できる市場はありません。</p>
      ) : (
        <MarketsClient markets={markets} />
      )}
    </section>
  );
}
