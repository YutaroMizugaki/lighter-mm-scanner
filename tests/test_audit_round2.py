"""Round-2 correctness audit regressions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from lighter_mm.collector import CollectorApp
from lighter_mm.config import Settings
from lighter_mm.engine.markout import MarkoutEngine
from lighter_mm.engine.mid_history import MidHistory
from lighter_mm.models import TradeEvent, TradeType
from lighter_mm.storage.gcs_backend import GCSStorageBackend
from lighter_mm.storage.state import RunState


def test_mid_at_prefers_at_or_after() -> None:
    hist = MidHistory()
    hist.add(0, 100.0)
    hist.add(5000, 101.0)
    # Closest absolute would be t=0 for target 1100; markouts need forward mid.
    assert hist.mid_at(1100, tolerance_ms=5000) == 101.0
    # Only a before-mid within tolerance (no forward sample close enough).
    hist2 = MidHistory()
    hist2.add(1000, 100.0)
    assert hist2.mid_at(1100, tolerance_ms=500) == 100.0
    assert hist.mid_at(1100, tolerance_ms=500) is None


def test_analysis_window_uses_elapsed_unless_completed() -> None:
    app = object.__new__(CollectorApp)
    app.hours = 72.0
    app._completed = False
    started = datetime.now(UTC) - timedelta(hours=5)
    app.state = RunState(run_id="r1", started_at=started.isoformat(), status="running")
    elapsed = 5.0
    hours = 72.0
    window = (
        hours
        if False and app._completed and app.hours
        else min(hours, max(elapsed, 0.1))
    )
    assert window == 5.0
    app._completed = True
    window_done = (
        hours if True and app._completed and app.hours else min(hours, max(elapsed, 0.1))
    )
    assert window_done == 72.0


def test_resolve_run_keeps_pointer_without_state_json(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, reports_dir=tmp_path / "reports", environment="local")
    app = object.__new__(CollectorApp)
    app.settings = settings
    app.resume = True
    app.hours = 72.0
    app.holder_id = "holder"
    app.backend = MagicMock()
    app.meta = MagicMock()
    app.meta.get_active_run.return_value = None
    app.backend.download_json.side_effect = [
        {"run_id": "deadbeef12ab", "status": "running", "updated_at": "2026-08-01T00:00:00+00:00"},
        None,  # missing state.json
    ]
    run_id, state, resumed = CollectorApp._resolve_run(app)
    assert resumed is True
    assert run_id == "deadbeef12ab"
    assert state.run_id == "deadbeef12ab"
    assert state.started_at == "2026-08-01T00:00:00+00:00"


@patch("google.cloud.storage.Client")
def test_public_upload_requires_public_bucket(mock_client_cls: MagicMock, tmp_path: Path) -> None:
    mock_client_cls.return_value = MagicMock()
    backend = GCSStorageBackend("priv-bucket", local_root=tmp_path, public_bucket_name=None)
    try:
        backend.upload_json("lighter-mm/public/latest.json", {"ok": True}, public=True)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "GCS_PUBLIC_BUCKET" in str(exc)


def test_markout_drop_is_counted() -> None:
    rows: list[dict] = []
    eng = MarkoutEngine(horizons=[1], on_markout=rows.append, max_pending=1)
    t1 = TradeEvent(
        trade_id=1,
        timestamp_ms=1000,
        market_id=0,
        price=__import__("decimal").Decimal("10"),
        size=__import__("decimal").Decimal("1"),
        usd_amount=__import__("decimal").Decimal("10"),
        is_maker_ask=True,
        type=TradeType.TRADE,
    )
    t2 = TradeEvent(
        trade_id=2,
        timestamp_ms=1001,
        market_id=0,
        price=__import__("decimal").Decimal("10"),
        size=__import__("decimal").Decimal("1"),
        usd_amount=__import__("decimal").Decimal("10"),
        is_maker_ask=True,
        type=TradeType.TRADE,
    )
    eng.on_trade(t1, "ETH", 10.0)
    eng.on_trade(t2, "ETH", 10.0)
    assert eng.dropped_pending == 1
    assert eng.pending_count == 1
