"""Score Confidence layer — unit tests (Cases 1–23)."""

from __future__ import annotations

import math

import pytest

from lighter_mm.confidence import (
    ConfidenceConfig,
    _overall_confidence,
    compute_market_confidence,
    coverage_confidence_from_pct,
    duration_confidence_from_hours,
    sample_confidence,
    scored_market_sort_key,
)
from lighter_mm.paper_mm import resolve_paper_mm_targets
from lighter_mm.scoring import ScoredMarket, score_markets
from tests.helpers.estimated_fill import make_candidate_row

EPS = 0.05
CFG = ConfidenceConfig()


def _full_row(**overrides: object) -> dict:
    row = {
        "symbol": "FULL",
        "markout_5s_count": 10_000_000,
        "markout_30s_count": 10_000_000,
        "estimated_maker_fill_samples": 10_000_000,
        "total_trade_count": 10_000_000,
        "data_coverage_pct": 99.5,
        "observation_coverage_pct": 99.5,
        "observation_hours": 500.0,
    }
    row.update(overrides)
    return row


def _all_ones_present(**overrides: float) -> dict[str, float]:
    present = {
        "markout_5s": 1.0,
        "markout_30s": 1.0,
        "fill": 1.0,
        "coverage": 1.0,
        "trade": 1.0,
        "duration": 1.0,
    }
    present.update(overrides)
    return present


def _overall_from_row(row: dict) -> float:
    return compute_market_confidence(row, 100.0).confidence


def _expect_eps_power(weight: float) -> float:
    return EPS ** weight


def _scored_market(
    symbol: str,
    raw_score: float,
    effective_score: float,
    *,
    market_id: int = 1,
    candidate: bool = True,
) -> ScoredMarket:
    confidence = effective_score / raw_score if raw_score > 0 else 0.0
    return ScoredMarket(
        row={"symbol": symbol, "market_id": market_id},
        score=raw_score,
        raw_score=raw_score,
        effective_score=effective_score,
        confidence=confidence,
        rank_components={},
        candidate=candidate,
    )


# --- Case 1: sample reliability ---
def test_case1_sample_reliability_ranking() -> None:
    high = make_candidate_row(
        symbol="HIGH",
        markout_5s_count=2000,
        markout_30s_count=2000,
    )
    low = make_candidate_row(
        symbol="LOW",
        markout_5s_count=20,
        markout_30s_count=20,
    )
    scored = score_markets([low, high])
    by_sym = {s.row["symbol"]: s for s in scored}
    assert by_sym["HIGH"].confidence > by_sym["LOW"].confidence
    assert by_sym["HIGH"].effective_score > by_sym["LOW"].effective_score


# --- Case 2: effective_score ranking via sort key ---
def test_case2_effective_prefers_confidence_via_sort_key() -> None:
    high_raw_low_eff = _scored_market("A", raw_score=90.0, effective_score=18.0, market_id=1)
    low_raw_high_eff = _scored_market("B", raw_score=70.0, effective_score=63.0, market_id=2)
    ordered = sorted(
        [high_raw_low_eff, low_raw_high_eff],
        key=scored_market_sort_key,
    )
    assert [s.row["symbol"] for s in ordered] == ["B", "A"]


def test_scored_market_sort_key_tie_breaks_on_raw_score() -> None:
    higher_raw = _scored_market("A", raw_score=90.0, effective_score=50.0, market_id=1)
    lower_raw = _scored_market("B", raw_score=80.0, effective_score=50.0, market_id=2)
    ordered = sorted([lower_raw, higher_raw], key=scored_market_sort_key)
    assert ordered[0].row["symbol"] == "A"


def test_scored_market_sort_key_tie_breaks_on_symbol() -> None:
    aaa = _scored_market("AAA", raw_score=80.0, effective_score=50.0, market_id=1)
    bbb = _scored_market("BBB", raw_score=80.0, effective_score=50.0, market_id=2)
    ordered = sorted([bbb, aaa], key=scored_market_sort_key)
    assert ordered[0].row["symbol"] == "AAA"


def test_score_markets_orders_by_effective_score() -> None:
    low = make_candidate_row(
        symbol="LOW",
        markout_5s_count=20,
        markout_30s_count=20,
    )
    high = make_candidate_row(
        symbol="HIGH",
        markout_5s_count=2000,
        markout_30s_count=2000,
    )
    scored = score_markets([low, high])
    assert scored[0].row["symbol"] == "HIGH"
    assert scored[0].effective_score >= scored[1].effective_score


