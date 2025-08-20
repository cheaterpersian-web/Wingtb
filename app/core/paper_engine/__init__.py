from dataclasses import dataclass
from typing import List, Optional
import time


@dataclass
class Trade:
    ts: int
    pair: str
    side: str
    price: float
    qty: float
    fee: float
    pnl_realized: float


class PaperEngine:
    def __init__(self, start_usdt: float, fee_bps: float, slippage_bps: float, pair: str) -> None:
        self.usdt = start_usdt
        self.pair = pair
        self.asset_qty = 0.0
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps
        self.trades: List[Trade] = []
        self.avg_cost: float = 0.0

    def _apply_slippage(self, price: float, side: str) -> float:
        bps = self.slippage_bps / 10000.0
        return price * (1 + bps) if side == "buy" else price * (1 - bps)

    def _fee(self, notional: float) -> float:
        return notional * (self.fee_bps / 10000.0)

    def on_intent(self, side: str, qty: float, price: float) -> Optional[Trade]:
        px = self._apply_slippage(price, side)
        ts = int(time.time() * 1000)
        if side == "buy":
            cost = px * qty
            fee = self._fee(cost)
            if self.usdt < cost + fee:
                return None
            prev_notional = self.avg_cost * self.asset_qty
            self.asset_qty += qty
            self.avg_cost = (prev_notional + cost + fee) / self.asset_qty if self.asset_qty > 0 else 0.0
            self.usdt -= (cost + fee)
            tr = Trade(ts, self.pair, side, px, qty, fee, 0.0)
            self.trades.append(tr)
            return tr
        else:
            if self.asset_qty < qty:
                qty = self.asset_qty
            if qty <= 0:
                return None
            proceeds = px * qty
            fee = self._fee(proceeds)
            pnl = (px - self.avg_cost) * qty - fee
            self.asset_qty -= qty
            self.usdt += (proceeds - fee)
            if self.asset_qty <= 1e-12:
                self.avg_cost = 0.0
            tr = Trade(ts, self.pair, side, px, qty, fee, pnl)
            self.trades.append(tr)
            return tr

