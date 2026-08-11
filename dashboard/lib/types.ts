export type CollectorStatus = {
  run_id: string;
  status: string;
  started_at: string;
  ended_at?: string | null;
  generated_at: string;
  last_successful_sync: string | null;
  last_durable_event_at?: string | null;
  last_sync_attempt_at?: string | null;
  last_sync_error?: string | null;
  consecutive_sync_failures?: number;
  samples_written: number;
  trades_written: number;
  markouts_written: number;
  last_trade_at?: string | null;
  last_usable_book_sample_at?: string | null;
  last_book_row_at?: string | null;
  trades_without_reference_mid?: number;
  ws?: Overview["ws"];
  health_warnings?: string[];
  git_sha?: string | null;
  collector_version?: string | null;
};

export type AnalysisStatus = {
  status:
    | "NOT_STARTED"
    | "NO_ACTIVE_RUN"
    | "RUNNING"
    | "OK"
    | "DEGRADED"
    | "ERROR"
    | string;
  run_id: string | null;
  generated_at: string;
  started_at?: string | null;
  error?: string | null;
  last_successful_analysis_at?: string | null;
  duration_seconds?: number | null;
  git_sha?: string | null;
  analyzer_version?: string | null;
  start_ms?: number;
  end_ms?: number;
  analysis_end_ms?: number;
  durable_watermark_ms?: number;
  book_rows?: number;
  trade_rows?: number;
  markout_rows?: number;
  markets_analyzed?: number;
  candidates?: number;
  valid_parquet_files?: number;
  corrupt_parquet_files?: number;
  skipped_files?: Array<{ path: string; error: string }>;
  parquet_health_status?: string;
  analysis_window_hours?: number | null;
  run_observation_hours?: number | null;
};

export type DashboardGeneration = {
  analysis_id: string;
  generated_at: string;
};

export type Overview = {
  title: string;
  status: string;
  run_id: string | null;
  started_at: string | null;
  observation_hours: number | null;
  run_observation_hours?: number | null;
  analysis_scope?: string | null;
  analysis_window_hours?: number | null;
  observation_target_hours: number | null;
  markets: number;
  markets_analyzed?: number;
  markets_discovered?: number;
  candidates: number;
  coverage_pct: number | null;
  last_update: string | null;
  last_data_at?: string | null;
  last_successful_sync?: string | null;
  last_successful_flush?: string | null;
  last_trade_at?: string | null;
  last_book_sample_at?: string | null;
  last_usable_book_sample_at?: string | null;
  last_book_row_at?: string | null;
  trades_without_reference_mid?: number;
  git_sha: string | null;
  collector_version: string | null;
  analysis_error?: string | null;
  health_warnings?: string[];
  samples_written?: number;
  ws?: {
    connected_shards?: number;
    total_shards?: number;
    planned_channels?: number;
    acked_channels?: number;
    subscribed_channels?: number;
    dropped_connections?: number;
    subscription_errors?: number;
    trade_parse_errors?: number;
    book_resyncs?: number;
    nonce_gaps?: number;
    last_ws_error?: string | null;
  } | null;
  top_candidate: {
    symbol: string;
    score: number;
    median_spread_bps: number | null;
    maker_markout_5s_median_bps: number | null;
    maker_markout_30s_median_bps: number | null;
    letter_rank?: string;
  } | null;
  storage_estimate?: Record<string, unknown> | null;
  generated_at: string;
  disclaimer: string;
};

export type EstimatedFillSideRates = {
  optimistic?: number | null;
  conservative?: number | null;
  bid_optimistic?: number | null;
  bid_conservative?: number | null;
  ask_optimistic?: number | null;
  ask_conservative?: number | null;
};

export type Stage1Summary = {
  eligible: boolean;
  screening_score: number | null;
  observation_coverage: number;
  trade_count: number;
  median_spread_bps: number | null;
  book_observation_count?: number;
  book_update_count?: number;
  trade_volume?: number;
  mean_spread_bps?: number | null;
  p25_spread_bps?: number | null;
  p75_spread_bps?: number | null;
};

export type MarketRow = {
  symbol: string;
  market_id: number;
  score: number | null;
  raw_score?: number;
  confidence?: number;
  effective_score?: number;
  confidence_label?: string | null;
  confidence_reasons?: string[];
  confidence_breakdown?: Record<string, number | null> | null;
  letter_rank: string | null;
  analysis_stage?: "full" | "screened";
  stage1?: Stage1Summary | null;
  is_candidate: boolean;
  median_spread_bps: number | null;
  pct_time_spread_ge_5bps: number | null;
  median_two_sided_depth_10bps_usd: number | null;
  trades_per_minute_median: number | null;
  trades_per_minute_mean: number | null;
  total_trade_count: number | null;
  maker_markout_5s_median_bps: number | null;
  maker_markout_30s_median_bps: number | null;
  markout_sample_quality?: string | null;
  estimated_maker_fill_rate_5s_conservative?: number | null;
  estimated_maker_fill_rate_30s_conservative?: number | null;
  estimated_maker_fill_rate_5s_optimistic?: number | null;
  estimated_maker_fill_rate_30s_optimistic?: number | null;
  estimated_maker_fill_samples?: number | null;
  estimated_maker_fill_sample_quality?: string | null;
  estimated_maker_edge_5s_bps?: number | null;
  estimated_maker_edge_30s_bps?: number | null;
  estimated_maker_edge_fee_included?: boolean | null;
  estimated_maker_fill_by_size?: Record<
    string,
    Record<string, EstimatedFillSideRates>
  > | null;
  estimated_maker_fill_order_usd_default?: number | null;
  paper_mm_total_pnl_usd?: number | null;
  paper_mm_round_trips?: number | null;
  paper_mm_filled_notional_usd?: number | null;
  paper_mm_pnl_per_hour_usd?: number | null;
  paper_mm_pnl_bps_on_filled_notional?: number | null;
  paper_mm_max_abs_inventory_usd?: number | null;
  paper_mm_time_with_inventory_pct?: number | null;
  paper_mm_median_holding_seconds?: number | null;
  paper_mm_markout_5s_median_bps?: number | null;
  paper_mm_markout_30s_median_bps?: number | null;
  paper_mm_status?: string | null;
  paper_mm_order_usd?: number | null;
  paper_mm_queue_model?: string | null;
  paper_mm_quote_count?: number | null;
  paper_mm_bid_fills?: number | null;
  paper_mm_ask_fills?: number | null;
  paper_mm_partial_fills?: number | null;
  paper_mm_full_fills?: number | null;
  paper_mm_gross_pnl_usd?: number | null;
  paper_mm_realized_pnl_usd?: number | null;
  paper_mm_unrealized_pnl_usd?: number | null;
  paper_mm_fees_usd?: number | null;
  paper_mm_p90_holding_seconds?: number | null;
  paper_mm_max_holding_seconds?: number | null;
  paper_mm_markout_5s_count?: number | null;
  paper_mm_markout_30s_count?: number | null;
  paper_mm_final_inventory_usd?: number | null;
  paper_mm_fee_included?: boolean | null;
  paper_mm_samples?: number | null;
  paper_mm_gross_spread_capture_usd?: number | null;
  analysis_scope?: string | null;
  analysis_window_hours?: number | null;
  current_funding_rate: number | null;
  data_coverage_pct: number | null;
  observation_coverage_pct?: number | null;
  usable_quote_coverage_pct?: number | null;
  spread_coverage_pct?: number | null;
  warnings?: string[];
  pros?: string[];
  cons?: string[];
};

export type FetchResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; status?: number };

export type DashboardBundle = {
  prefix: string;
  legacy: boolean;
};