def test_resolve_paper_mm_targets_uses_effective_top_n() -> None:
    scored = [
        _scored_market("A", raw_score=95.0, effective_score=20.0, market_id=1),
        _scored_market("B", raw_score=80.0, effective_score=75.0, market_id=2),
        _scored_market("C", raw_score=70.0, effective_score=65.0, market_id=3),
    ]
    scored.sort(key=scored_market_sort_key)
    targets = resolve_paper_mm_targets(scored, top_n=2)
    assert [t.row["symbol"] for t in targets] == ["B", "C"]


# --- Case 3: coverage monotonicity ---
def test_case3_coverage_monotonicity() -> None:
    vals = [99.7, 95.0, 90.0, 85.0]
    confs = [coverage_confidence_from_pct(v) for v in vals]
    assert confs == sorted(confs, reverse=True)


# --- Case 4: sufficient data ---
def test_case4_full_data_high_confidence() -> None:
    res = compute_market_confidence(_full_row(), 82.0)
    assert res.confidence >= 0.90
    assert abs(res.effective_score - 82.0) < 5.0


# --- Case 5: tiny samples ---
def test_case5_tiny_samples_no_crash() -> None:
    for n in (0, 1, 5):
        row = _full_row(
            markout_5s_count=n,
            markout_30s_count=n,
            estimated_maker_fill_samples=n,
            total_trade_count=n,
        )
        res = compute_market_confidence(row, 50.0)
        assert 0 <= res.confidence <= 1
        assert not math.isnan(res.confidence)
        assert res.confidence < 0.5


# --- Case 6: zero vs missing ---
def test_case6_zero_vs_missing_fill() -> None:
    zero = compute_market_confidence(
        _full_row(estimated_maker_fill_samples=0),
        80.0,
    )
    missing = compute_market_confidence(
        _full_row(estimated_maker_fill_samples=None),
        80.0,
    )
    assert zero.confidence_breakdown["estimated_fill"] == 0.0
    assert missing.confidence_breakdown["estimated_fill"] is None
    assert zero.confidence < missing.confidence


# --- Case 7: fill missing completeness ---
def test_case7_fill_missing_not_full_confidence() -> None:
    res = _overall_confidence(
        {
            "markout_5s": 1.0,
            "markout_30s": 1.0,
            "coverage": 1.0,
            "trade": 1.0,
            "duration": 1.0,
        },
        CFG,
    )
    assert res < 1.0
    assert abs(res - 0.75) < 0.02


# --- Case 8: all missing ---
def test_case8_all_missing_confidence_zero() -> None:
    res = compute_market_confidence({}, 80.0)
    assert res.confidence == 0.0
    assert not math.isnan(res.confidence)


# --- Case 9: sample monotonicity ---
def test_case9_sample_monotonicity() -> None:
    ns = [0, 1, 5, 20, 100, 500, 1000, 5000]
    confs = [sample_confidence(n, 200) for n in ns]
    for i in range(len(confs) - 1):
        assert confs[i + 1] >= confs[i]


# --- Case 10: duration monotonicity ---
def test_case10_duration_monotonicity() -> None:
    hours = [2, 12, 24, 48, 72, 168]
    confs = [duration_confidence_from_hours(h, 24) for h in hours]
    for i in range(len(confs) - 1):
        assert confs[i + 1] >= confs[i]
    assert confs[-2] >= 0.90


# --- Case 11: coverage interpolation no jump ---
def test_case11_coverage_interpolation_continuous() -> None:
    pts = [79.9, 80.0, 80.1, 89.9, 90.0, 90.1, 94.9, 95.0, 95.1, 98.9, 99.0, 99.1]
    confs = [coverage_confidence_from_pct(p) for p in pts]
    for i in range(len(confs) - 1):
        assert confs[i + 1] >= confs[i]


# --- Case 12: score regression raw unchanged ---
def test_case12_raw_score_equals_score() -> None:
    rows = [
        make_candidate_row(symbol="HEALTHY", estimated_maker_fill_samples=500),
        make_candidate_row(
            symbol="LOW_SAMPLES",
            markout_5s_count=15,
            markout_30s_count=15,
            estimated_maker_fill_samples=50,
        ),
    ]
    scored = score_markets(rows)
    for s in scored:
        assert s.score == s.raw_score


