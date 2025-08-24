from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List
import logging

from app.datafeed.adapters import IDataFeed
from app.datafeed.coinex_rest import CoinExREST
from app.datafeed.coinex_ws import CoinExWS


class CoinExDataFeed(IDataFeed):
    def __init__(self) -> None:
        self._rest = CoinExREST()
        self._ws = CoinExWS()
        self._log = logging.getLogger("coinex_feed")

    async def get_klines(self, pair: str, tf: str, limit: int) -> List[Dict[str, Any]]:
        self._log.info("REST get_klines: pair=%s tf=%s limit=%d", pair, tf, limit)
        return await self._rest.get_klines(pair, tf, limit)

    async def subscribe_ticker(self, pair: str, on_price: Callable[[float], Awaitable[None]]) -> None:
        self._log.info("WS subscribe_ticker: pair=%s", pair)
        await self._ws.subscribe_ticker(pair, on_price)

    async def now_price(self, pair: str) -> float:
        self._log.info("REST now_price: pair=%s", pair)
        return await self._rest.get_ticker(pair)

