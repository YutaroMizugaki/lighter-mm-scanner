const FACTORS = [
  { name: "取引の活発さ", weight: "15%" },
  { name: "約定シミュレーション", weight: "20%" },
  { name: "スプレッド", weight: "20%" },
  { name: "買い・売り両側の板の厚さ", weight: "15%" },
  { name: "約定後の価格変化", weight: "20%" },
  { name: "データ品質", weight: "10%" },
] as const;

export default function ScoreExplainer() {
  return (
    <details className="diagnostics methodology" id="methodology">
      <summary>スコアの見方・算出方法</summary>
      <div className="diagnostics-body">
        <p className="section-lead">6つの指標から市場を比較します。総合スコアは、元のスコアにデータの信頼度を掛けた値です。利益の予測ではありません。</p>
        <ul className="score-factors">
          {FACTORS.map((f) => <li key={f.name}><span>{f.name}</span><span className="tabular muted">{f.weight}</span></li>)}
        </ul>
        <div className="method-callout">
          <h3>約定の推定について</h3>
          <p>最良気配に置いた小さな指値注文が、成行取引の流れによって約定する機会を推定します。実際の順番待ちや通信遅延は再現していません。</p>
          <p className="muted">実際の約定確率や収益を保証するものではありません。</p>
        </div>
      </div>
    </details>
  );
}
