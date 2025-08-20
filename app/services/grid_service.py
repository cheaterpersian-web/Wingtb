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
        self._closes: deque[float] = deque(maxlen=500)
        self._fast_ema_series: list[float] | None = None
        self._slow_ema_series: list[float] | None = None
        self._rsi_series: list[float] | None = None

    async def warmup_indicators(self) -> None:
        candles = await self.datafeed.get_klines(self.cfg.pair, self.cfg.timeframe, 200)
        closes = [float(c.get("close") or c.get("c") or 0.0) for c in candles]
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

        async def on_price(price: float) -> None:
            await self.exec.on_price(price)
            self._closes.append(price)
            self._fast_ema_series = compute_ema(list(self._closes), 12)
            self._slow_ema_series = compute_ema(list(self._closes), 26)
            self._rsi_series = compute_rsi(list(self._closes), 14)
            fast_ema = self._fast_ema_series[-1] if self._fast_ema_series else None
            slow_ema = self._slow_ema_series[-1] if self._slow_ema_series else None
            rsi = self._rsi_series[-1] if self._rsi_series else None
            intents = self.strategy.on_tick(price, rsi=rsi, fast_ema=fast_ema, slow_ema=slow_ema)
            for intent in intents:
                await self.exec.place_order(self.cfg.pair, intent.side, intent.qty, price)

        async def runner():
            try:
                await self.datafeed.subscribe_ticker(self.cfg.pair, on_price)
            finally:
                self._running = False

        self._task = asyncio.create_task(runner())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(Exception):
                await self._task
            self._task = None

    async def reconfigure(self, new_cfg: ServiceConfig) -> None:
        await self.stop()
        self.cfg = new_cfg
        self.strategy = GridStrategy(
            GridParams(
                lower_price=new_cfg.lower_price,
                upper_price=new_cfg.upper_price,
                grid_count=new_cfg.grid_count,
                step_type=new_cfg.step_type,
                base_order_usdt=new_cfg.base_order_usdt,
                use_rsi_filter=new_cfg.use_rsi_filter,
                use_ema_filter=new_cfg.use_ema_filter,
            )
        )
        await self.start()

