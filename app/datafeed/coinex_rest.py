from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

import httpx


logger = logging.getLogger(__name__)


class CoinExREST:
    BASE_URL = "https://api.coinex.com/v2"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(base_url=self.BASE_URL, timeout=10)

    async def get_klines(self, market: str, interval: str, limit: int = 200) -> List[Dict[str, Any]]:
        # Endpoint path according to v2 docs: /market/kline
        # If exact differs, adjust here; wrapped for single point of change.
        params = {
            "market": market,
            "interval": interval,
            "limit": limit,
        }
        for attempt in range(5):
            try:
                r = await self._client.get("/market/kline", params=params)
                r.raise_for_status()
                data = r.json()
                # Expect data like { code: 0, data: { ... list ... } }
                candles = data.get("data") or data
                return candles if isinstance(candles, list) else candles.get("list", [])
            except Exception as e:
                backoff = min(2 ** attempt, 10)
                logger.warning("get_klines error: %s (attempt %s)", e, attempt + 1)
                await asyncio.sleep(backoff)
        return []

    async def get_ticker(self, market: str) -> float:
        params = {"market": market}
        for attempt in range(5):
            try:
                r = await self._client.get("/market/ticker", params=params)
                r.raise_for_status()
                data = r.json()
                d = data.get("data") or {}
                last = d.get("last") or d.get("price")
                return float(last)
            except Exception as e:
                backoff = min(2 ** attempt, 10)
                logger.warning("get_ticker error: %s (attempt %s)", e, attempt + 1)
                await asyncio.sleep(backoff)
        return 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

