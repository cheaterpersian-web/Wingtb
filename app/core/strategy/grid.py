from dataclasses import dataclass
from typing import List, Optional


@dataclass
class GridConfig:
    lower_price: float
    upper_price: float
    grid_count: int
    step_type: str  # "percent" | "fixed"
    base_order_usdt: float
    max_position_usdt: float
    fee_bps: float
    slippage_bps: float
    use_rsi_filter: bool = False
    use_ema_filter: bool = False


@dataclass
class Intent:
    side: str  # "buy" | "sell"
    qty: float
    reason: str


class GridStrategy:
    def __init__(self, cfg: GridConfig) -> None:
        self.cfg = cfg
        self.levels: List[float] = []
        self._build_levels()
        self._last_price: Optional[float] = None
        self._inv_fifo: List[float] = []

    def _build_levels(self) -> None:
        if self.cfg.grid_count <= 1:
            self.levels = [self.cfg.lower_price, self.cfg.upper_price]
            return
        if self.cfg.step_type == "percent":
            # uniform in percent over bounds
            low, high = self.cfg.lower_price, self.cfg.upper_price
            step = (high / low) ** (1.0 / (self.cfg.grid_count - 1))
            p = low
            self.levels = []
            for _ in range(self.cfg.grid_count):
                self.levels.append(p)
                p *= step
        else:
            low, high = self.cfg.lower_price, self.cfg.upper_price
            step = (high - low) / (self.cfg.grid_count - 1)
            self.levels = [low + i * step for i in range(self.cfg.grid_count)]

    def on_tick(self, price: float, rsi_val: Optional[float] = None, ema_fast_ok: Optional[bool] = None, ema_slow_ok: Optional[bool] = None) -> Optional[Intent]:
        # Cross logic: if price crosses below a level => buy; above => sell
        if self._last_price is None:
            self._last_price = price
            return None
        prev = self._last_price
        self._last_price = price
        # Filters
        def pass_buy() -> bool:
            ok = True
            if self.cfg.use_rsi_filter and rsi_val is not None:
                ok = ok and (rsi_val < 30.0)
            if self.cfg.use_ema_filter and ema_fast_ok is not None and ema_slow_ok is not None:
                ok = ok and (not ema_fast_ok and ema_slow_ok)
            return ok

        def pass_sell() -> bool:
            ok = True
            if self.cfg.use_rsi_filter and rsi_val is not None:
                ok = ok and (rsi_val > 70.0)
            if self.cfg.use_ema_filter and ema_fast_ok is not None and ema_slow_ok is not None:
                ok = ok and (ema_fast_ok and not ema_slow_ok)
            return ok

        # Decide level cross
        for lvl in self.levels:
            if prev > lvl >= price and pass_buy():
                qty = self.cfg.base_order_usdt / max(price, 1e-12)
                self._inv_fifo.append(qty)
                return Intent(side="buy", qty=qty, reason=f"cross_down@{lvl}")
            if prev < lvl <= price and self._inv_fifo and pass_sell():
                qty = self._inv_fifo.pop(0)
                return Intent(side="sell", qty=qty, reason=f"cross_up@{lvl}")
        return None

