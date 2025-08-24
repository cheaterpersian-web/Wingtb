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
    anchor_center: Optional[float] = None
    buy_levels: List[float] | None = None
    sell_levels: List[float] | None = None


@dataclass
class GridParams:
    lower_price: float
    upper_price: float
    grid_count: int  # interpreted as steps PER SIDE
    step_type: str  # percent | fixed
    base_order_usdt: float
    use_rsi_filter: bool
    use_ema_filter: bool


class GridStrategy:
    def __init__(self, params: GridParams) -> None:
        self.params = params
        self.state = GridState()
        
    def _compute_step(self, center: float) -> float:
        p = self.params
        if p.grid_count <= 0:
            return 0.0
        if p.step_type == "fixed":
            span = max(p.upper_price - p.lower_price, 0.0)
            # distribute across both sides
            step = span / max(p.grid_count * 2, 1)
            # clamp to a reasonable bound (~0.2% of price) to ensure activity
            max_step = center * 0.002
            min_step = center * 0.0005
            return max(min(step, max_step), min_step)
        # percent-based step derived from range ratio
        ratio = 0.0
        if p.lower_price > 0 and p.upper_price > 0 and p.upper_price > p.lower_price:
            ratio = (p.upper_price / p.lower_price) ** (1.0 / max(p.grid_count * 2, 1)) - 1.0
        step = max(center * ratio, 0.0)
        # clamp to [0.05%, 0.2%] of price
        max_step = center * 0.002
        min_step = center * 0.0005
        return max(min(step, max_step), min_step)

    def _ensure_levels(self, center: float) -> None:
        if self.state.anchor_center is None:
            self.state.anchor_center = center
            step = self._compute_step(center)
            grid_size = max(self.params.grid_count, 0)
            self.state.buy_levels = [center - (i + 1) * step for i in range(grid_size)]
            self.state.sell_levels = [center + (i + 1) * step for i in range(grid_size)]

    def set_anchor_center(self, center: float) -> None:
        self.state.anchor_center = None
        self._ensure_levels(center)

    def on_tick(self, price: float, rsi: Optional[float] = None, fast_ema: Optional[float] = None, slow_ema: Optional[float] = None) -> List[GridIntent]:
        intents: List[GridIntent] = []
        last = self.state.last_price
        self.state.last_price = price
        if last is None:
            return intents

        # Build symmetric levels around initial center (first observed price)
        self._ensure_levels(price)
        buy_levels = self.state.buy_levels or []
        sell_levels = self.state.sell_levels or []

        crossed_down = [lvl for lvl in buy_levels if price <= lvl < last]
        crossed_up = [lvl for lvl in sell_levels if last < lvl <= price]

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

