"""Tests for Analyzer two-stage screening (Stage 1 → Stage 2)."""

from __future__ import annotations

import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from lighter_mm.analytics.aggregation import _analyze_range_impl, analyze_range
from lighter_mm.analytics.parquet_source import _connect
from lighter_mm.analytics.screening import (
    Stage1Market,
    _apply_screening_scores,
    merge_screening_and_full_results,
    run_stage1,
    select_stage2_markets,
)
from lighter_mm.cloud.dashboard_data import build_dashboard_payload
from lighter_mm.config import Settings


def _book_row(
    ts: int,
    market_id: int,
    symbol: str,
    *,
    bid: float = 100.0,
    ask: float = 100.10,
    is_usable: bool = True,
    is_stale: bool = False,
) -> dict:
    mid = (bid + ask) / 2.0
    spread_bps = (ask - bid) / mid * 10000.0
    return {
        "timestamp_ms": ts,
        "market_id": market_id,
        "symbol": symbol,
        "best_bid": bid,
        "best_ask": ask,
        "mid": mid,
        "spread_bps": spread_bps,
        "spread_absolute": ask - bid,
        "best_bid_size_base": 1.0,
        "best_ask_size_base": 1.0,
        "best_bid_size_usd": 100.0,
        "best_ask_size_usd": 100.0,
        "is_stale": is_stale,
        "is_usable": is_usable,
        "is_inactive": False,
        "book_update_age_ms": 1000,
        "nonce": 1,
        "index_price": None,
        "mark_price": None,
        "stats_mid_price": None,
        "open_interest": None,
        "last_trade_price": None,
        "current_funding_rate": None,
        "funding_rate": None,
        "daily_base_token_volume": None,
        "daily_quote_token_volume": None,
        "daily_price_low": None,
        "daily_price_high": None,
        "daily_price_change": None,
        "bid_depth_5bps_usd": 50.0,
        "ask_depth_5bps_usd": 50.0,
        "two_sided_depth_5bps_usd": 100.0,
        "bid_depth_10bps_usd": 200.0,
        "ask_depth_10bps_usd": 200.0,
        "two_sided_depth_10bps_usd": 400.0,
        "bid_depth_25bps_usd": 400.0,
        "ask_depth_25bps_usd": 400.0,
        "two_sided_depth_25bps_usd": 800.0,
    }


def _trade_row(ts: int, market_id: int, symbol: str, trade_id: int) -> dict:
    return {
        "timestamp_ms": ts,
        "market_id": market_id,
        "symbol": symbol,
        "trade_id": trade_id,
        "usd_amount": 50.0,
        "type": "trade",
        "price": 100.0,
        "is_maker_ask": False,
    }


def _markout_row(ts: int, market_id: int, symbol: str, trade_id: int, horizon: int) -> dict:
    return {
        "timestamp_ms": ts,
        "market_id": market_id,
        "symbol": symbol,
        "trade_id": trade_id,
        "horizon_s": horizon,
        "maker_markout_bps": 1.5,
    }


