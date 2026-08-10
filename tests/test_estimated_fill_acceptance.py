"""Acceptance tests for Estimated Maker Fill v1 review fixes (PR #44)."""

from __future__ import annotations

import duckdb
import pytest

from lighter_mm.analytics.estimated_fill_metrics import (
    MIN_MEANINGFUL_SAMPLES,
    SNAPSHOT_BUCKET_MS,
    aggregate_estimated_fill_sql,
    attach_estimated_maker_edge,
    downsample_snapshot_counts,
    estimated_maker_edge_bps,
    snapshot_bucket_id,
)
from lighter_mm.scoring import CandidateThresholds, score_markets
from tests.helpers.estimated_fill import make_book_rows, make_candidate_row

# ---------------------------------------------------------------------------
# A — Estimated Maker Edge formula
# ---------------------------------------------------------------------------


def test_a1_edge_excludes_half_spread_and_is_insensitive_to_spread() -> None:
    edge = estimated_maker_edge_bps(
        fill_rate=0.50,
        maker_markout_bps=2.0,
        maker_fee_bps=1.0,
    )
    assert edge == pytest.approx(0.50)

    # Changing median spread must not change edge (spread is not in the formula).
    row_a = {
        "median_spread_bps": 4.0,
        "maker_markout_5s_median_bps": 2.0,
        "maker_markout_30s_median_bps": 2.0,
        "estimated_maker_fill_rate_5s_conservative": 0.50,
        "estimated_maker_fill_rate_30s_conservative": 0.50,
    }
    row_b = dict(row_a)
    row_b["median_spread_bps"] = 40.0
    attach_estimated_maker_edge(row_a, maker_fee_bps=1.0)
    attach_estimated_maker_edge(row_b, maker_fee_bps=1.0)
    assert row_a["estimated_maker_edge_30s_bps"] == pytest.approx(0.50)
    assert row_b["estimated_maker_edge_30s_bps"] == pytest.approx(0.50)
    # Must NOT be the old half-spread double-count: 0.5 * (2 + 2 - 1) = 1.5
    assert row_a["estimated_maker_edge_30s_bps"] != pytest.approx(1.50)


def test_a2_negative_markout_edge() -> None:
    edge = estimated_maker_edge_bps(
        fill_rate=0.25,
        maker_markout_bps=-4.0,
        maker_fee_bps=0.0,
    )
    assert edge == pytest.approx(-1.0)


def test_a3_null_fill_yields_null_edge() -> None:
    assert (
        estimated_maker_edge_bps(
            fill_rate=None,
            maker_markout_bps=2.0,
            maker_fee_bps=0.0,
        )
        is None
    )
    row = {
        "median_spread_bps": 4.0,
        "maker_markout_5s_median_bps": 2.0,
        "maker_markout_30s_median_bps": 2.0,
        "estimated_maker_fill_rate_5s_conservative": None,
        "estimated_maker_fill_rate_30s_conservative": None,
    }
    attach_estimated_maker_edge(row)
    assert row["estimated_maker_edge_5s_bps"] is None
    assert row["estimated_maker_edge_30s_bps"] is None


# ---------------------------------------------------------------------------
# B — 30s floor bucket semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ts", "bucket"),
    [
        (0, 0),
        (14_999, 0),
        (15_000, 0),
        (29_999, 0),
        (30_000, 1),
        (30_001, 1),
        (59_999, 1),
        (60_000, 2),
    ],
)
def test_b_bucket_boundaries_floor(ts: int, bucket: int) -> None:
    assert snapshot_bucket_id(ts, SNAPSHOT_BUCKET_MS) == bucket


def test_b_bucket_boundaries_realistic_epoch_ms() -> None:
    # 2026-01-01T00:00:00Z ≈ 1767225600000 (verify via duckdb floor).
    base = 1_767_225_600_000
    assert snapshot_bucket_id(base + 0, SNAPSHOT_BUCKET_MS) == base // SNAPSHOT_BUCKET_MS
    assert snapshot_bucket_id(base + 15_000, SNAPSHOT_BUCKET_MS) == base // SNAPSHOT_BUCKET_MS
    assert snapshot_bucket_id(base + 29_999, SNAPSHOT_BUCKET_MS) == base // SNAPSHOT_BUCKET_MS
    assert (
        snapshot_bucket_id(base + 30_000, SNAPSHOT_BUCKET_MS)
        == base // SNAPSHOT_BUCKET_MS + 1
    )


