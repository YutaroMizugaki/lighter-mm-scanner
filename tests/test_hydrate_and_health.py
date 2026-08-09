"""Regression: resume hydrate + dashboard health for silent empty-data bugs."""

from __future__ import annotations

import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from lighter_mm.analytics.aggregation import _glob_patterns, analyze_window
from lighter_mm.cloud.dashboard_data import build_dashboard_payload, collector_status_label
from lighter_mm.cloud.sync import DurableSync
from lighter_mm.config import Settings
from lighter_mm.storage.local_backend import LocalStorageBackend
from lighter_mm.storage.parquet_store import ParquetStore, _book_schema
from lighter_mm.storage.state import RunState, now_iso
from tests.helpers import enrich_book_row


def _book_row(ts: int, *, stale: bool = False) -> dict:
    return enrich_book_row({
        "timestamp_ms": ts,
        "market_id": 1,
        "symbol": "ETH",
        "best_bid": 100.0,
        "best_ask": 100.1,
        "mid": 100.05,
        "spread_absolute": 0.1,
        "spread_bps": None if stale else 10.0,
        "best_bid_size_base": 1.0,
        "best_ask_size_base": 1.0,
        "best_bid_size_usd": 100.0,
        "best_ask_size_usd": 100.1,
        "is_stale": stale,
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
        "bid_depth_5bps_usd": 0.0 if stale else 50.0,
        "ask_depth_5bps_usd": 0.0 if stale else 50.0,
        "two_sided_depth_5bps_usd": 0.0 if stale else 50.0,
        "bid_depth_10bps_usd": 0.0 if stale else 200.0,
        "ask_depth_10bps_usd": 0.0 if stale else 200.0,
        "two_sided_depth_10bps_usd": 0.0 if stale else 200.0,
        "bid_depth_25bps_usd": 0.0 if stale else 400.0,
        "ask_depth_25bps_usd": 0.0 if stale else 400.0,
        "two_sided_depth_25bps_usd": 0.0 if stale else 400.0,
    })


def test_remote_books_hydrate_to_local_book_samples(tmp_path: Path) -> None:
    be = LocalStorageBackend(tmp_path)
    hot = tmp_path / "hot"
    hot.mkdir()
    sync = DurableSync(be, run_id="run1", gcs_prefix="lighter-mm")

    # Simulate prior upload: durable books/ (not book_samples/)
    src = tmp_path / "part.parquet"
    schema = _book_schema([5, 10, 25])
    table = pa.Table.from_pylist(
        [_book_row(int(time.time() * 1000))],
        schema=schema,
    )
    pq.write_table(table, src)
    remote = "lighter-mm/runs/run1/books/date=2026-08-09/hour=10/part.parquet"
    be.upload_file(src, remote)

    restored = sync.hydrate_run_parquets(hot)
    assert restored == [remote]
    local = hot / "book_samples/date=2026-08-09/hour=10/part.parquet"
    assert local.exists()
    assert local.stat().st_size > 0
    # Second hydrate is a no-op and marks file as already uploaded.
    assert sync.hydrate_run_parquets(hot) == []
    assert str(local) in sync._uploaded


def test_public_key_uses_configured_public_prefix(tmp_path: Path) -> None:
    be = LocalStorageBackend(tmp_path)
    sync = DurableSync(
        be,
        run_id="run1",
        gcs_prefix="lighter-mm",
        public_prefix="custom/public",
    )
    assert sync.public_key("latest.json") == "custom/public/latest.json"


def test_degraded_when_ws_degraded() -> None:
    from datetime import UTC, datetime

    from lighter_mm.cloud.dashboard_data import build_collector_status_payload

    recent = datetime.now(UTC).isoformat()
    state = RunState(
        run_id="r1",
        started_at=recent,
        status="running",
        last_successful_flush=recent,
        samples_written=100,
        markets=[1, 2, 3],
    )
    payload = build_collector_status_payload(
        state,
        settings=Settings(),
        ws_runtime={"connected_shards": 0, "total_shards": 5, "planned_channels": 10, "acked_channels": 5},
    )
    assert payload["status"] == "DEGRADED"


def test_collecting_on_cold_start_without_samples() -> None:
    from datetime import UTC, datetime

    state = RunState(
        run_id="r1",
        started_at=now_iso(),
        status="running",
        last_successful_flush=now_iso(),
        last_durable_event_ms=int(datetime.now(UTC).timestamp() * 1000) - 60_000,
        samples_written=0,
        markets=[1, 2, 3],
    )
    assert (
        collector_status_label(
            state,
            ok_minutes=20,
            warn_minutes=40,
        )
        == "COLLECTING"
    )


def test_dashboard_payload_surfaces_analysis_error(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", reports_dir=tmp_path / "reports")
    (settings.data_dir / "book_samples").mkdir(parents=True)
    state = RunState(
        run_id="r1",
        started_at=now_iso(),
        status="running",
        last_successful_flush=now_iso(),
        samples_written=50,
        markets=[1],
    )
    payload = build_dashboard_payload(settings, hours=1, state=state)
    assert payload["latest"]["status"] == "ERROR"
    assert payload["latest"]["analysis_error"]
    assert payload["latest"]["markets_discovered"] == 1
    assert payload["latest"]["markets_analyzed"] == 0
    assert payload["latest"]["top_candidate"] is None


def test_glob_patterns_includes_flat_and_nested(tmp_path: Path) -> None:
    root = tmp_path / "book_samples"
    nested = root / "date=2026-08-09" / "hour=10"
    nested.mkdir(parents=True)
    (nested / "part-n.parquet").write_bytes(b"x")
    flat = root / "date=2026-08-08"
    flat.mkdir(parents=True)
    (flat / "part-f.parquet").write_bytes(b"y")
    patterns = _glob_patterns(root)
    assert any(p.endswith("date=*/hour=*/*.parquet") for p in patterns)
    assert any(p.endswith("date=*/*.parquet") for p in patterns)


def test_legacy_stale_with_mid_counts_toward_coverage(tmp_path: Path) -> None:
    """Legacy rows: is_stale=true but mid present → valid observation for coverage."""
    settings = Settings(data_dir=tmp_path, reports_dir=tmp_path / "reports")
    store = ParquetStore(
        tmp_path, depth_levels=[5, 10, 25], flush_rows=1, flush_seconds=60
    )
    ts = int(time.time() * 1000)
    store.write_book(_book_row(ts, stale=True))
    store.close()
    result = analyze_window(settings, hours=1.0)
    assert result["markets"]
    assert result["markets"][0]["data_coverage_pct"] >= 99.0
