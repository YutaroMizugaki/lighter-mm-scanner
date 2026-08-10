"""Dashboard payload health: coverage, TPM fields, ws block, markout inconsistency."""

from __future__ import annotations

from lighter_mm.cloud.dashboard_data import _market_row, build_dashboard_payload
from lighter_mm.config import Settings
from lighter_mm.scoring import ScoredMarket
from lighter_mm.storage.state import RunState, now_iso


def test_market_row_includes_tpm_mean_and_trade_count() -> None:
    s = ScoredMarket(
        row={
            "symbol": "ETH",
            "market_id": 1,
            "trades_per_minute_median": 0.0,
            "trades_per_minute_mean": 0.42,
            "total_trade_count": 120,
            "maker_markout_5s_median_bps": 1.0,
            "maker_markout_30s_median_bps": 0.5,
            "median_spread_bps": 5.0,
            "pct_time_spread_ge_5bps": 0.5,
            "median_two_sided_depth_10bps_usd": 500.0,
            "current_funding_rate": 0.0,
            "data_coverage_pct": 95.0,
        },
        score=80.0,
        rank_components={},
        penalties=[],
        pros=["ok"],
        cons=[],
        warnings=[],
        candidate=True,
        letter_rank="A",
        recommended_max_order_usd=100.0,
        size_fit={},
    )
    row = _market_row(s)
    assert row["trades_per_minute_mean"] == 0.42
    assert row["total_trade_count"] == 120


def test_market_row_estimated_fill_fields_optional_compat() -> None:
    """Old generations without Estimated Fill fields still serialize cleanly."""
    legacy = ScoredMarket(
        row={
            "symbol": "OLD",
            "market_id": 3,
            "trades_per_minute_median": 1.0,
            "trades_per_minute_mean": 1.0,
            "total_trade_count": 10,
            "maker_markout_5s_median_bps": 0.0,
            "maker_markout_30s_median_bps": 0.0,
            "median_spread_bps": 2.0,
            "pct_time_spread_ge_5bps": 0.1,
            "median_two_sided_depth_10bps_usd": 300.0,
            "current_funding_rate": None,
            "data_coverage_pct": 90.0,
        },
        score=40.0,
        rank_components={},
        penalties=[],
        pros=[],
        cons=[],
        warnings=[],
        candidate=False,
        letter_rank="C",
        recommended_max_order_usd=None,
        size_fit={},
    )
    legacy_row = _market_row(legacy)
    assert legacy_row["estimated_maker_fill_rate_30s_conservative"] is None
    assert legacy_row["estimated_maker_fill_sample_quality"] is None

    modern = ScoredMarket(
        row={
            **legacy.row,
            "symbol": "NEW",
            "estimated_maker_fill_rate_5s_conservative": 0.1,
            "estimated_maker_fill_rate_30s_conservative": 0.25,
            "estimated_maker_fill_rate_5s_optimistic": 0.2,
            "estimated_maker_fill_rate_30s_optimistic": 0.4,
            "estimated_maker_fill_samples": 500,
            "estimated_maker_fill_sample_quality": "reliable",
            "estimated_maker_edge_5s_bps": 0.5,
            "estimated_maker_edge_30s_bps": 1.0,
            "markout_sample_quality": "preliminary",
            "analysis_scope": "rolling",
            "estimated_maker_fill_by_size": {"50": {"30s": {"conservative": 0.25}}},
        },
        score=70.0,
        rank_components={"estimated_maker_fill": 80.0},
        penalties=[],
        pros=[],
        cons=[],
        warnings=[],
        candidate=True,
        letter_rank="B",
        recommended_max_order_usd=50.0,
        size_fit={},
    )
    from lighter_mm.cloud.dashboard_data import _market_detail

    detail = _market_detail(modern)
    assert detail["estimated_maker_fill_rate_30s_conservative"] == 0.25
    assert detail["estimated_maker_fill_by_size"]["50"]["30s"]["conservative"] == 0.25
    assert detail["estimated_maker_fill_order_usd_default"] == 50


def test_markout_without_trades_adds_warning() -> None:
    s = ScoredMarket(
        row={
            "symbol": "X",
            "market_id": 2,
            "trades_per_minute_median": 0.0,
            "trades_per_minute_mean": 0.0,
            "total_trade_count": 0,
            "maker_markout_5s_median_bps": 1.2,
            "maker_markout_30s_median_bps": None,
            "median_spread_bps": 3.0,
            "pct_time_spread_ge_5bps": 0.1,
            "median_two_sided_depth_10bps_usd": 100.0,
            "current_funding_rate": None,
            "data_coverage_pct": 50.0,
        },
        score=10.0,
        rank_components={},
        penalties=[],
        pros=[],
        cons=[],
        warnings=[],
        candidate=False,
        letter_rank="D",
        recommended_max_order_usd=None,
        size_fit={},
    )
    row = _market_row(s)
    assert any("trade aggregation inconsistency" in w for w in row["warnings"])


def test_payload_includes_flush_and_ws(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", reports_dir=tmp_path / "reports")
    (settings.data_dir / "book_samples").mkdir(parents=True)
    state = RunState(
        run_id="r1",
        started_at=now_iso(),
        status="running",
        last_successful_flush=now_iso(),
        last_durable_event_ms=1_700_000_000_500,
        samples_written=50,
        markets=[1],
        last_trade_timestamp_ms=1_700_000_000_000,
    )
    ws = {
        "connected_shards": 5,
        "total_shards": 5,
        "subscribed_channels": 415,
        "dropped_connections": 0,
        "subscription_errors": 0,
        "trade_parse_errors": 0,
    }
    payload = build_dashboard_payload(
        settings,
        hours=1,
        state=state,
        ws_runtime=ws,
        last_book_sample_at_ms=1_700_000_000_500,
    )
    latest = payload["latest"]
    assert latest["last_successful_flush"] == state.last_successful_flush
    assert latest["last_update"] != state.last_successful_flush
    assert latest["last_successful_sync"] == state.last_successful_flush
    assert latest["ws"] == ws
