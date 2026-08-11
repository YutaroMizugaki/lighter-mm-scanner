"""Two-stage Analyzer × Score Confidence integration regression tests."""

from __future__ import annotations

from pathlib import Path

from lighter_mm.analytics.screening import Stage1Market, merge_screening_and_full_results
from lighter_mm.cloud.dashboard_data import build_dashboard_payload
from lighter_mm.config import Settings
from lighter_mm.paper_mm import resolve_paper_mm_targets
from lighter_mm.scoring import ScoredMarket


def _scored_market(
    symbol: str,
    raw_score: float,
    effective_score: float,
    *,
    market_id: int,
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


def _stage1(market_id: int, symbol: str) -> Stage1Market:
    return Stage1Market(market_id=market_id, symbol=symbol, eligible=True)


def _merge_fixture() -> dict:
    # Stage 1 order: AAA → BBB → CCC (screening order).
    stage1 = [_stage1(1, "AAA"), _stage1(2, "BBB"), _stage1(3, "CCC")]
    # Stage 2 full_results already ranked by effective_score DESC.
    full_scored = [
        _scored_market("CCC", raw_score=70.0, effective_score=70.0, market_id=3),
        _scored_market("BBB", raw_score=80.0, effective_score=60.0, market_id=2),
        _scored_market("AAA", raw_score=95.0, effective_score=20.0, market_id=1),
    ]
    return merge_screening_and_full_results(
        stage1,
        {"scored": full_scored, "avoid": []},
        hours=24.0,
        start_ms=0,
        end_ms=86_400_000,
        stage1_elapsed=0.1,
        stage2_elapsed=1.0,
        selected_market_ids=[1, 2, 3],
    )


def test_merge_preserves_effective_score_order_over_stage1_order() -> None:
    result = _merge_fixture()
    assert [s.row["symbol"] for s in result["scored"]] == ["CCC", "BBB", "AAA"]
    assert [s.row["symbol"] for s in result["candidates"]] == ["CCC", "BBB", "AAA"]


def test_merge_dashboard_payload_top_candidate_is_effective_leader(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", reports_dir=tmp_path / "reports")
    payload = build_dashboard_payload(settings, hours=24, analysis_result=_merge_fixture())
    market_symbols = [m["symbol"] for m in payload["markets"] if m.get("analysis_stage") == "full"]
    candidate_symbols = [m["symbol"] for m in payload["candidates"]]
    assert market_symbols == ["CCC", "BBB", "AAA"]
    assert candidate_symbols == ["CCC", "BBB", "AAA"]
    top = payload["latest"]["top_candidate"]
    assert top is not None
    assert top["symbol"] == "CCC"

    market_effective = [
        m["effective_score"]
        for m in payload["markets"]
        if m.get("analysis_stage") == "full"
    ]
    candidate_effective = [m["effective_score"] for m in payload["candidates"]]
    assert market_effective == sorted(market_effective, reverse=True)
    assert candidate_effective == sorted(candidate_effective, reverse=True)


def test_merge_paper_mm_targets_use_effective_score_order_after_two_stage() -> None:
    result = _merge_fixture()
    targets = resolve_paper_mm_targets(result["scored"], top_n=2)
    assert [s.row["symbol"] for s in targets] == ["CCC", "BBB"]


def test_screened_markets_do_not_get_confidence_fields() -> None:
    stage1 = [_stage1(1, "AAA"), _stage1(2, "BBB")]
    full_scored = [
        _scored_market("AAA", raw_score=80.0, effective_score=60.0, market_id=1),
    ]
    result = merge_screening_and_full_results(
        stage1,
        {"scored": full_scored, "avoid": []},
        hours=24.0,
        start_ms=0,
        end_ms=86_400_000,
        stage1_elapsed=0.1,
        stage2_elapsed=1.0,
        selected_market_ids=[1],
    )
    screened = result["screened"]
    assert len(screened) == 1
    row = screened[0]
    assert row["symbol"] == "BBB"
    assert row["analysis_stage"] == "screened"
    assert "confidence" not in row
    assert "effective_score" not in row
    assert "raw_score" not in row
    assert "confidence_breakdown" not in row
