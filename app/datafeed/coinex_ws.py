from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
import zlib
from typing import Awaitable, Callable, Optional

import websockets


logger = logging.getLogger(__name__)


class CoinExWS:
    WS_URL = "wss://socket.coinex.com/v2/spot"

    def __init__(self) -> None:
        # Configurable via env
        self.ping_seconds = int(os.getenv("WS_PING_SECONDS", "25"))
        self.read_timeout = int(os.getenv("WS_READ_TIMEOUT_SECONDS", "30"))
        self.backoff_max = int(os.getenv("WS_BACKOFF_MAX_SECONDS", "30"))

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._hb_task: Optional[asyncio.Task] = None
        self._last_rx: float = 0.0
        self._subscriptions: list[dict] = []

    async def subscribe_ticker(self, market: str, on_price: Callable[[float], Awaitable[None]]) -> None:
        # Deterministic subscription set => idempotent resubscribe
        self._subscriptions = [
            {"method": "subscribe", "params": {"channel": "spot/ticker", "market": market}, "id": 1},
        ]
        backoff = 1
        attempt = 0
        while True:
            attempt += 1
            try:
                logger.info("WS connecting to %s (attempt %s)", self.WS_URL, attempt)
                async with websockets.connect(
                    self.WS_URL,
                    ping_interval=None,  # app-level ping only
                    compression="deflate",
                    max_queue=4096,
                    max_size=None,
                    close_timeout=5,
                    open_timeout=10,
                ) as ws:
                    self._ws = ws
                    self._last_rx = time.monotonic()
                    logger.info("WS connected")

                    # Resubscribe once per connection
                    for sub in self._subscriptions:
                        await ws.send(json.dumps(sub))
                    logger.info("WS subscribed %s", len(self._subscriptions))

                    # Heartbeat task
                    self._hb_task = asyncio.create_task(self._pinger())

                    # Read loop with timeout + idle reconnect
                    while True:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=self.read_timeout)
                        except asyncio.TimeoutError:
                            if time.monotonic() - self._last_rx > 60:
                                raise ConnectionError("inactivity > 60s")
                            await self._send_ping()
                            continue

                        self._last_rx = time.monotonic()
                        await self._handle_message(msg, on_price)

                logger.warning("WS disconnected: context exit")
            except Exception as e:
                logger.warning("WS disconnected: %s", e)
            finally:
                if self._hb_task:
                    self._hb_task.cancel()
                    self._hb_task = None
                if self._ws is not None:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                    self._ws = None

            # Exponential backoff with jitter
            sleep_s = min(self.backoff_max, backoff) + random.uniform(0, 0.5)
            logger.info("WS reconnecting in %.1fs", sleep_s)
            await asyncio.sleep(sleep_s)
            backoff = min(self.backoff_max, backoff * 2) if backoff > 0 else 1

    async def _pinger(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.ping_seconds + random.uniform(0, 5))
                await self._send_ping()
            except asyncio.CancelledError:
                return
            except Exception:
                return

    async def _send_ping(self) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"method": "server.ping", "params": {}, "id": 999}))
            logger.debug("WS ping sent")
        except Exception:
            pass

    async def _send_pong(self, ts) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"method": "server.pong", "params": {"ts": ts}, "id": 999}))
        except Exception:
            pass

    def _decode(self, raw) -> Optional[dict]:
        text: Optional[str] = None
        if isinstance(raw, (bytes, bytearray)):
            # Try raw DEFLATE (RFC7692) first then default
            for wbits in (-zlib.MAX_WBITS, zlib.MAX_WBITS):
                try:
                    text = zlib.decompress(raw, wbits).decode("utf-8")
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
            logger.debug("WS JSON decode failed: %s", (text[:200] + "...") if isinstance(text, str) else type(text))
            return None

    async def _handle_message(self, raw, on_price: Callable[[float], Awaitable[None]]) -> None:
        data = self._decode(raw)
        if not isinstance(data, dict):
            return
        # Heartbeats
        if data.get("method") == "server.ping" or data.get("ping") is not None:
            await self._send_pong(data.get("ts"))
            return
        if data.get("method") == "server.pong" or data.get("pong") is not None:
            logger.debug("WS pong ok")
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
                # Non-blocking: schedule the callback
                asyncio.create_task(on_price(float(price)))
            except Exception as e:
                logger.exception("on_price scheduling error: %s", e)