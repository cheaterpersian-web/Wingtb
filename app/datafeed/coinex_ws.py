from __future__ import annotations

import asyncio
import json
import logging
import gzip
import zlib
from typing import Awaitable, Callable, Optional

import websockets


logger = logging.getLogger(__name__)


class CoinExWS:
    # Prefer v2 spot; v1 path returns 404 in some regions, so we don't rotate to v1
    WS_URLS = ["wss://socket.coinex.com/v2/spot"]

    def __init__(self) -> None:
        self._ws = None
        self._hb_task: Optional[asyncio.Task] = None

    async def subscribe_ticker(self, market: str, on_price: Callable[[float], Awaitable[None]]) -> None:
        url_idx = 0
        while True:
            url = self.WS_URLS[url_idx % len(self.WS_URLS)]
            try:
                # Disable low-level ping; CoinEx expects app-level JSON ping/pong
                async with websockets.connect(url, max_queue=2048, ping_interval=None) as ws:
                    self._ws = ws
                    await self._send_sub(ws, market)
                    self._hb_task = asyncio.create_task(self._heartbeat())
                    async for msg in ws:
                        await self._handle_message(msg, on_price)
            except Exception as e:
                logger.warning("WS disconnected from %s: %s, reconnecting", url, e)
                await asyncio.sleep(1.5)
                url_idx += 1
            finally:
                if self._hb_task:
                    self._hb_task.cancel()
                    self._hb_task = None

    async def _heartbeat(self) -> None:
        # Send periodic JSON ping per API; if server requires ts, omit or include
        while True:
            try:
                if self._ws is None:
                    return
                await self._ws.send(json.dumps({"method": "ping"}))
            except Exception:
                return
            await asyncio.sleep(15)

    async def _send_sub(self, ws, market: str) -> None:
        # v2 format: channel "spot/ticker" with market
        payload = {"method": "subscribe", "params": {"channel": "spot/ticker", "market": market}, "id": 1}
        await ws.send(json.dumps(payload))

    def _decode(self, raw) -> Optional[dict]:
        text: Optional[str] = None
        if isinstance(raw, (bytes, bytearray)):
            for fn in (gzip.decompress, zlib.decompress):
                try:
                    text = fn(raw).decode("utf-8")
                    break
                except Exception:
                    continue
            if text is None:
                try:
                    text = raw.decode("utf-8")
                except Exception:
                    return None
        elif isinstance(raw, str):
            text = raw
        else:
            return None
        try:
            return json.loads(text)
        except Exception:
            logger.debug("WS JSON decode failed: %s", text[:200] if isinstance(text, str) else type(text))
            return None

    async def _handle_message(self, raw, on_price: Callable[[float], Awaitable[None]]) -> None:
        data = self._decode(raw)
        if not isinstance(data, dict):
            return
        # heartbeat handling
        if data.get("ping") is not None:
            await self._pong(data.get("ping"))
            return
        if data.get("method") == "ping":
            await self._pong(data.get("ts"))
            return
        d = data.get("data") or data
        price = None
        if isinstance(d, dict):
            price = d.get("last") or d.get("price")
            if price is None and isinstance(d.get("ticker"), dict):
                t = d["ticker"]
                price = t.get("last") or t.get("price")
        if price is not None:
            try:
                await on_price(float(price))
            except Exception as e:
                logger.exception("on_price callback error: %s", e)

    async def _pong(self, ts) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"method": "pong", "params": {"ts": ts}}))
        except Exception:
            pass

