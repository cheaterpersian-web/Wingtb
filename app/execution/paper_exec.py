from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional

from app.core.storage.db import SQLiteRepo, TradeRow, BalanceRow


@dataclass
class OrderResult:
    side: str
    price: float
    qty: float
    fee: float
    pnl_realized: float


class PaperExecutionGateway:
    def __init__(self, repo: SQLiteRepo, start_usdt: float, fee_bps: float, slippage_bps: float) -> None:
        self.repo = repo
        self.usdt_balance: float = start_usdt
        self.asset_qty: float = 0.0
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps
        self.last_price: float = 0.0
        self.realized_pnl: float = 0.0
        self.closed_wins: int = 0
        self.closed_total: int = 0
        self.max_equity: float = start_usdt
        self.max_drawdown: float = 0.0
        self._trade_callback: Optional[Callable[[OrderResult], Awaitable[None]]] = None

    def _slip(self, price: float, side: str) -> float:
        slip = price * (self.slippage_bps / 10000.0)
        return price + slip if side == "BUY" else price - slip

    async def on_price(self, price: float) -> None:
        self.last_price = price
        await self._snapshot()

    async def place_order(self, pair: str, side: str, qty: float, price: float) -> OrderResult:
        ts = int(time.time() * 1000)
        fill_price = self._slip(price, side)
        notional = fill_price * qty
        fee = notional * (self.fee_bps / 10000.0)

        pnl_realized = 0.0
        if side == "BUY":
            cost = notional + fee
            if cost > self.usdt_balance:
                qty = max(self.usdt_balance / (fill_price * (1 + self.fee_bps / 10000.0)), 0.0)
                notional = fill_price * qty
                fee = notional * (self.fee_bps / 10000.0)
                cost = notional + fee
            self.usdt_balance -= cost
            self.asset_qty += qty
        else:
            if qty > self.asset_qty:
                qty = self.asset_qty
                notional = fill_price * qty
                fee = notional * (self.fee_bps / 10000.0)
            proceeds = notional - fee
            # Simple FIFO approximation: assume average price equal to last_price for PnL calc
            pnl_realized = proceeds - (self.last_price * qty)
            self.realized_pnl += pnl_realized
            self.usdt_balance += proceeds
            self.asset_qty -= qty
            self.closed_total += 1
            if pnl_realized > 0:
                self.closed_wins += 1

        await self.repo.insert_trade(
            TradeRow(
                id=None,
                ts=ts,
                pair=pair,
                side=side,
                price=fill_price,
                qty=qty,
                fee=fee,
                pnl_realized=pnl_realized,
            )
        )
        await self._snapshot()
        result = OrderResult(side=side, price=fill_price, qty=qty, fee=fee, pnl_realized=pnl_realized)
        if self._trade_callback is not None:
            try:
                await self._trade_callback(result)
            except Exception:
                pass
        return result

    async def _snapshot(self) -> None:
        equity = self.usdt_balance + self.asset_qty * self.last_price
        if equity > self.max_equity:
            self.max_equity = equity
        dd = 0.0 if self.max_equity == 0 else (self.max_equity - equity)
        if dd > self.max_drawdown:
            self.max_drawdown = dd
        await self.repo.snapshot_balance(
            BalanceRow(
                ts=int(time.time() * 1000),
                usdt=self.usdt_balance,
                asset_qty=self.asset_qty,
                asset_price=self.last_price,
                equity=equity,
            )
        )

    def balances(self) -> Dict[str, float]:
        return {
            "USDT": self.usdt_balance,
            "ASSET_QTY": self.asset_qty,
            "ASSET_PRICE": self.last_price,
            "EQUITY": self.usdt_balance + self.asset_qty * self.last_price,
            "PNL_REALIZED": self.realized_pnl,
            "WIN_RATE": (self.closed_wins / self.closed_total * 100.0) if self.closed_total else 0.0,
            "MAX_DRAWDOWN": self.max_drawdown,
        }

    def set_trade_callback(self, cb: Optional[Callable[[OrderResult], Awaitable[None]]]) -> None:
        self._trade_callback = cb

    def reset(self, start_usdt: float) -> None:
        self.usdt_balance = start_usdt
        self.asset_qty = 0.0
        self.last_price = 0.0
        self.realized_pnl = 0.0
        self.closed_wins = 0
        self.closed_total = 0
        self.max_equity = start_usdt
        self.max_drawdown = 0.0

