"""Dashboard markets.json fetch must not collapse HTTP errors to []."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_TS = ROOT / "dashboard" / "lib" / "data.ts"
API_TS = ROOT / "dashboard" / "lib" / "api.ts"
STATUS_TS = ROOT / "dashboard" / "lib" / "status.ts"
MARKETS_PAGE = ROOT / "dashboard" / "app" / "markets" / "page.tsx"
HOME_PAGE = ROOT / "dashboard" / "app" / "page.tsx"


def test_get_markets_result_exported() -> None:
    src = API_TS.read_text(encoding="utf-8")
    barrel = DATA_TS.read_text(encoding="utf-8")
    assert "export async function getMarketsResult(" in src
    assert "markets.json" in src
    assert "resolveDashboardBundle" in src
    assert "getMarketsResult" in barrel


def test_markets_page_surfaces_fetch_failure() -> None:
    src = MARKETS_PAGE.read_text(encoding="utf-8")
    assert "getMarketsResult" in src
    assert "PublicErrorState" in src
    assert "publicDataUnavailableMessage" in src
    # Must not use the swallow-to-[] helper as the sole data path.
    assert "getMarkets()" not in src


def test_home_page_detects_markets_fetch_failure() -> None:
    src = HOME_PAGE.read_text(encoding="utf-8")
    assert "getMarketsResult" in src
    assert "Market aggregate data could not be loaded." in src


def test_effective_status_uses_durable_event_not_generated_at() -> None:
    src = STATUS_TS.read_text(encoding="utf-8")
    assert "last_durable_event_at" in src
    assert "Market data has not been durably collected for >40m." in src
    assert "Public latest.json has not been refreshed" not in src
