from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx


logger = logging.getLogger(__name__)


def _map_tf_to_v1(tf: str) -> str:
    mapping = {
        "1m": "1min",
        "3m": "3min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1hour",
        "2h": "2hour",
        "4h": "4hour",
        "6h": "6hour",
        "12h": "12hour",
        "1d": "1day",
        "3d": "3day",
        "1w": "1week",
    }
    return mapping.get(tf, tf)


class CoinExREST:
    BASE_URLS = ["https://api.coinex.com/v2", "https://api.coinex.com/v1"]

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=10, follow_redirects=True)

    async def _request(self, base_url: str, path: str, params: dict) -> httpx.Response:
        url = base_url + path
        return await self._client.get(url, params=params)

    def _extract_candles(self, payload: dict) -> List[Dict[str, Any]]:
        # Try common shapes
        if isinstance(payload, list):
            return payload
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("list", "klines", "candles"):
                if isinstance(data.get(key), list):
                    return data[key]
        return []

    async def get_klines(self, market: str, interval: str, limit: int = 200) -> List[Dict[str, Any]]:
        # Try v2 official spot path first, then other guesses, then v1 fallback
        v2_paths = ("/spot/kline", "/market/kline", "/market/candlestick")
        v1_paths = ("/market/kline",)
        # Try v2
        for path in v2_paths:
            if path.startswith("/spot/"):
                params = {"market": market, "period": _map_tf_to_v1(interval), "limit": limit}
            else:
                params = {"market": market, "interval": interval, "limit": limit}
            for attempt in range(2):
                try:
                    r = await self._request(self.BASE_URLS[0], path, params)
                    if r.status_code == 404:
                        break
                    r.raise_for_status()
                    return self._extract_candles(r.json())
                except Exception as e:
                    backoff = 1 + attempt
                    logger.warning("v2 %s failed: %s (attempt %s)", path, e, attempt + 1)
                    await asyncio.sleep(backoff)
        # Fallback v1
        for path in v1_paths:
            params = {"market": market, "type": _map_tf_to_v1(interval), "limit": limit}
            for attempt in range(2):
                try:
                    r = await self._request(self.BASE_URLS[1], path, params)
                    r.raise_for_status()
                    return self._extract_candles(r.json())
                except Exception as e:
                    backoff = 1 + attempt
                    logger.warning("v1 %s failed: %s (attempt %s)", path, e, attempt + 1)
                    await asyncio.sleep(backoff)
        return []

    def _extract_price(self, payload: dict) -> Optional[float]:
        # Flexible extraction of last price
        data = payload.get("data", payload)
        if isinstance(data, dict):
            for key in ("last", "price", "close", "c"):
                v = data.get(key)
                if v is not None:
                    try:
                        return float(v)
                    except Exception:
                        pass
            # some responses nest ticker
            t = data.get("ticker")
            if isinstance(t, dict):
                v = t.get("last") or t.get("price")
                if v is not None:
                    try:
                        return float(v)
                    except Exception:
                        pass
        return None

    async def get_ticker(self, market: str) -> float:
        paths = ("/spot/ticker", "/market/ticker", "/market/ticker/all")
        params = {"market": market}
        # Try v2 then v1
        for base in self.BASE_URLS:
            for path in paths:
                try:
                    r = await self._request(base, path, params)
                    r.raise_for_status()
                    price = self._extract_price(r.json())
                    if price is not None:
                        return price
                except Exception as e:
                    logger.debug("ticker fetch failed on %s%s: %s", base, path, e)
        return 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

