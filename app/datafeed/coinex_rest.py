import httpx
from typing import List, Dict, Any


class CoinexREST:
    BASE = "https://api.coinex.com/v2"

    def __init__(self, timeout: float = 10.0) -> None:
        self._client = httpx.Client(timeout=timeout)

    def klines(self, market: str, period: str, limit: int = 100) -> List[Dict[str, Any]]:
        r = self._client.get(f"{self.BASE}/spot/kline", params={"market": market, "period": period, "limit": limit})
        r.raise_for_status()
        data = r.json()
        return data.get("data", []) if isinstance(data, dict) else []

    def ticker(self, market: str) -> Dict[str, Any]:
        r = self._client.get(f"{self.BASE}/spot/ticker")
        r.raise_for_status()
        data = r.json()
        arr = data.get("data", []) if isinstance(data, dict) else []
        for it in arr:
            if it.get("market") == market:
                return it
        return {}

