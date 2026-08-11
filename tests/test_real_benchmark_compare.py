"""Unit tests for real-data A/B benchmark comparison logic."""

from __future__ import annotations

from lighter_mm.analytics.real_benchmark import (
    RESULT_FAIL,
    RESULT_PASS,
    RESULT_PASS_WITH_WARNINGS,
    compare_snapshots,
    values_match,
)


def _market(
    mid: int,
    *,
    spread: float = 2.0,
    score: float | None = 10.0,
) -> dict:
    return {
        "median_spread_bps": spread,
        "median_two_sided_depth_10bps_usd": 400.0,
        "trades_per_minute_mean": 1.5,
        "trades_per_minute_median": 1.0,
        "pct_time_spread_ge_5bps": 0.5,
        "data_coverage_pct": 95.0,
        "observation_coverage_pct": 95.0,
        "maker_markout_5s_median_bps": 1.0,
        "maker_markout_30s_median_bps": 0.5,
        "markout_5s_count": 25,
        "markout_30s_count": 25,
        "estimated_maker_fill_rate_5s_conservative": 0.1,
        "estimated_maker_fill_rate_30s_conservative": 0.15,
        "estimated_maker_fill_rate_5s_optimistic": 0.2,
        "estimated_maker_fill_rate_30s_optimistic": 0.25,
        "estimated_maker_fill_samples": 100,
        "score": score,
    }


def _base_snapshot(mode: str, markets: dict[int, dict]) -> dict:
    return {
        "mode": mode,
        "start_ms": 1_000_000,
        "end_ms": 2_000_000,
        "hours": 0.278,
        "elapsed_seconds": 500.0 if mode == "legacy" else 300.0,
        "peak_rss_mb": 3000.0 if mode == "legacy" else 1500.0,
        "book_row_count": 1000,
        "trade_row_count": 500,
        "markout_row_count": 800,
        "latest_book_event_ms": 1_999_000,
        "markets_scored": len(markets),
        "markets_listed": len(markets),
        "candidates": 1,
        "error": None,
        "benchmark_profile": {},
        "markets": markets,
        "two_stage": {
            "stage1_elapsed_seconds": 50.0,
            "stage2_elapsed_seconds": 250.0,
            "markets_total": len(markets),
            "markets_eligible": len(markets),
            "markets_selected": len(markets),
            "markets_full_analyzed": len(markets),
            "stage2_selection_ratio": 1.0,
        },
    }


def test_values_match_identical_and_tolerance() -> None:
    assert values_match(None, None, "median_spread_bps")
    assert values_match(1.0, 1.0, "median_spread_bps")
    assert values_match(1.0, 1.0 + 1e-12, "median_spread_bps")
    assert not values_match(None, 1.0, "median_spread_bps")
    assert not values_match(1.0, None, "median_spread_bps")
    assert not values_match(1.0, 2.0, "median_spread_bps")
    assert values_match(10, 10, "markout_5s_count")
    assert not values_match(10, 11, "markout_5s_count")


def test_compare_identical_snapshots_pass() -> None:
    markets = {1: _market(1), 2: _market(2, spread=3.0, score=8.0)}
    legacy = _base_snapshot("legacy", markets)
    two_stage = _base_snapshot("two-stage", markets)
    cmp = compare_snapshots(legacy, two_stage)
    assert cmp["result"] == RESULT_PASS
    assert cmp["correctness"]["row_counts_match"]
    assert cmp["correctness"]["raw_metric_mismatch_count"] == 0


def test_compare_raw_metric_mismatch_fails() -> None:
    markets_ok = {1: _market(1)}
    markets_bad = {1: _market(1, spread=99.0)}
    legacy = _base_snapshot("legacy", markets_ok)
    two_stage = _base_snapshot("two-stage", markets_bad)
    cmp = compare_snapshots(legacy, two_stage)
    assert cmp["result"] == RESULT_FAIL
    assert cmp["correctness"]["raw_metric_mismatch_count"] > 0


def test_compare_row_count_mismatch_fails() -> None:
    markets = {1: _market(1)}
    legacy = _base_snapshot("legacy", markets)
    two_stage = _base_snapshot("two-stage", markets)
    two_stage["book_row_count"] = 999
    cmp = compare_snapshots(legacy, two_stage)
    assert cmp["result"] == RESULT_FAIL
    assert not cmp["correctness"]["row_counts_match"]


