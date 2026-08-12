"""Dashboard fetch paths: candidates.json, cache policy, and failure surfacing."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_TS = ROOT / "dashboard" / "lib" / "data.ts"
API_TS = ROOT / "dashboard" / "lib" / "api.ts"
STATUS_TS = ROOT / "dashboard" / "lib" / "status.ts"
MARKETS_PAGE = ROOT / "dashboard" / "app" / "markets" / "page.tsx"
CANDIDATES_PAGE = ROOT / "dashboard" / "app" / "candidates" / "page.tsx"
HOME_PAGE = ROOT / "dashboard" / "app" / "page.tsx"


def test_get_markets_result_exported() -> None:
    src = API_TS.read_text(encoding="utf-8")
    barrel = DATA_TS.read_text(encoding="utf-8")
    assert "export async function getMarketsResult(" in src
    assert "markets.json" in src
    assert "resolveDashboardBundle" in src
    assert "getMarketsResult" in barrel


def test_get_candidates_result_exported() -> None:
    src = API_TS.read_text(encoding="utf-8")
    barrel = DATA_TS.read_text(encoding="utf-8")
    assert "export async function getCandidatesResult(" in src
    assert "candidates.json" in src
    assert "getCandidatesResult" in barrel


def test_markets_page_surfaces_fetch_failure() -> None:
    src = MARKETS_PAGE.read_text(encoding="utf-8")
    assert "getMarketsResult" in src
    assert "PublicErrorState" in src
    assert "publicDataUnavailableMessage" in src
    # Must not use the swallow-to-[] helper as the sole data path.
    assert "getMarkets()" not in src


def test_home_page_detects_candidates_fetch_failure() -> None:
    src = HOME_PAGE.read_text(encoding="utf-8")
    assert "getCandidatesResult" in src
    assert "getMarketsResult" not in src
    assert "Market aggregate data could not be loaded." in src


def test_home_page_parallel_fetch() -> None:
    src = HOME_PAGE.read_text(encoding="utf-8")
    assert "Promise.all" in src
    assert "resolveDashboardBundle()" in src
    assert "getOverviewResult(bundle)" in src
    assert "getCandidatesResult(bundle)" in src


def test_candidates_page_uses_candidates_json() -> None:
    src = CANDIDATES_PAGE.read_text(encoding="utf-8")
    assert "getCandidatesResult" in src
    assert "getMarketsResult" not in src
    assert ".filter((m) => m.is_candidate)" not in src


def test_effective_status_uses_durable_event_not_generated_at() -> None:
    src = STATUS_TS.read_text(encoding="utf-8")
    assert "last_durable_event_at" in src
    assert "Market data has not been durably collected for >40m." in src
    assert "Public latest.json has not been refreshed" not in src


def test_fetch_policy_generation_cache() -> None:
    src = API_TS.read_text(encoding="utf-8")
    assert 'type FetchPolicy = "live" | "generation"' in src
    assert "revalidate: false" in src
    assert "cache: \"no-store\"" in src
    # generation URLs must not use cache-busting query params
    assert "?t=${Date.now()}" in src
    assert "const live = policy === \"live\"" in src


def test_get_candidates_legacy_first() -> None:
    src = API_TS.read_text(encoding="utf-8")
    assert "export async function getCandidatesResult(" in src
    assert "if (resolved.legacy)" in src
    assert 'fetchJson<{ candidates: MarketRow[] }>("candidates.json", "live")' in src
    assert '`${resolved.prefix}candidates.json`' in src
    assert '"generation"' in src


def test_get_overview_result_accepts_bundle() -> None:
    src = API_TS.read_text(encoding="utf-8")
    assert "export async function getOverviewResult(" in src
    assert "bundle?: DashboardBundle" in src
    assert "const resolved = bundle ?? (await resolveDashboardBundle())" in src


def test_get_markets_result_cache_policy() -> None:
    src = API_TS.read_text(encoding="utf-8")
    assert "export async function getMarketsResult(" in src
    assert '"markets.json", "live"' in src
    assert '`${resolved.prefix}markets.json`' in src


def test_get_market_cache_policy() -> None:
    src = API_TS.read_text(encoding="utf-8")
    assert "export async function getMarket(" in src
    assert '`market/${encoded}.json`, "live"' in src
    assert '"generation"' in src


def test_generation_fetch_does_not_mix_legacy_fallback() -> None:
    src = API_TS.read_text(encoding="utf-8")
    # Legacy mode still reads live root files once.
    assert src.count('fetchJson<Overview>("latest.json", "live")') == 1
    assert src.count('fetchJson<{ markets: MarketRow[] }>("markets.json", "live")') == 1
    assert src.count('fetchJson<{ candidates: MarketRow[] }>("candidates.json", "live")') == 1
    # Generation misses must fail closed — no second live fetch after prefix 404.
    assert "if (result.ok) return result;" not in src


def test_markets_client_null_sort_does_not_coerce_zero() -> None:
    src = (ROOT / "dashboard" / "app" / "markets" / "MarketsClient.tsx").read_text(
        encoding="utf-8"
    )
    assert "maker_markout_5s_median_bps ?? -Infinity" in src
    assert "median_spread_bps ?? -Infinity" in src
    assert "maker_markout_5s_median_bps || 0" not in src
    assert "selected_incomplete" in src
