"""Scoring regression — multi-market fixture must stay behavior-identical."""

from __future__ import annotations

import pytest

from lighter_mm.scoring import CandidateThresholds, score_markets
from tests.helpers.estimated_fill import make_candidate_row


def _fixture_rows() -> list[dict]:
    return [
        make_candidate_row(
            symbol="HEALTHY",
            market_id=1,
            estimated_maker_fill_samples=500,
            estimated_maker_fill_rate_30s_conservative=0.45,
            estimated_maker_fill_sample_quality="reliable",
        ),
        make_candidate_row(
            symbol="INSUFF_FILL",
            market_id=2,
            estimated_maker_fill_samples=99,
            estimated_maker_fill_rate_30s_conservative=None,
            estimated_maker_fill_sample_quality="insufficient",
        ),
        make_candidate_row(
            symbol="ZERO_FILL",
            market_id=3,
            estimated_maker_fill_samples=500,
            estimated_maker_fill_rate_30s_conservative=0.0,
            estimated_maker_fill_sample_quality="reliable",
        ),
        make_candidate_row(
            symbol="NEG_MARKOUT",
            market_id=4,
            maker_markout_5s_median_bps=-2.0,
            maker_markout_30s_median_bps=-1.0,
            estimated_maker_fill_rate_30s_conservative=0.5,
        ),
        make_candidate_row(
            symbol="POOR_COV",
            market_id=5,
            data_coverage_pct=70.0,
            observation_coverage_pct=70.0,
        ),
        make_candidate_row(
            symbol="THIN_DEPTH",
            market_id=6,
            median_two_sided_depth_10bps_usd=100.0,
            median_two_sided_depth_5bps_usd=50.0,
        ),
        make_candidate_row(
            symbol="LOW_ACT",
            market_id=7,
            trades_per_minute_median=0.1,
            trades_per_minute_mean=0.1,
            total_trade_count=5,
        ),
    ]


def test_scoring_regression_multi_market_snapshot() -> None:
    scored = score_markets(_fixture_rows(), thresholds=CandidateThresholds())
    by = {s.row["symbol"]: s for s in scored}

    # healthy candidate
    healthy = by["HEALTHY"]
    assert healthy.candidate is True
    assert healthy.letter_rank == "C"
    assert healthy.penalties == []
    assert healthy.rank_components == {
        "trade_activity": pytest.approx(57.14285714285714),
        "estimated_maker_fill": pytest.approx(75.0),
        "spread": pytest.approx(50.0),
        "two_sided_depth": pytest.approx(57.14285714285714),
        "maker_markout": pytest.approx(57.14285714285714),
        "data_quality_persistence": pytest.approx(57.14285714285714),
    }
    assert healthy.score == pytest.approx(59.285714285714285)

    # insufficient Estimated Fill
    insuff = by["INSUFF_FILL"]
    assert insuff.candidate is False
    assert insuff.letter_rank == "C"
    assert insuff.rank_components["estimated_maker_fill"] is None
    assert insuff.score == pytest.approx(55.357142857142854)

    # measured zero fill — samples gate passes; zero fill adds penalty
    zero = by["ZERO_FILL"]
    assert zero.candidate is True
    assert zero.letter_rank == "D"
    assert any("Estimated Maker Fill ~0" in p for p in zero.penalties)
    assert zero.rank_components["estimated_maker_fill"] == pytest.approx(8.333333333333332)
    assert zero.score == pytest.approx(39.0595238095238)

    # negative 5s markout — penalty applied; still candidate at -2 (>= -5 gate)
    neg = by["NEG_MARKOUT"]
    assert neg.candidate is True
    assert neg.letter_rank == "D"
    assert any("median 5s maker markout < 0" in p for p in neg.penalties)
    assert neg.score == pytest.approx(36.83333333333333)

    # poor coverage
    poor = by["POOR_COV"]
    assert poor.candidate is False
    assert poor.letter_rank == "D"
    assert any("observation coverage" in p for p in poor.penalties)
    assert poor.score == pytest.approx(26.190476190476193)

    # thin depth
    thin = by["THIN_DEPTH"]
    assert thin.candidate is False
    assert thin.letter_rank == "D"
    assert any("two-sided depth very thin" in p for p in thin.penalties)
    assert thin.score == pytest.approx(33.839285714285715)

    # low activity
    low = by["LOW_ACT"]
    assert low.candidate is False
    assert low.letter_rank == "D"
    assert any("extremely low trade count" in p for p in low.penalties)
    assert low.score == pytest.approx(22.559523809523807)

    # Ranking order (scores descending)
    order = [s.row["symbol"] for s in scored]
    assert order == [
        "HEALTHY",
        "INSUFF_FILL",
        "ZERO_FILL",
        "NEG_MARKOUT",
        "THIN_DEPTH",
        "POOR_COV",
        "LOW_ACT",
    ]


def test_candidate_threshold_override_applies_to_gate_and_penalty() -> None:
    """Override min_estimated_maker_fill_samples must drive gate + zero-fill penalty."""
    row = make_candidate_row(
        estimated_maker_fill_samples=150,
        estimated_maker_fill_rate_30s_conservative=0.0,
        estimated_maker_fill_sample_quality="preliminary",
    )
    default = score_markets([row], thresholds=CandidateThresholds())[0]
    assert default.candidate is True
    assert any("Estimated Maker Fill ~0" in p for p in default.penalties)

    raised = score_markets(
        [row],
        thresholds=CandidateThresholds(min_estimated_maker_fill_samples=200),
    )[0]
    assert raised.candidate is False
    assert not any("Estimated Maker Fill ~0" in p for p in raised.penalties)
    assert any("sample insufficient (<200)" in w for w in raised.warnings)
