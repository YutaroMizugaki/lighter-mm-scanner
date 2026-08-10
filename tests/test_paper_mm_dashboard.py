"""Dashboard JSON paper MM fields."""

from __future__ import annotations

from lighter_mm.cloud.dashboard_data import _market_detail, _market_row
from lighter_mm.scoring import ScoredMarket
from tests.helpers.estimated_fill import make_candidate_row


def test_market_row_includes_paper_mm() -> None:
    row = make_candidate_row(
        paper_mm_status="ok",
        paper_mm_total_pnl_usd=1.24,
        paper_mm_round_trips=31,
    )
    s = ScoredMarket(
        score=80.0,
        letter_rank="A",
        candidate=True,
        rank_components={},
        penalties=[],
        pros=[],
        cons=[],
        warnings=[],
        recommended_max_order_usd=50.0,
        size_fit={},
        row=row,
    )
    out = _market_row(s)
    assert out["paper_mm_total_pnl_usd"] == 1.24
    assert out["paper_mm_round_trips"] == 31
    assert out["paper_mm_status"] == "ok"


def test_market_detail_not_simulated() -> None:
    row = make_candidate_row(paper_mm_status="not_simulated")
    s = ScoredMarket(
        score=50.0,
        letter_rank="C",
        candidate=False,
        rank_components={},
        penalties=[],
        pros=[],
        cons=[],
        warnings=[],
        recommended_max_order_usd=None,
        size_fit={},
        row=row,
    )
    detail = _market_detail(s)
    assert detail.get("paper_mm_status") == "not_simulated"