def test_b_one_snapshot_per_market_bucket() -> None:
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE book_observed (
            market_id BIGINT, symbol VARCHAR, timestamp_ms BIGINT, mid DOUBLE,
            is_usable BOOLEAN, best_bid DOUBLE, best_ask DOUBLE,
            best_bid_size_usd DOUBLE, best_ask_size_usd DOUBLE
        )
        """
    )
    # Three usable snaps in bucket 0, two in bucket 1 → downsample to 2.
    rows = [
        (1, "T", 0, 100.5, True, 100.0, 101.0, 10.0, 10.0),
        (1, "T", 10_000, 100.5, True, 100.0, 101.0, 10.0, 10.0),
        (1, "T", 29_999, 100.5, True, 100.0, 101.0, 10.0, 10.0),
        (1, "T", 30_000, 100.5, True, 100.0, 101.0, 10.0, 10.0),
        (1, "T", 45_000, 100.5, True, 100.0, 101.0, 10.0, 10.0),
    ]
    con.executemany("INSERT INTO book_observed VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    counts = downsample_snapshot_counts(con)
    assert counts[1] == 2
    snaps = con.execute(
        "SELECT timestamp_ms FROM estimated_fill_snapshots ORDER BY timestamp_ms"
    ).fetchall()
    assert [r[0] for r in snaps] == [0, 30_000]


# ---------------------------------------------------------------------------
# C — unavailable != measured zero
# ---------------------------------------------------------------------------


def test_c1_missing_price_column_is_unavailable_not_zero() -> None:
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE book_observed (
            market_id BIGINT, symbol VARCHAR, timestamp_ms BIGINT, mid DOUBLE,
            is_usable BOOLEAN, best_bid DOUBLE, best_ask DOUBLE,
            best_bid_size_usd DOUBLE, best_ask_size_usd DOUBLE
        )
        """
    )
    con.executemany(
        "INSERT INTO book_observed VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        make_book_rows(),
    )
    # Schema-drift shape: price projected as all-NULL (column name present).
    con.execute(
        """
        CREATE TABLE trade_deduped AS
        SELECT
            1::BIGINT AS market_id,
            1::BIGINT AS trade_id,
            1001000::BIGINT AS timestamp_ms,
            CAST(NULL AS DOUBLE) AS price,
            50.0::DOUBLE AS usd_amount,
            false AS is_maker_ask,
            'trade' AS type
        """
    )
    for i in range(1, MIN_MEANINGFUL_SAMPLES):
        ts = 1_000_000 + i * SNAPSHOT_BUCKET_MS
        con.execute(
            """
            INSERT INTO trade_deduped
            VALUES (1, ?, ?, CAST(NULL AS DOUBLE), 50.0, false, 'trade')
            """,
            [i + 1, ts + 1000],
        )
    out = aggregate_estimated_fill_sql(con)
    assert out == {}


def test_c2_missing_is_maker_ask_is_unavailable_not_zero() -> None:
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE book_observed (
            market_id BIGINT, symbol VARCHAR, timestamp_ms BIGINT, mid DOUBLE,
            is_usable BOOLEAN, best_bid DOUBLE, best_ask DOUBLE,
            best_bid_size_usd DOUBLE, best_ask_size_usd DOUBLE
        )
        """
    )
    con.executemany(
        "INSERT INTO book_observed VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        make_book_rows(),
    )
    con.execute(
        """
        CREATE TABLE trade_deduped AS
        SELECT
            1::BIGINT AS market_id,
            1::BIGINT AS trade_id,
            1001000::BIGINT AS timestamp_ms,
            100.0::DOUBLE AS price,
            50.0::DOUBLE AS usd_amount,
            CAST(NULL AS BOOLEAN) AS is_maker_ask,
            'trade' AS type
        """
    )
    for i in range(1, MIN_MEANINGFUL_SAMPLES):
        ts = 1_000_000 + i * SNAPSHOT_BUCKET_MS
        con.execute(
            """
            INSERT INTO trade_deduped
            VALUES (1, ?, ?, 100.0, 50.0, CAST(NULL AS BOOLEAN), 'trade')
            """,
            [i + 1, ts + 1000],
        )
    out = aggregate_estimated_fill_sql(con)
    assert out == {}


def test_c3_valid_schema_zero_eligible_is_measured_zero() -> None:
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE book_observed (
            market_id BIGINT, symbol VARCHAR, timestamp_ms BIGINT, mid DOUBLE,
            is_usable BOOLEAN, best_bid DOUBLE, best_ask DOUBLE,
            best_bid_size_usd DOUBLE, best_ask_size_usd DOUBLE
        )
        """
    )
    con.executemany(
        "INSERT INTO book_observed VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        make_book_rows(),
    )
    # Valid schema, empty regular trades → measured zero with enough samples.
    con.execute(
        """
        CREATE TABLE trade_deduped (
            market_id BIGINT, trade_id BIGINT, timestamp_ms BIGINT,
            price DOUBLE, usd_amount DOUBLE, is_maker_ask BOOLEAN, type VARCHAR
        )
        """
    )
    out = aggregate_estimated_fill_sql(con)[1]
    assert out["estimated_maker_fill_samples"] >= MIN_MEANINGFUL_SAMPLES
    assert out["estimated_maker_fill_rate_30s_conservative"] == pytest.approx(0.0)
    assert out["estimated_maker_fill_sample_quality"] in {"preliminary", "reliable"}


