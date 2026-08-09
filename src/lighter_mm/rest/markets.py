"""REST market discovery via official orderBooks endpoint."""

from __future__ import annotations

import logging
from collections.abc import Callable

import httpx

from lighter_mm.config import Settings
from lighter_mm.models import MarketMeta, MarketStatus, MarketType

log = logging.getLogger(__name__)


class MarketDiscovery:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client
        self.markets: dict[int, MarketMeta] = {}

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.settings.rest_base_url,
                timeout=30.0,
                headers={"User-Agent": "lighter-mm-scanner/0.1 (read-only research)"},
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_perp_markets(self, *, active_only: bool = True) -> list[MarketMeta]:
        client = await self._http()
        resp = await client.get("/api/v1/orderBooks", params={"filter": "perp"})
        resp.raise_for_status()
        payload = resp.json()
        if int(payload.get("code", 0)) not in (0, 200):
            raise RuntimeError(f"orderBooks failed: {payload}")
        out: list[MarketMeta] = []
        for raw in payload.get("order_books") or []:
            meta = MarketMeta.from_api(raw)
            if meta.market_type != MarketType.PERP:
                continue
            if active_only and meta.status != MarketStatus.ACTIVE:
                continue
            out.append(meta)
        out.sort(key=lambda m: m.market_id)
        return out

    async def refresh(
        self,
        on_added: Callable[[MarketMeta], None] | None = None,
        on_removed: Callable[[MarketMeta], None] | None = None,
    ) -> tuple[list[MarketMeta], list[MarketMeta]]:
        latest = await self.fetch_perp_markets(active_only=True)
        latest_map = {m.market_id: m for m in latest}
        added = [m for mid, m in latest_map.items() if mid not in self.markets]
        removed = [m for mid, m in self.markets.items() if mid not in latest_map]
        self.markets = latest_map
        for m in added:
            log.info("market added: %s (%s)", m.symbol, m.market_id)
            if on_added:
                on_added(m)
        for m in removed:
            log.info("market removed: %s (%s)", m.symbol, m.market_id)
            if on_removed:
                on_removed(m)
        return added, removed
