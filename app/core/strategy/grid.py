from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class GridIntent:
    side: str  # "BUY" or "SELL"
    qty: float
    price: float


@dataclass
class GridState:
    last_price: Optional[float] = None
    levels: List[float] | None = None


@dataclass
class GridParams:
    lower_price: float
    upper_price: float
    grid_count: int
    step_type: str  # percent | fixed
    base_order_usdt: float
    use_rsi_filter: bool
    use_ema_filter: bool


class GridStrategy:
    def __init__(self, params: GridParams) -> None:
        self.params = params
        self.state = GridState()
        self._build_levels()

    def _build_levels(self) -> None:
        p = self.params
        if p.grid_count <= 1:
            self.state.levels = [p.lower_price, p.upper_price]
            return
        levels: List[float] = []
        if p.step_type == "percent":
            step = (p.upper_price / p.lower_price) ** (1 / (p.grid_count - 1))
            price = p.lower_price
            for _ in range(p.grid_count):
                levels.append(price)
                price *= step
        else:
            step = (p.upper_price - p.lower_price) / (p.grid_count - 1)
            for i in range(p.grid_count):
                levels.append(p.lower_price + step * i)
        self.state.levels = levels

    def on_tick(self, price: float, rsi: Optional[float] = None, fast_ema: Optional[float] = None, slow_ema: Optional[float] = None) -> List[GridIntent]:
        intents: List[GridIntent] = []
        if not self.state.levels:
            self._build_levels()
        assert self.state.levels is not None
        last = self.state.last_price
        self.state.last_price = price
        if last is None:
            return intents

        crossed_up = [lvl for lvl in self.state.levels if last < lvl <= price]
        crossed_down = [lvl for lvl in self.state.levels if price <= lvl < last]

        def rsi_ok_buy() -> bool:
            return True if not self.params.use_rsi_filter else (rsi is not None and rsi < 30)

        def rsi_ok_sell() -> bool:
            return True if not self.params.use_rsi_filter else (rsi is not None and rsi > 70)

        def ema_ok_buy() -> bool:
            return True if not self.params.use_ema_filter else (fast_ema is not None and slow_ema is not None and fast_ema < slow_ema)

        def ema_ok_sell() -> bool:
            return True if not self.params.use_ema_filter else (fast_ema is not None and slow_ema is not None and fast_ema > slow_ema)

        for _ in crossed_down:
            if rsi_ok_buy() and ema_ok_buy():
                qty = max(self.params.base_order_usdt / price, 0.0)
                if qty > 0:
                    intents.append(GridIntent(side="BUY", qty=qty, price=price))
        for _ in crossed_up:
            if rsi_ok_sell() and ema_ok_sell():
                qty = max(self.params.base_order_usdt / price, 0.0)
                if qty > 0:
                    intents.append(GridIntent(side="SELL", qty=qty, price=price))
        return intents