def _write_parquet(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(rows[0].keys())
    data = {k: [r.get(k) for r in rows] for k in keys}
    pq.write_table(pa.table(data), path)


def _seed_multi_market_fixture(tmp_path: Path, n_markets: int = 5) -> tuple[int, int]:
    base = int(time.time() * 1000) - 3_600_000
    end = base + 3_600_000
    books: list[dict] = []
    trades: list[dict] = []
    markouts: list[dict] = []

    for i in range(n_markets):
        mid = i + 1
        symbol = f"M{mid}"
        spread = 1.0 + i * 0.5
        ask = 100.0 + spread / 100.0
        for j in range(30):
            ts = base + j * 120_000
            books.append(_book_row(ts, mid, symbol, bid=100.0, ask=ask))
        for t in range(25):
            ts = base + t * 140_000
            trades.append(_trade_row(ts, mid, symbol, t + 1))
            markouts.append(_markout_row(ts, mid, symbol, t + 1, 5))
            markouts.append(_markout_row(ts, mid, symbol, t + 1, 30))

    book_dir = tmp_path / "book_samples/date=2026-08-09/hour=10"
    trade_dir = tmp_path / "trades/date=2026-08-09/hour=10"
    markout_dir = tmp_path / "markouts/date=2026-08-09/hour=10"
    _write_parquet(book_dir / "books.parquet", books)
    _write_parquet(trade_dir / "trades.parquet", trades)
    _write_parquet(markout_dir / "markouts.parquet", markouts)
    return base, end


def test_stage1_aggregates_all_markets(tmp_path: Path) -> None:
    start_ms, end_ms = _seed_multi_market_fixture(tmp_path, n_markets=4)
    settings = Settings(data_dir=tmp_path)
    from lighter_mm.storage.parquet_validation import prepare_parquet_dataset

    book_valid, _ = prepare_parquet_dataset(tmp_path / "book_samples", quarantine=False)
    trade_valid, _ = prepare_parquet_dataset(tmp_path / "trades", quarantine=False)
    markout_valid, _ = prepare_parquet_dataset(tmp_path / "markouts", quarantine=False)
    con = _connect(tmp_path)
    stage1, _ = run_stage1(
        con,
        settings,
        start_ms=start_ms,
        end_ms=end_ms,
        book_valid=book_valid,
        trade_valid=trade_valid,
        markout_valid=markout_valid,
    )
    con.close()

    assert len(stage1) == 4
    for m in stage1:
        assert m.book_observation_count == 30
        assert m.trade_count == 25
        assert m.observation_coverage > 0
        assert m.median_spread_bps is not None
        assert m.median_spread_bps > 0


def test_stage1_materializes_book_tables(tmp_path: Path) -> None:
    start_ms, end_ms = _seed_multi_market_fixture(tmp_path, n_markets=2)
    settings = Settings(data_dir=tmp_path)
    from lighter_mm.storage.parquet_validation import prepare_parquet_dataset

    book_valid, _ = prepare_parquet_dataset(tmp_path / "book_samples", quarantine=False)
    con = _connect(tmp_path)
    try:
        run_stage1(
            con,
            settings,
            start_ms=start_ms,
            end_ms=end_ms,
            book_valid=book_valid,
            trade_valid=[],
        )
        tables = {
            row[0]
            for row in con.execute(
                "SELECT table_name FROM duckdb_tables() "
                "WHERE table_name IN ('stage1_book_deduped', 'stage1_book_observed')"
            ).fetchall()
        }
        assert tables == {"stage1_book_deduped", "stage1_book_observed"}
        raw_views = con.execute(
            "SELECT COUNT(*) FROM duckdb_views() WHERE view_name = 'stage1_book_raw'"
        ).fetchone()
        assert raw_views is not None and raw_views[0] == 0
    finally:
        con.close()


def test_stage1_dedupes_duplicate_books(tmp_path: Path) -> None:
    start_ms = int(time.time() * 1000) - 3_600_000
    end_ms = start_ms + 3_600_000
    ts = start_ms + 1000
    dup = _book_row(ts, 1, "DUP")
    dup2 = dict(dup)
    books = [dup, dup2]
    book_dir = tmp_path / "book_samples/date=2026-08-09/hour=10"
    _write_parquet(book_dir / "books.parquet", books)

    settings = Settings(data_dir=tmp_path)
    from lighter_mm.storage.parquet_validation import prepare_parquet_dataset

    book_valid, _ = prepare_parquet_dataset(tmp_path / "book_samples", quarantine=False)
    con = _connect(tmp_path)
    stage1, _ = run_stage1(
        con,
        settings,
        start_ms=start_ms,
        end_ms=end_ms,
        book_valid=book_valid,
        trade_valid=[],
    )
    con.close()
    assert len(stage1) == 1
    assert stage1[0].book_observation_count == 1


def test_stage1_excludes_unusable_books_from_spread(tmp_path: Path) -> None:
    start_ms = int(time.time() * 1000) - 3_600_000
    end_ms = start_ms + 3_600_000
    bad = _book_row(start_ms, 1, "BAD", bid=100.0, ask=100.20, is_usable=False)
    good = _book_row(start_ms + 1000, 1, "BAD", bid=100.0, ask=100.20, is_usable=True)
    book_dir = tmp_path / "book_samples/date=2026-08-09/hour=10"
    _write_parquet(book_dir / "books.parquet", [bad, good])

    settings = Settings(data_dir=tmp_path)
    from lighter_mm.storage.parquet_validation import prepare_parquet_dataset

    book_valid, _ = prepare_parquet_dataset(tmp_path / "book_samples", quarantine=False)
    con = _connect(tmp_path)
    stage1, _ = run_stage1(
        con,
        settings,
        start_ms=start_ms,
        end_ms=end_ms,
        book_valid=book_valid,
        trade_valid=[],
    )
    con.close()
    assert len(stage1) == 1
    assert stage1[0].median_spread_bps is not None


def test_stage1_tpm_matches_full_analyzer(tmp_path: Path) -> None:
    """Stage 1 TPM must match full Analyzer slot-based semantics (zeros for inactive minutes)."""
    start_ms = int(time.time() * 1000) - 3_600_000
    end_ms = start_ms + 3_600_000
    trades = [
        _trade_row(start_ms + 60_000, 1, "TPM", 1),
        _trade_row(start_ms + 120_000, 1, "TPM", 2),
    ]
    for i in range(100):
        trades.append(_trade_row(start_ms + 180_000 + i, 1, "TPM", 100 + i))
    book = _book_row(start_ms, 1, "TPM")
    book_dir = tmp_path / "book_samples/date=2026-08-09/hour=10"
    trade_dir = tmp_path / "trades/date=2026-08-09/hour=10"
    _write_parquet(book_dir / "books.parquet", [book])
    _write_parquet(trade_dir / "trades.parquet", trades)

    settings = Settings(data_dir=tmp_path)
    hours = (end_ms - start_ms) / 3_600_000.0
    from lighter_mm.storage.parquet_validation import prepare_parquet_dataset

    book_valid, _ = prepare_parquet_dataset(tmp_path / "book_samples", quarantine=False)
    trade_valid, _ = prepare_parquet_dataset(tmp_path / "trades", quarantine=False)
    con = _connect(tmp_path)
    stage1, _ = run_stage1(
        con,
        settings,
        start_ms=start_ms,
        end_ms=end_ms,
        book_valid=book_valid,
        trade_valid=trade_valid,
    )
    con.close()

    legacy = _analyze_range_impl(
        settings,
        start_ms=start_ms,
        end_ms=end_ms,
        hours=hours,
        read_only=True,
    )
    legacy_row = next(
        r for r in legacy["scored"] if int(r.row["market_id"]) == 1
    ).row

    m = stage1[0]
    assert m.trades_per_minute_mean == legacy_row["trades_per_minute_mean"]
    assert m.trades_per_minute_median == legacy_row["trades_per_minute_median"]
    assert m.trades_per_minute_median != m.trades_per_minute_mean


def test_two_stage_top_level_row_counts_match_legacy(tmp_path: Path) -> None:
    start_ms, end_ms = _seed_multi_market_fixture(tmp_path, n_markets=5)
    legacy = analyze_range(
        Settings(data_dir=tmp_path, analyzer_two_stage_enabled=False),
        start_ms=start_ms,
        end_ms=end_ms,
        read_only=True,
    )
    staged = analyze_range(
        Settings(
            data_dir=tmp_path,
            analyzer_two_stage_enabled=True,
            analyzer_stage1_min_coverage=0.0,
            analyzer_stage1_min_trades=0,
            analyzer_stage1_min_spread_bps=0.0,
            analyzer_stage2_top_n=2,
        ),
        start_ms=start_ms,
        end_ms=end_ms,
        read_only=True,
    )
    assert staged["book_row_count"] == legacy["book_row_count"]
    assert staged["trade_row_count"] == legacy["trade_row_count"]
    assert staged["markout_row_count"] == legacy["markout_row_count"]
    assert staged["latest_book_event_ms"] == legacy["latest_book_event_ms"]
    assert staged["two_stage"]["stage2_book_row_count"] < legacy["book_row_count"]


def test_eligibility_and_top_n_selection() -> None:
    markets = [
        Stage1Market(
            market_id=1,
            eligible=True,
            screening_score=0.9,
            observation_coverage=0.95,
            trade_count=50,
            median_spread_bps=2.0,
        ),
        Stage1Market(
            market_id=2,
            eligible=True,
            screening_score=0.5,
            observation_coverage=0.95,
            trade_count=50,
            median_spread_bps=2.0,
        ),
        Stage1Market(
            market_id=3,
            eligible=False,
            screening_score=None,
            observation_coverage=0.5,
            trade_count=2,
            median_spread_bps=0.2,
        ),
    ]
    selected = select_stage2_markets(markets, top_n=1)
    assert selected == [1]

    markets[0].screening_score = 0.3
    markets[1].screening_score = 0.8
    selected2 = select_stage2_markets(markets, top_n=10)
    assert set(selected2) == {1, 2}

    none_eligible = select_stage2_markets(
        [Stage1Market(market_id=9, eligible=False)], top_n=40
    )
    assert none_eligible == []

    with_extra = select_stage2_markets(
        [Stage1Market(market_id=9, eligible=False)],
        top_n=1,
        extra_market_ids={42},
    )
    assert with_extra == [42]


def test_market_ids_filter_limits_stage2_input(tmp_path: Path) -> None:
    start_ms, end_ms = _seed_multi_market_fixture(tmp_path, n_markets=3)
    settings = Settings(data_dir=tmp_path)
    hours = (end_ms - start_ms) / 3_600_000.0

    result = _analyze_range_impl(
        settings,
        start_ms=start_ms,
        end_ms=end_ms,
        hours=hours,
        market_ids=frozenset({1}),
        read_only=True,
    )
    assert len(result["scored"]) == 1
    assert int(result["scored"][0].row["market_id"]) == 1
    assert result["book_row_count"] == 30


def test_two_stage_null_semantics_for_screened(tmp_path: Path) -> None:
    start_ms, end_ms = _seed_multi_market_fixture(tmp_path, n_markets=3)
    settings = Settings(
        data_dir=tmp_path,
        analyzer_two_stage_enabled=True,
        analyzer_stage1_min_coverage=0.0,
        analyzer_stage1_min_trades=0,
        analyzer_stage1_min_spread_bps=0.0,
        analyzer_stage2_top_n=1,
    )
    result = analyze_range(
        settings,
        start_ms=start_ms,
        end_ms=end_ms,
        read_only=True,
    )
    screened = result.get("screened") or []
    assert len(screened) == 2
    for row in screened:
        assert row["analysis_stage"] == "screened"
        assert row["score"] is None
        assert row["maker_markout_5s_median_bps"] is None
        assert row["estimated_maker_fill_rate_5s_conservative"] is None

    full = result.get("scored") or []
    assert len(full) == 1
    assert full[0].score is not None


def test_feature_flag_false_matches_legacy(tmp_path: Path) -> None:
    start_ms, end_ms = _seed_multi_market_fixture(tmp_path, n_markets=3)
    settings = Settings(data_dir=tmp_path, analyzer_two_stage_enabled=False)
    legacy = analyze_range(
        settings,
        start_ms=start_ms,
        end_ms=end_ms,
        read_only=True,
    )
    assert len(legacy["scored"]) == 3
    assert legacy.get("screened") is None or legacy.get("screened") == []


def test_all_markets_stage2_equivalent_scores(tmp_path: Path) -> None:
    start_ms, end_ms = _seed_multi_market_fixture(tmp_path, n_markets=4)
    base_settings = Settings(data_dir=tmp_path, analyzer_two_stage_enabled=False)
    legacy = analyze_range(
        base_settings,
        start_ms=start_ms,
        end_ms=end_ms,
        read_only=True,
    )

    two_stage_settings = Settings(
        data_dir=tmp_path,
        analyzer_two_stage_enabled=True,
        analyzer_stage1_min_coverage=0.0,
        analyzer_stage1_min_trades=0,
        analyzer_stage1_min_spread_bps=0.0,
        analyzer_stage2_top_n=100,
    )
    staged = analyze_range(
        two_stage_settings,
        start_ms=start_ms,
        end_ms=end_ms,
        read_only=True,
    )

    legacy_by_id = {int(s.row["market_id"]): s.score for s in legacy["scored"]}
    for s in staged["scored"]:
        mid = int(s.row["market_id"])
        assert mid in legacy_by_id
        assert abs(s.score - legacy_by_id[mid]) < 0.01


def test_paper_mm_market_forced_into_stage2(tmp_path: Path) -> None:
    start_ms, end_ms = _seed_multi_market_fixture(tmp_path, n_markets=5)
    settings = Settings(
        data_dir=tmp_path,
        analyzer_two_stage_enabled=True,
        analyzer_stage1_min_coverage=0.0,
        analyzer_stage1_min_trades=0,
        analyzer_stage1_min_spread_bps=0.0,
        analyzer_stage2_top_n=1,
        paper_mm_enabled=True,
    )
    result = analyze_range(
        settings,
        start_ms=start_ms,
        end_ms=end_ms,
        read_only=True,
        paper_mm_market_ids={5},
        paper_mm_top_n_override=2,
        paper_mm_order_usd_override=50.0,
    )
    scored_ids = {int(s.row["market_id"]) for s in result["scored"]}
    assert 5 in scored_ids


def test_screening_score_ordering() -> None:
    markets = [
        Stage1Market(
            market_id=1,
            eligible=True,
            median_spread_bps=5.0,
            trade_count=100,
            observation_coverage=0.99,
        ),
        Stage1Market(
            market_id=2,
            eligible=True,
            median_spread_bps=1.0,
            trade_count=10,
            observation_coverage=0.91,
        ),
    ]
    _apply_screening_scores(markets)
    assert markets[0].screening_score is not None
    assert markets[1].screening_score is not None
    assert markets[0].screening_score > markets[1].screening_score


def test_top_n_subset_score_differs_from_legacy(tmp_path: Path) -> None:
    """Document that Estimated Fill peer pool shrinks when TOP N < all markets."""
    start_ms, end_ms = _seed_multi_market_fixture(tmp_path, n_markets=5)
    legacy = analyze_range(
        Settings(data_dir=tmp_path, analyzer_two_stage_enabled=False),
        start_ms=start_ms,
        end_ms=end_ms,
        read_only=True,
    )
    subset = analyze_range(
        Settings(
            data_dir=tmp_path,
            analyzer_two_stage_enabled=True,
            analyzer_stage1_min_coverage=0.0,
            analyzer_stage1_min_trades=0,
            analyzer_stage1_min_spread_bps=0.0,
            analyzer_stage2_top_n=2,
        ),
        start_ms=start_ms,
        end_ms=end_ms,
        read_only=True,
    )
    legacy_by_id = {int(s.row["market_id"]): s.score for s in legacy["scored"]}
    for s in subset["scored"]:
        mid = int(s.row["market_id"])
        # Spread/activity/coverage peers match; fill peer may differ — scores may diverge.
        assert mid in legacy_by_id


def test_selected_but_unscored_is_not_labeled_screened() -> None:
    stage1 = [
        Stage1Market(
            market_id=1,
            symbol="KEEP",
            maker_markout_5s_median_bps=1.25,
            eligible=True,
        ),
        Stage1Market(market_id=2, symbol="SKIP", eligible=False),
    ]
    result = merge_screening_and_full_results(
        stage1,
        {"scored": []},
        hours=24,
        start_ms=0,
        end_ms=1,
        stage1_elapsed=0.1,
        stage2_elapsed=0.1,
        selected_market_ids=[1],
    )
    by_id = {int(row["market_id"]): row for row in result["markets"]}
    assert by_id[1]["analysis_stage"] == "selected_incomplete"
    assert by_id[1]["maker_markout_5s_median_bps"] == 1.25
    assert by_id[2]["analysis_stage"] == "screened"
    assert by_id[2]["maker_markout_5s_median_bps"] is None
    assert result["two_stage"]["markets_selected_incomplete"] == 1
    assert len(result["incomplete"]) == 1
    assert result["incomplete"][0]["analysis_stage"] == "selected_incomplete"
    assert result["incomplete"][0]["maker_markout_5s_median_bps"] == 1.25


def test_dashboard_payload_includes_selected_incomplete(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", reports_dir=tmp_path / "reports")
    stage1 = [
        Stage1Market(
            market_id=1,
            symbol="KEEP",
            maker_markout_5s_median_bps=1.25,
            eligible=True,
        ),
        Stage1Market(market_id=2, symbol="SKIP", eligible=False),
    ]
    result = merge_screening_and_full_results(
        stage1,
        {"scored": []},
        hours=24,
        start_ms=0,
        end_ms=1,
        stage1_elapsed=0.1,
        stage2_elapsed=0.1,
        selected_market_ids=[1],
    )
    payload = build_dashboard_payload(settings, hours=24, analysis_result=result)
    by_id = {int(row["market_id"]): row for row in payload["markets"]}
    assert by_id[1]["analysis_stage"] == "selected_incomplete"
    assert by_id[1]["maker_markout_5s_median_bps"] == 1.25
    assert by_id[2]["analysis_stage"] == "screened"
    assert payload["latest"]["markets"] == 2
    assert payload["latest"]["markets_analyzed"] == 0
