"""Paper MM analyzer integration."""

from __future__ import annotations

import duckdb

from lighter_mm.config import Settings
from tests.helpers.paper_mm import setup_paper_mm_tables


def test_paper_mm_replay_ok() -> None:
    con = duckdb.connect()
    books = [
        (1, "T", 10_000, 100.5, True, 100.0, 101.0, 100.0, 100.0),
        (1, "T", 15_000, 100.5, True, 100.0, 101.0, 100.0, 100.0),
    ]
    trades = [
        (1, 1, 10_001, 100.0, 50.0, False, "trade"),
        (1, 2, 10_011, 101.0, 50.0, True, "trade"),
    ]
    setup_paper_mm_tables(con, books=books, trades=trades)

    from lighter_mm.paper_mm.metrics import paper_mm_config_from_settings
    from lighter_mm.paper_mm.replay import run_paper_mm_replay

    settings = Settings(paper_mm_enabled=True, paper_mm_top_n=1)
    result = run_paper_mm_replay(
        con,
        1,
        0,
        20_000,
        paper_mm_config_from_settings(settings),
        1.0,
    )
    assert result["paper_mm_status"] == "ok"


def test_deterministic_replay() -> None:
    con = duckdb.connect()
    books = [(1, "T", 10_000, 100.5, True, 100.0, 101.0, 100.0, 100.0)]
    trades = [(1, 1, 10_001, 100.0, 50.0, False, "trade")]
    setup_paper_mm_tables(con, books=books, trades=trades)

    from lighter_mm.paper_mm.metrics import paper_mm_config_from_settings
    from lighter_mm.paper_mm.replay import run_paper_mm_replay

    settings = Settings()
    cfg = paper_mm_config_from_settings(settings)
    a = run_paper_mm_replay(con, 1, 0, 20_000, cfg, 1.0)
    b = run_paper_mm_replay(con, 1, 0, 20_000, cfg, 1.0)
    assert a == b


def test_null_is_maker_ask_is_skipped_not_coerced() -> None:
    con = duckdb.connect()
    books = [(1, "T", 10_000, 100.5, True, 100.0, 101.0, 100.0, 100.0)]
    trades_with_null = [
        (1, 1, 10_001, 100.0, 50.0, None, "trade"),
        (1, 2, 10_011, 101.0, 50.0, True, "trade"),
    ]
    trades_without_null = [(1, 2, 10_011, 101.0, 50.0, True, "trade")]
    setup_paper_mm_tables(con, books=books, trades=trades_with_null)

    from lighter_mm.paper_mm.metrics import paper_mm_config_from_settings
    from lighter_mm.paper_mm.replay import run_paper_mm_replay

    cfg = paper_mm_config_from_settings(Settings())
    with_null = run_paper_mm_replay(con, 1, 0, 20_000, cfg, 1.0)

    con2 = duckdb.connect()
    setup_paper_mm_tables(con2, books=books, trades=trades_without_null)
    without_null = run_paper_mm_replay(con2, 1, 0, 20_000, cfg, 1.0)
    assert with_null["paper_mm_bid_fills"] == without_null["paper_mm_bid_fills"]
    assert with_null["paper_mm_ask_fills"] == without_null["paper_mm_ask_fills"]


def test_not_simulated_status_fields() -> None:
    from lighter_mm.paper_mm.metrics import empty_paper_mm_result

    row = empty_paper_mm_result(Settings(), status="not_simulated")
    assert row["paper_mm_status"] == "not_simulated"
    assert row["paper_mm_total_pnl_usd"] is None