# --- Case 13A: leaf zero weight ordering ---
def test_case13a_leaf_zero_weight_order() -> None:
    fill_z = _overall_confidence(_all_ones_present(fill=0.0), CFG)
    m5_z = _overall_confidence(_all_ones_present(markout_5s=0.0), CFG)
    trade_z = _overall_confidence(_all_ones_present(trade=0.0), CFG)
    assert abs(fill_z - _expect_eps_power(0.250)) < 0.02
    assert abs(m5_z - _expect_eps_power(0.245)) < 0.02
    assert abs(trade_z - _expect_eps_power(0.100)) < 0.02
    assert fill_z < m5_z < trade_z


def test_case13b_entire_markout_zero() -> None:
    entire = _overall_confidence(
        _all_ones_present(markout_5s=0.0, markout_30s=0.0),
        CFG,
    )
    fill_z = _overall_confidence(_all_ones_present(fill=0.0), CFG)
    trade_z = _overall_confidence(_all_ones_present(trade=0.0), CFG)
    assert abs(entire - _expect_eps_power(0.35)) < 0.02
    assert entire < fill_z < trade_z


# --- Case 14: markout partial missing ---
def test_case14_markout_partial_missing() -> None:
    both = _overall_confidence(_all_ones_present(), CFG)
    m30_miss = _overall_confidence(
        {
            "markout_5s": 1.0,
            "fill": 1.0,
            "coverage": 1.0,
            "trade": 1.0,
            "duration": 1.0,
        },
        CFG,
    )
    m5_miss = _overall_confidence(
        {
            "markout_30s": 1.0,
            "fill": 1.0,
            "coverage": 1.0,
            "trade": 1.0,
            "duration": 1.0,
        },
        CFG,
    )
    assert both > m30_miss > m5_miss
    assert abs(m30_miss - 0.895) < 0.02
    assert abs(m5_miss - 0.755) < 0.02


# --- Case 15: fill missing not high label ---
def test_case15_fill_missing_not_high_label() -> None:
    res_conf = _overall_confidence(
        {
            "markout_5s": 1.0,
            "markout_30s": 1.0,
            "coverage": 1.0,
            "trade": 1.0,
            "duration": 1.0,
        },
        CFG,
    )
    assert res_conf < CFG.high_confidence_threshold
    assert abs(res_conf - 0.75) < 0.02


# --- Case 16: observed zero stronger than missing ---
def test_case16_zero_stronger_than_missing() -> None:
    zero = _overall_from_row(_full_row(estimated_maker_fill_samples=0))
    missing = _overall_from_row(_full_row(estimated_maker_fill_samples=None))
    assert zero < missing


# --- Case 17: coverage input safety ---
def test_case17_coverage_input_safety() -> None:
    invalid_inputs = [None, float("nan"), float("inf"), float("-inf"), -10, 120]
    for val in invalid_inputs:
        row = _full_row(data_coverage_pct=val, observation_coverage_pct=val)
        res = compute_market_confidence(row, 80.0)
        assert res.confidence_breakdown["coverage"] is None
        assert not math.isnan(res.confidence)
    valid = compute_market_confidence(
        _full_row(data_coverage_pct=50, observation_coverage_pct=50),
        80.0,
    )
    assert valid.confidence_breakdown["coverage"] is not None
    zero_cov = compute_market_confidence(
        _full_row(data_coverage_pct=0, observation_coverage_pct=0),
        80.0,
    )
    assert zero_cov.confidence_breakdown["coverage"] == 0.0
    full_cov = compute_market_confidence(
        _full_row(data_coverage_pct=100, observation_coverage_pct=100),
        80.0,
    )
    assert full_cov.confidence_breakdown["coverage"] == 1.0


# --- Case 18: deterministic ranking ---
def test_case18_deterministic_ranking() -> None:
    a = make_candidate_row(symbol="AAA", market_id=1)
    b = make_candidate_row(symbol="BBB", market_id=2)
    scored1 = score_markets([a, b])
    scored2 = score_markets([b, a])
    syms1 = [s.row["symbol"] for s in scored1]
    syms2 = [s.row["symbol"] for s in scored2]
    assert syms1 == syms2


# --- Case 19: range invariants ---
def test_case19_range_invariants() -> None:
    cases = [
        _full_row(),
        _full_row(markout_5s_count=0),
        _full_row(estimated_maker_fill_samples=None),
        {},
    ]
    for row in cases:
        raw = 75.0
        res = compute_market_confidence(row, raw)
        assert 0 <= res.confidence <= 1
        assert 0 <= res.effective_score <= raw <= 100
        assert not math.isnan(res.confidence)
        assert not math.isnan(res.effective_score)


