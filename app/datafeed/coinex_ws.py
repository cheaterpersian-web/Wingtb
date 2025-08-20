from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

import websockets


logger = logging.getLogger(__name__)


class CoinExWS:
    # Try v2 first; if fails, reconnect using v1 path
    WS_URLS = ["wss://socket.coinex.com/v2/spot", "wss://socket.coinex.com/v1/spot"]

    def __init__(self) -> None:
        self._ws = None
        self._lock = asyncio.Lock()

    async def subscribe_ticker(self, market: str, on_price: Callable[[float], Awaitable[None]]) -> None:
        url_idx = 0
        while True:
            url = self.WS_URLS[url_idx % len(self.WS_URLS)]
            try:
                async with websockets.connect(url, max_queue=1024) as ws:
                    self._ws = ws
                    await self._send_sub(ws, market)
                    async for msg in ws:
                        await self._handle_message(msg, on_price)
            except Exception as e:
                logger.warning("WS disconnected from %s: %s, switching endpoint", url, e)
                url_idx += 1
                await asyncio.sleep(1.0)

    async def _send_sub(self, ws, market: str) -> None:
        # CoinEx v2 likely expects JSON with method/params; adjust channel name if needed.
        sub = {
            "method": "subscribe",
            "params": {"channel": "ticker", "market": market},
            "id": 1,
        }
        await ws.send(json.dumps(sub))

    async def _handle_message(self, raw: str, on_price: Callable[[float], Awaitable[None]]) -> None:
        try:
            data = json.loads(raw)
        except Exception:
            return
        if isinstance(data, dict):
            if data.get("ping"):
                await self._pong(data.get("ping"))
                return
            d = data.get("data") or {}
            price = d.get("last") or d.get("price")
            if price is not None:
                try:
                    await on_price(float(price))
                except Exception as e:
                    logger.exception("on_price callback error: %s", e)

    async def _pong(self, ts) -> None:
        if self._ws is None:
            return
        await self._ws.send(json.dumps({"method": "pong", "params": {"ts": ts}}))