def test_c_source_fields_flag_short_circuits_when_false() -> None:
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE book_observed (
            market_id BIGINT, symbol VARCHAR, timestamp_ms BIGINT, mid DOUBLE,
            is_usable BOOLEAN, best_bid DOUBLE, best_ask DOUBLE,
            best_bid_size_usd DOUBLE, best_ask_size_usd DOUBLE
        )
        """
    )
    con.executemany(
        "INSERT INTO book_observed VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        make_book_rows(10),
    )
    con.execute(
        """
        CREATE TABLE trade_deduped (
            market_id BIGINT, trade_id BIGINT, timestamp_ms BIGINT,
            price DOUBLE, usd_amount DOUBLE, is_maker_ask BOOLEAN, type VARCHAR
        )
        """
    )
    assert aggregate_estimated_fill_sql(con, source_fields_available=False) == {}


# ---------------------------------------------------------------------------
# D — candidate sample gate
# ---------------------------------------------------------------------------


def test_d1_insufficient_fill_samples_blocks_candidate() -> None:
    row = make_candidate_row(
        estimated_maker_fill_samples=99,
        estimated_maker_fill_rate_30s_conservative=None,
        estimated_maker_fill_sample_quality="insufficient",
    )
    scored = score_markets([row], thresholds=CandidateThresholds())
    assert scored[0].candidate is False
    # Numeric score still computes (neutral renormalization for missing fill).
    assert scored[0].score > 0
    assert scored[0].rank_components["estimated_maker_fill"] is None


def test_d2_hundred_fill_samples_passes_fill_gate() -> None:
    row = make_candidate_row(
        estimated_maker_fill_samples=100,
        estimated_maker_fill_rate_30s_conservative=0.35,
        estimated_maker_fill_sample_quality="preliminary",
    )
    scored = score_markets([row], thresholds=CandidateThresholds())
    assert scored[0].candidate is True


def test_d3_legacy_schema_unavailable_blocks_candidate_keeps_score() -> None:
    row = make_candidate_row(
        estimated_maker_fill_samples=0,
        estimated_maker_fill_rate_30s_conservative=None,
        estimated_maker_fill_sample_quality="insufficient",
    )
    scored = score_markets([row], thresholds=CandidateThresholds())
    assert scored[0].candidate is False
    assert scored[0].rank_components["estimated_maker_fill"] is None
    assert scored[0].score > 0


def test_measured_zero_penalty_distinct_from_null() -> None:
    null_row = make_candidate_row(
        symbol="NULL_FILL",
        market_id=1,
        estimated_maker_fill_samples=20,
        estimated_maker_fill_rate_30s_conservative=None,
        estimated_maker_fill_sample_quality="insufficient",
    )
    zero_row = make_candidate_row(
        symbol="ZERO_FILL",
        market_id=2,
        estimated_maker_fill_samples=500,
        estimated_maker_fill_rate_30s_conservative=0.0,
        estimated_maker_fill_sample_quality="reliable",
    )
    scored = score_markets([null_row, zero_row], thresholds=CandidateThresholds())
    by = {s.row["symbol"]: s for s in scored}
    assert by["NULL_FILL"].rank_components["estimated_maker_fill"] is None
    assert by["ZERO_FILL"].rank_components["estimated_maker_fill"] is not None
    assert any("Estimated Maker Fill ~0" in p for p in by["ZERO_FILL"].penalties)
    assert by["NULL_FILL"].score > by["ZERO_FILL"].score


# ---------------------------------------------------------------------------
# Conservative boundary + side semantics regressions
# ---------------------------------------------------------------------------


def test_conservative_boundary_149_99_false_150_true() -> None:
    spacing = SNAPSHOT_BUCKET_MS

    def _run(eligible: float) -> float:
        con = duckdb.connect()
        books = []
        trades = []
        for i in range(MIN_MEANINGFUL_SAMPLES):
            ts = 1_000_000 + i * spacing
            books.append((1, "T", ts, 100.5, True, 100.0, 101.0, 100.0, 100.0))
            trades.append((1, i * 2 + 1, ts + 1000, 100.0, eligible, False, "trade"))
            trades.append((1, i * 2 + 2, ts + 1000, 101.0, eligible, True, "trade"))
        con.execute(
            """
            CREATE TABLE book_observed (
                market_id BIGINT, symbol VARCHAR, timestamp_ms BIGINT, mid DOUBLE,
                is_usable BOOLEAN, best_bid DOUBLE, best_ask DOUBLE,
                best_bid_size_usd DOUBLE, best_ask_size_usd DOUBLE
            )
            """
        )
        con.executemany(
            "INSERT INTO book_observed VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", books
        )
        con.execute(
            """
            CREATE TABLE trade_deduped (
                market_id BIGINT, trade_id BIGINT, timestamp_ms BIGINT,
                price DOUBLE, usd_amount DOUBLE, is_maker_ask BOOLEAN, type VARCHAR
            )
            """
        )
        con.executemany(
            "INSERT INTO trade_deduped VALUES (?, ?, ?, ?, ?, ?, ?)", trades
        )
        return float(
            aggregate_estimated_fill_sql(con)[1]["estimated_maker_fill_by_size"]["50"][
                "5s"
            ]["conservative"]
        )

    # required = touch 100 + order 50 = 150
    assert _run(149.99) == pytest.approx(0.0)
    assert _run(150.00) == pytest.approx(1.0)
