from __future__ import annotations

from typing import Any, Dict, Optional, Awaitable, Callable


class ExecutionRouter:
    def __init__(self, paper: Any, real: Any) -> None:
        self._paper = paper
        self._real = real
        self._mode: str = "paper"  # or "real"

    def set_mode(self, mode: str) -> None:
        self._mode = "real" if str(mode).lower() == "real" else "paper"

    def get_mode(self) -> str:
        return self._mode

    async def on_price(self, price: float) -> None:
        if self._mode == "real":
            await self._real.on_price(price)
        else:
            await self._paper.on_price(price)

    async def place_order(self, pair: str, side: str, qty: float, price: float):
        if self._mode == "real":
            return await self._real.place_order(pair, side, qty, price)
        return await self._paper.place_order(pair, side, qty, price)

    def balances(self) -> Dict[str, float]:
        b = self._real.balances() if self._mode == "real" else self._paper.balances()
        b["MODE"] = 1.0 if self._mode == "real" else 0.0
        return b

    def reset(self, start_usdt: float) -> None:
        # reset both for simplicity
        if hasattr(self._paper, "reset"):
            self._paper.reset(start_usdt)
        if hasattr(self._real, "reset"):
            self._real.reset(start_usdt)

    # passthrough attributes often used by engine
    @property
    def usdt_balance(self) -> float:
        return self._real.usdt_balance if self._mode == "real" else self._paper.usdt_balance

    @property
    def fee_bps(self) -> float:
        return self._real.fee_bps if self._mode == "real" else self._paper.fee_bps

    @property
    def last_price(self) -> float:
        return self._real.last_price if self._mode == "real" else self._paper.last_price