"""Dashboard markets.json fetch must not collapse HTTP errors to []."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_TS = ROOT / "dashboard" / "lib" / "data.ts"
MARKETS_PAGE = ROOT / "dashboard" / "app" / "markets" / "page.tsx"
HOME_PAGE = ROOT / "dashboard" / "app" / "page.tsx"


def test_get_markets_result_exported() -> None:
    src = DATA_TS.read_text(encoding="utf-8")
    assert "export async function getMarketsResult()" in src
    assert 'fetchJson<{ markets: MarketRow[] }>("markets.json")' in src


def test_markets_page_surfaces_fetch_failure() -> None:
    src = MARKETS_PAGE.read_text(encoding="utf-8")
    assert "getMarketsResult" in src
    assert "Failed to load markets.json" in src
    # Must not use the swallow-to-[] helper as the sole data path.
    assert "getMarkets()" not in src


def test_home_page_detects_markets_fetch_failure() -> None:
    src = HOME_PAGE.read_text(encoding="utf-8")
    assert "getMarketsResult" in src
    assert "Market aggregate data could not be loaded." in src


def test_effective_status_uses_flush_not_generated_at() -> None:
    src = DATA_TS.read_text(encoding="utf-8")
    assert "last_successful_flush" in src
    assert "Collector sync has not succeeded for >40m." in src
    assert "Public latest.json has not been refreshed" not in src