def test_score_delta_only_warns_not_raw_fail() -> None:
    markets_legacy = {1: _market(1, score=50.0)}
    markets_two = {1: _market(1, score=10.0)}
    legacy = _base_snapshot("legacy", markets_legacy)
    two_stage = _base_snapshot("two-stage", markets_two)
    cmp = compare_snapshots(legacy, two_stage)
    assert cmp["correctness"]["raw_metric_mismatch_count"] == 0
    assert cmp["result"] in (RESULT_PASS, RESULT_PASS_WITH_WARNINGS)
    assert cmp["result"] != RESULT_FAIL


def test_performance_warning_without_fail() -> None:
    markets = {1: _market(1)}
    legacy = _base_snapshot("legacy", markets)
    two_stage = _base_snapshot("two-stage", markets)
    legacy["elapsed_seconds"] = 100.0
    legacy["peak_rss_mb"] = 1000.0
    two_stage["elapsed_seconds"] = 95.0
    two_stage["peak_rss_mb"] = 900.0
    cmp = compare_snapshots(
        legacy,
        two_stage,
        min_rss_reduction_pct=30.0,
        min_elapsed_reduction_pct=20.0,
    )
    assert cmp["correctness"]["raw_metric_mismatch_count"] == 0
    assert cmp["result"] == RESULT_PASS_WITH_WARNINGS


def test_analyzer_error_fails() -> None:
    markets = {1: _market(1)}
    legacy = _base_snapshot("legacy", markets)
    two_stage = _base_snapshot("two-stage", markets)
    legacy["error"] = "boom"
    cmp = compare_snapshots(legacy, two_stage)
    assert cmp["result"] == RESULT_FAIL


def test_compare_zero_book_rows_fails() -> None:
    markets = {1: _market(1)}
    legacy = _base_snapshot("legacy", markets)
    two_stage = _base_snapshot("two-stage", markets)
    legacy["book_row_count"] = 0
    cmp = compare_snapshots(legacy, two_stage)
    assert cmp["result"] == RESULT_FAIL
    assert any("legacy book_row_count is 0" in f for f in cmp["hard_failures"])


def test_compare_zero_stage2_markets_fails() -> None:
    markets = {1: _market(1)}
    legacy = _base_snapshot("legacy", markets)
    two_stage = _base_snapshot("two-stage", {})
    two_stage["markets"] = {}
    two_stage["markets_scored"] = 0
    two_stage["two_stage"]["markets_selected"] = 0
    two_stage["two_stage"]["markets_full_analyzed"] = 0
    cmp = compare_snapshots(legacy, two_stage)
    assert cmp["result"] == RESULT_FAIL
    assert any("no two-stage markets to compare" in f for f in cmp["hard_failures"])


def test_compare_selected_but_not_analyzed_fails() -> None:
    markets = {1: _market(1)}
    legacy = _base_snapshot("legacy", markets)
    two_stage = _base_snapshot("two-stage", {})
    two_stage["markets"] = {}
    two_stage["markets_scored"] = 0
    two_stage["two_stage"]["markets_selected"] = 1
    two_stage["two_stage"]["markets_full_analyzed"] = 0
    cmp = compare_snapshots(legacy, two_stage)
    assert cmp["result"] == RESULT_FAIL
    assert any("markets_selected (1) != markets_full_analyzed (0)" in f for f in cmp["hard_failures"])


def test_compare_output_market_count_mismatch_fails() -> None:
    markets = {1: _market(1), 2: _market(2, spread=3.0, score=8.0)}
    legacy = _base_snapshot("legacy", markets)
    two_stage = _base_snapshot("two-stage", {1: markets[1]})
    two_stage["markets_scored"] = 1
    two_stage["two_stage"]["markets_selected"] = 2
    two_stage["two_stage"]["markets_full_analyzed"] = 2
    cmp = compare_snapshots(legacy, two_stage)
    assert cmp["result"] == RESULT_FAIL
    assert any(
        "markets_full_analyzed (2) != two_stage output markets (1)" in f
        for f in cmp["hard_failures"]
    )
