from __future__ import annotations

import abc
from typing import Any, Awaitable, Callable, Dict, List, Optional


class IDataFeed(abc.ABC):
    @abc.abstractmethod
    async def get_klines(self, pair: str, tf: str, limit: int) -> List[Dict[str, Any]]:
        ...

    @abc.abstractmethod
    async def subscribe_ticker(self, pair: str, on_price: Callable[[float], Awaitable[None]]) -> None:
        ...

    @abc.abstractmethod
    async def now_price(self, pair: str) -> float:
        ...

