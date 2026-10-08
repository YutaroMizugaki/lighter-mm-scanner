import type { MarketRow } from "./types";

// Display priorities use the existing scorer's positive liquidity benchmarks.
// Once liquidity is adequate, preserve its spread/fill/markout assessment.
const ACTIVE_TRADES_PER_MINUTE = 5;
const SUFFICIENT_DEPTH_USD = 1_000;

// Premium, no staking discount: 0.4bp maker entry + 0.4bp maker exit.
// This is an editable scenario, not the user's account-specific fee.
export const DEFAULT_ROUND_TRIP_COST_BPS = 0.8;
export const ROUND_TRIP_COST_TOOLTIP =
  "往復手数料と想定スリッページなどの合計を入力します。初期値はPremium・割引なしの往復Maker手数料（0.4 + 0.4 = 0.8bp）。Maker→Takerは3.2bp、Standardの通常注文は手数料0bpです。口座・退出方法に合わせて調整してください。1bp = 0.01%。";

export function coversRoundTripCost(m: MarketRow, costBps: number | null): boolean {
  const spread = m.median_spread_bps;
  return costBps != null && Number.isFinite(costBps) && costBps >= 0
    && spread != null && Number.isFinite(spread) && spread > costBps;
}

export const HOME_RANKING_TOOLTIP =
  "総合スコアに流動性の補正を掛けた表示順です。取引回数は中央値5回/分、板の厚さは±10bp内の両側合計$1,000を基準に、不足する割合だけ順位を下げます。基準以上では流動性だけで加点しません。分析スコア自体は変更しません。";

function liquidityFactor(value: number | null, benchmark: number): number {
  return value != null && Number.isFinite(value) ? Math.min(1, Math.max(0, value) / benchmark) : 0;
}

export function homeOpportunityPriority(m: MarketRow): number {
  const score = m.effective_score ?? m.score;
  if (score == null || !Number.isFinite(score)) return -Infinity;
  return Math.max(0, score)
    * liquidityFactor(m.trades_per_minute_median, ACTIVE_TRADES_PER_MINUTE)
    * liquidityFactor(m.median_two_sided_depth_10bps_usd, SUFFICIENT_DEPTH_USD);
}
