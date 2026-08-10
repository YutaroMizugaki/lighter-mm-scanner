/** Barrel re-exports — prefer importing from the split modules directly. */

export type {
  AnalysisStatus,
  CollectorStatus,
  DashboardBundle,
  DashboardGeneration,
  EstimatedFillSideRates,
  FetchResult,
  MarketRow,
  Overview,
} from "./types";

export {
  fetchJson,
  getAnalysisStatusResult,
  getCandidatesResult,
  getCollectorStatusResult,
  getMarket,
  getMarkets,
  getMarketsResult,
  getOverview,
  getOverviewResult,
  resolveDashboardBundle,
} from "./api";

export {
  fmt,
  fmtEstimatedFill,
  fmtJst,
  fmtPctFraction,
  fmtSampleQuality,
} from "./format";

export { ESTIMATED_EDGE_TOOLTIP, ESTIMATED_FILL_TOOLTIP } from "./marketMetrics";

export {
  analysisDisplayTimestamp,
  effectiveAnalysisStatus,
  effectiveCollectorStatus,
  effectivePublicAnalysisStatus,
  effectiveStatus,
  overviewAnalysisTimestamp,
  statusHealthNote,
} from "./status";
