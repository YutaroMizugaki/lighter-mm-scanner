export const ESTIMATED_FILL_TOOLTIP =
  "公開板と公開約定データから、Best Bid / Ask に仮想 Maker 注文を置いた場合の約定機会を推定した指標です。実際の queue position、自身の注文履歴、cancel latency は含まれないため、実約定率ではありません。ランキング基準サイズは $50（conservative / 30s）です。sample <100 は Insufficient（0% ではありません）。";

export const ESTIMATED_EDGE_TOOLTIP =
  "Estimated Maker Edge = Estimated Fill × (Maker Markout − Maker Fee)。期待利益ではありません。Maker Markout は約定価格起点のため half-spread は加算しません。Maker Fee 未取得時は fee 控除前（fee excluded）です。";
