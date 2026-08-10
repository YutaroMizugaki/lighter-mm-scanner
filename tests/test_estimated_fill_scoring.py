"""Gate C — Estimated Maker Fill scoring integration."""

from __future__ import annotations

from lighter_mm.scoring import ScoreWeights, score_markets
from tests.helpers.estimated_fill import make_candidate_row


def _base_row(symbol: str, market_id: int, **overrides: object) -> dict:
    return make_candidate_row(symbol=symbol, market_id=market_id, **overrides)


def test_score_weights_sum_to_100() -> None:
    w = ScoreWeights()
    total = (
        w.trade_activity
        + w.estimated_maker_fill
        + w.spread
        + w.two_sided_depth
        + w.maker_markout
        + w.data_quality_persistence
    )
    assert total == 100.0
    assert w.trade_activity == 15.0
    assert w.estimated_maker_fill == 20.0


def test_case_a_high_activity_low_fill_ranks_lower() -> None:
    """High TPM + low Estimated Fill should not dominate vs balanced peer."""
    rows = [
        _base_row(
            "ACTIVE_LOW_FILL",
            1,
            trades_per_minute_median=20.0,
            trades_per_minute_mean=20.0,
            estimated_maker_fill_rate_30s_conservative=0.02,
        ),
        _base_row(
            "BALANCED",
            2,
            trades_per_minute_median=6.0,
            trades_per_minute_mean=6.0,
            estimated_maker_fill_rate_30s_conservative=0.55,
        ),
    ]
    scored = score_markets(rows)
    by_sym = {s.row["symbol"]: s for s in scored}
    assert by_sym["BALANCED"].score > by_sym["ACTIVE_LOW_FILL"].score


def test_case_b_moderate_activity_high_fill_improves_rank() -> None:
    rows = [
        _base_row(
            "MOD_HIGH_FILL",
            1,
            trades_per_minute_median=4.0,
            trades_per_minute_mean=4.0,
            estimated_maker_fill_rate_30s_conservative=0.7,
            maker_markout_5s_median_bps=0.8,
            maker_markout_30s_median_bps=0.3,
        ),
        _base_row(
            "HIGH_ACT_MED_FILL",
            2,
            trades_per_minute_median=12.0,
            trades_per_minute_mean=12.0,
            estimated_maker_fill_rate_30s_conservative=0.25,
            maker_markout_5s_median_bps=0.8,
            maker_markout_30s_median_bps=0.3,
        ),
    ]
    scored = score_markets(rows)
    by_sym = {s.row["symbol"]: s for s in scored}
    assert by_sym["MOD_HIGH_FILL"].score > by_sym["HIGH_ACT_MED_FILL"].score


def test_case_c_high_fill_bad_markout_not_top() -> None:
    rows = [
        _base_row(
            "FILL_TOXIC",
            1,
            estimated_maker_fill_rate_30s_conservative=0.9,
            maker_markout_5s_median_bps=-8.0,
            maker_markout_30s_median_bps=-20.0,
        ),
        _base_row(
            "HEALTHY",
            2,
            estimated_maker_fill_rate_30s_conservative=0.45,
            maker_markout_5s_median_bps=1.0,
            maker_markout_30s_median_bps=0.5,
        ),
    ]
    scored = score_markets(rows)
    assert scored[0].row["symbol"] == "HEALTHY"
    assert scored[0].score > scored[1].score


def test_case_d_wide_spread_near_zero_fill_not_top() -> None:
    rows = [
        _base_row(
            "WIDE_NO_FILL",
            1,
            median_spread_bps=18.0,
            pct_time_spread_ge_5bps=0.9,
            estimated_maker_fill_rate_30s_conservative=0.0,
            estimated_maker_fill_samples=500,
        ),
        _base_row(
            "NORMAL",
            2,
            median_spread_bps=4.0,
            estimated_maker_fill_rate_30s_conservative=0.5,
        ),
    ]
    scored = score_markets(rows)
    assert scored[0].row["symbol"] == "NORMAL"


def test_missing_estimated_fill_not_forced_to_bottom() -> None:
    """Insufficient-sample (null) fill must not be treated as 0th percentile."""
    rows = [
        _base_row(
            "NO_FILL_DATA",
            1,
            estimated_maker_fill_rate_30s_conservative=None,
            estimated_maker_fill_samples=20,
            estimated_maker_fill_sample_quality="insufficient",
            trades_per_minute_median=8.0,
            trades_per_minute_mean=8.0,
            maker_markout_5s_median_bps=1.0,
        ),
        _base_row(
            "ZERO_FILL",
            2,
            estimated_maker_fill_rate_30s_conservative=0.0,
            estimated_maker_fill_samples=500,
            estimated_maker_fill_sample_quality="reliable",
            trades_per_minute_median=8.0,
            trades_per_minute_mean=8.0,
            maker_markout_5s_median_bps=1.0,
        ),
    ]
    scored = score_markets(rows)
    by_sym = {s.row["symbol"]: s for s in scored}
    assert by_sym["NO_FILL_DATA"].rank_components["estimated_maker_fill"] is None
    assert by_sym["NO_FILL_DATA"].score > by_sym["ZERO_FILL"].score