# --- Case 20: full confidence convergence ---
def test_case20_full_confidence_convergence() -> None:
    res = compute_market_confidence(_full_row(), 88.0)
    assert res.confidence > 0.99
    assert abs(res.effective_score - 88.0) < 1.0


def test_case21a_missing_not_full() -> None:
    full = _overall_confidence(_all_ones_present(), CFG)
    miss = _overall_confidence(
        {
            "markout_5s": 1.0,
            "markout_30s": 1.0,
            "coverage": 1.0,
            "trade": 1.0,
            "duration": 1.0,
        },
        CFG,
    )
    assert full == 1.0
    assert miss < full
    assert abs(miss - 0.75) < 0.02


def test_case21b_zero_missing_full_order() -> None:
    full = _overall_confidence(_all_ones_present(), CFG)
    fill_miss = _overall_confidence(
        {
            "markout_5s": 1.0,
            "markout_30s": 1.0,
            "coverage": 1.0,
            "trade": 1.0,
            "duration": 1.0,
        },
        CFG,
    )
    fill_zero = _overall_confidence(_all_ones_present(fill=0.0), CFG)
    assert fill_zero < fill_miss < full

    m5_miss = _overall_confidence(
        {
            "markout_30s": 1.0,
            "fill": 1.0,
            "coverage": 1.0,
            "trade": 1.0,
            "duration": 1.0,
        },
        CFG,
    )
    m5_zero = _overall_confidence(_all_ones_present(markout_5s=0.0), CFG)
    assert m5_zero < m5_miss < full

    trade_miss = _overall_confidence(
        {
            "markout_5s": 1.0,
            "markout_30s": 1.0,
            "fill": 1.0,
            "coverage": 1.0,
            "duration": 1.0,
        },
        CFG,
    )
    trade_zero = _overall_confidence(_all_ones_present(trade=0.0), CFG)
    assert trade_zero < trade_miss < full
    assert abs(trade_miss - 0.90) < 0.02


# --- Case 22: per-leaf sample monotonicity ---
def test_case22_per_leaf_monotonicity() -> None:
    ns = [0, 10, 100, 1000, 5000]
    for field in (
        "markout_5s_count",
        "markout_30s_count",
        "estimated_maker_fill_samples",
        "total_trade_count",
    ):
        confs = []
        for n in ns:
            row = _full_row(**{field: n})
            res = compute_market_confidence(row, 80.0)
            confs.append(res.confidence)
        for i in range(len(confs) - 1):
            assert confs[i + 1] >= confs[i]


# --- Case 23: markout summary partial missing ---
def test_case23_markout_summary() -> None:
    a = compute_market_confidence(_full_row(), 80.0)
    assert a.confidence_breakdown["markout"] is not None
    assert a.confidence_breakdown["markout"] > 0.99

    b = compute_market_confidence(
        _full_row(markout_30s_count=None),
        80.0,
    )
    assert abs(b.confidence_breakdown["markout"] - 0.70) < 0.02

    c = compute_market_confidence(
        _full_row(markout_5s_count=None),
        80.0,
    )
    assert abs(c.confidence_breakdown["markout"] - 0.30) < 0.02

    d = compute_market_confidence(
        _full_row(markout_5s_count=None, markout_30s_count=None),
        80.0,
    )
    assert d.confidence_breakdown["markout"] is None


# --- negative samples are missing not zero ---
def test_negative_sample_is_missing() -> None:
    res = compute_market_confidence(
        _full_row(markout_5s_count=-1),
        80.0,
    )
    assert res.confidence_breakdown["markout_5s"] is None


def test_negative_duration_is_missing() -> None:
    res = compute_market_confidence(
        _full_row(observation_hours=-5),
        80.0,
    )
    assert res.confidence_breakdown["duration"] is None


def test_fill_rate_none_uses_sample_count() -> None:
    res = compute_market_confidence(
        _full_row(
            estimated_maker_fill_samples=99,
            estimated_maker_fill_rate_30s_conservative=None,
        ),
        80.0,
    )
    assert res.confidence_breakdown["estimated_fill"] is not None
    assert res.confidence_breakdown["estimated_fill"] > 0.0


def test_curve_helpers_reject_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        sample_confidence(-1, 200)
    with pytest.raises(ValueError):
        coverage_confidence_from_pct(-10)
    with pytest.raises(ValueError):
        coverage_confidence_from_pct(120)
    with pytest.raises(ValueError):
        duration_confidence_from_hours(-1, 24)
