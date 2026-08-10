"""Real DuckDB SQL regression tests for forward-horizon volatility."""

from __future__ import annotations

import inspect
import math
from unittest.mock import MagicMock

import duckdb
import pytest

from lighter_mm.analytics.book_metrics import (
    _forward_horizon_sql,
    _resolve_mid_relation,
    _volatility_explain_plans,
    _volatility_sql,
)
from lighter_mm.config import Settings


def _bps_ratio(ratio: float) -> float:
    return abs(math.log(ratio)) * 10_000.0


def _settings() -> Settings:
    return Settings(book_sample_interval_seconds=5.0)


def _create_mids(con: duckdb.DuckDBPyConnection, rows: list[tuple[int, int, float]]) -> None:
    con.execute("CREATE TABLE book_mids (market_id INTEGER, timestamp_ms BIGINT, mid DOUBLE)")
    con.executemany("INSERT INTO book_mids VALUES (?, ?, ?)", rows)


def test_case_a_known_mid_series_horizons() -> None:
    """1 market, 5s spacing, constant relative moves → exact horizon expectations."""
    con = duckdb.connect()
    step = 1.01
    rows = [(1, i * 5000, 100.0 * (step**i)) for i in range(13)]
    _create_mids(con, rows)
    out = _volatility_sql(con, _settings())[1]

    one_step = _bps_ratio(step)
    assert out["p50_abs_mid_move_1s_bps"] == pytest.approx(one_step, rel=1e-6)
    assert out["volatility_5s_sample_count"] == 12
    assert out["p50_abs_mid_move_5s_bps"] == pytest.approx(one_step, rel=1e-6)
    assert out["volatility_30s_sample_count"] == 7
    assert out["p50_abs_mid_move_30s_bps"] == pytest.approx(_bps_ratio(step**6), rel=1e-6)
    assert out["volatility_60s_sample_count"] == 1
    assert out["p50_abs_mid_move_60s_bps"] == pytest.approx(_bps_ratio(step**12), rel=1e-6)


def test_case_b_book_mids_and_book_observed_agree() -> None:
    con = duckdb.connect()
    rows = [(1, i * 5000, 100.0 * (1.01**i)) for i in range(10)]
    _create_mids(con, rows)
    con.execute(
        "CREATE TABLE book_observed AS SELECT market_id, timestamp_ms, mid FROM book_mids"
    )
    via_mids = _volatility_sql(con, _settings())[1]
    con.execute("DROP TABLE book_mids")
    via_observed = _volatility_sql(con, _settings())[1]
    for key in (
        "p50_abs_mid_move_1s_bps",
        "p50_abs_mid_move_5s_bps",
        "p50_abs_mid_move_30s_bps",
        "volatility_5s_sample_count",
    ):
        assert via_mids[key] == pytest.approx(via_observed[key], rel=1e-9, abs=1e-9)


def test_case_c_tolerance_picks_first_in_window() -> None:
    """5s horizon: choose the earliest sample in [target, target+tolerance]."""
    con = duckdb.connect()
    settings = _settings()
    tolerance_ms = max(int(settings.book_sample_interval_seconds * 1500), 2500)
    horizon_ms = 5000
    origin = 0
    target = origin + horizon_ms
    rows = [
        (1, origin, 100.0),
        (1, target + 100, 101.0),
        (1, target + 200, 999.0),
        (1, target + tolerance_ms + 1, 50.0),
    ]
    _create_mids(con, rows)
    sql = (
        _forward_horizon_sql("book_mids", horizon_ms, tolerance_ms)
        + "\nSELECT origin_ts, mid0, mid1 FROM paired ORDER BY origin_ts"
    )
    paired = con.execute(sql).fetchall()
    # Origin at t=0 must pick the earliest in-window sample (101), not 999.
    assert (0, 100.0, 101.0) in paired
    origin0 = [r for r in paired if r[0] == 0]
    assert origin0 == [(0, 100.0, 101.0)]


def test_case_d_no_cross_market_join() -> None:
    con = duckdb.connect()
    rows: list[tuple[int, int, float]] = []
    for i in range(8):
        ts = i * 5000
        rows.append((1, ts, 100.0))
        rows.append((2, ts, 100.0 * (1.1**i)))
    _create_mids(con, rows)
    out = _volatility_sql(con, _settings())
    assert out[1]["p50_abs_mid_move_5s_bps"] == pytest.approx(0.0, abs=1e-9)
    assert out[2]["p50_abs_mid_move_5s_bps"] > 100.0


def test_case_e_fallback_when_book_mids_missing() -> None:
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE book_observed (market_id INTEGER, timestamp_ms BIGINT, mid DOUBLE)"
    )
    con.executemany(
        "INSERT INTO book_observed VALUES (?, ?, ?)",
        [(1, i * 5000, 100.0 * (1.01**i)) for i in range(5)],
    )
    assert _resolve_mid_relation(con) == "book_observed"
    out = _volatility_sql(con, _settings())
    assert 1 in out
    assert out[1]["volatility_5s_sample_count"] == 4


def test_case_f_non_catalog_errors_propagate() -> None:
    con = MagicMock()
    con.execute.side_effect = duckdb.OutOfMemoryException("simulated OOM")
    with pytest.raises(duckdb.OutOfMemoryException, match="simulated OOM"):
        _resolve_mid_relation(con)


def test_explain_plans_use_same_mid_relation_as_production() -> None:
    con = duckdb.connect()
    _create_mids(con, [(1, i * 5000, 100.0 * (1.01**i)) for i in range(8)])
    assert _resolve_mid_relation(con) == "book_mids"
    plans = _volatility_explain_plans(con, _settings())
    assert set(plans) == {"5s", "30s", "60s"}
    assert all(isinstance(v, str) and v for v in plans.values())
    src = inspect.getsource(_volatility_explain_plans)
    assert "_resolve_mid_relation" in src
    assert "book_observed" not in src
