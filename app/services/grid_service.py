from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional
import contextlib
from collections import deque

import numpy as np

from app.core.indicators.ema import compute_ema
from app.core.indicators.rsi import compute_rsi
from app.core.strategy.grid import GridParams, GridStrategy
from app.core.storage.db import SQLiteRepo
from app.datafeed.adapters import IDataFeed
from app.execution.paper_exec import PaperExecutionGateway


logger = logging.getLogger(__name__)


@dataclass
class ServiceConfig:
    pair: str
    timeframe: str
    fee_bps: float
    slippage_bps: float
    base_order_usdt: float
    lower_price: float
    upper_price: float
    grid_count: int
    step_type: str
    use_rsi_filter: bool
    use_ema_filter: bool


class GridService:
    def __init__(self, datafeed: IDataFeed, repo: SQLiteRepo, exec_gateway: PaperExecutionGateway, cfg: ServiceConfig) -> None:
        self.datafeed = datafeed
        self.repo = repo
        self.exec = exec_gateway
        self.cfg = cfg
        self.strategy = GridStrategy(
            GridParams(
                lower_price=cfg.lower_price,
                upper_price=cfg.upper_price,
                grid_count=cfg.grid_count,
                step_type=cfg.step_type,
                base_order_usdt=cfg.base_order_usdt,
                use_rsi_filter=cfg.use_rsi_filter,
                use_ema_filter=cfg.use_ema_filter,
            )
        )
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._rest_refresh_task: Optional[asyncio.Task] = None
        self._closes: deque[float] = deque(maxlen=500)
        self._fast_ema_series: list[float] | None = None
        self._slow_ema_series: list[float] | None = None
        self._rsi_series: list[float] | None = None

    async def warmup_indicators(self) -> None:
        candles = await self.datafeed.get_klines(self.cfg.pair, self.cfg.timeframe, 200)
        closes: list[float] = []
        for c in candles:
            close_val: float | None = None
            if isinstance(c, dict):
                v = c.get("close") or c.get("c") or c.get("last") or c.get("price")
                if v is not None:
                    try:
                        close_val = float(v)
                    except Exception:
                        close_val = None
            elif isinstance(c, (list, tuple)):
                # CoinEx v1 shape example:
                # [timestamp, open, close, high, low, amount, volume]
                if len(c) >= 3:
                    try:
                        close_val = float(c[2])
                    except Exception:
                        close_val = None
            if close_val is not None:
                closes.append(close_val)
        self._closes.clear()
        for c in closes:
            self._closes.append(c)
        self._fast_ema_series = compute_ema(list(self._closes), 12)
        self._slow_ema_series = compute_ema(list(self._closes), 26)
        self._rsi_series = compute_rsi(list(self._closes), 14)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self.warmup_indicators()
        # Auto-center using last close or current REST price
        try:
            anchor = (self._closes[-1] if self._closes else None) or await self.datafeed.now_price(self.cfg.pair)
            if hasattr(self.strategy, 'set_anchor_center') and anchor:
                self.strategy.set_anchor_center(float(anchor))
        except Exception:
            pass

        async def on_price(price: float) -> None:
            await self.exec.on_price(price)
            self._closes.append(price)
            closes_list = list(self._closes)
            self._fast_ema_series = compute_ema(closes_list, 12)
            self._slow_ema_series = compute_ema(closes_list, 26)
            self._rsi_series = compute_rsi(closes_list, 14)
            fast_ema = self._fast_ema_series[-1] if self._fast_ema_series else None
            slow_ema = self._slow_ema_series[-1] if self._slow_ema_series else None
            rsi = self._rsi_series[-1] if self._rsi_series else None
            intents = self.strategy.on_tick(price, rsi=rsi, fast_ema=fast_ema, slow_ema=slow_ema)
            # if no intents (step too large or price not crossing), emit a heartbeat trade test every ~300 ticks
            if not intents and len(self._closes) % 300 == 0:
                # small nudge: place a tiny buy to confirm engine connectivity (paper only)
                qty = max(1.0 / max(price, 1e-9), 0.0)
                intents = [
                    # comment out SELL to avoid oscillation; acts as liveness probe
                    # GridIntent(side="SELL", qty=qty, price=price),
                    GridIntent(side="BUY", qty=qty, price=price)
                ]
            for intent in intents:
                await self.exec.place_order(self.cfg.pair, intent.side, intent.qty, price)

        async def runner():
            try:
                await self.datafeed.subscribe_ticker(self.cfg.pair, on_price)
            finally:
                self._running = False

        self._task = asyncio.create_task(runner())

        async def rest_refresher():
            # Periodically fetch REST price as a fallback to keep engine updated
            while self._running:
                try:
                    px = await self.datafeed.now_price(self.cfg.pair)
                    if px and px > 0:
                        await self.exec.on_price(px)
                        self._closes.append(px)
                        closes_list = list(self._closes)
                        self._fast_ema_series = compute_ema(closes_list, 12)
                        self._slow_ema_series = compute_ema(closes_list, 26)
                        self._rsi_series = compute_rsi(closes_list, 14)
                        fast_ema = self._fast_ema_series[-1] if self._fast_ema_series else None
                        slow_ema = self._slow_ema_series[-1] if self._slow_ema_series else None
                        rsi = self._rsi_series[-1] if self._rsi_series else None
                        intents = self.strategy.on_tick(px, rsi=rsi, fast_ema=fast_ema, slow_ema=slow_ema)
                        for intent in intents:
                            await self.exec.place_order(self.cfg.pair, intent.side, intent.qty, px)
                except Exception:
                    pass
                await asyncio.sleep(15)

        self._rest_refresh_task = asyncio.create_task(rest_refresher())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(Exception):
                await self._task
            self._task = None
        if self._rest_refresh_task:
            self._rest_refresh_task.cancel()
            with contextlib.suppress(Exception):
                await self._rest_refresh_task
            self._rest_refresh_task = None

    async def reconfigure(self, new_cfg: ServiceConfig) -> None:
        await self.stop()
        self.cfg = new_cfg
        self.strategy = GridStrategy(
            GridParams(
                lower_price=new_cfg.lower_price,
                upper_price=new_cfg.upper_price,
                grid_count=new_cfg.grid_count,  # per side (auto 6)
                step_type=new_cfg.step_type,
                base_order_usdt=new_cfg.base_order_usdt,
                use_rsi_filter=new_cfg.use_rsi_filter,
                use_ema_filter=new_cfg.use_ema_filter,
            )
        )
        # recenter on reconfigure
        try:
            anchor = await self.datafeed.now_price(self.cfg.pair)
            if hasattr(self.strategy, 'set_anchor_center') and anchor:
                self.strategy.set_anchor_center(float(anchor))
        except Exception:
            pass
        await self.start()

