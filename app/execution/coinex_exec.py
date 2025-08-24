from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional, Awaitable, Callable, Dict

import ccxt.async_support as ccxt  # type: ignore

from app.core.storage.db import SQLiteRepo, TradeRow, BalanceRow


@dataclass
class OrderResult:
    side: str
    price: float
    qty: float
    fee: float
    pnl_realized: float


class CoinExExecutionGateway:
    """
    Real execution gateway backed by ccxt for CoinEx spot.
    Performs simple market orders with rounding to exchange limits.
    """

    def __init__(self, repo: SQLiteRepo, fee_bps_fallback: float = 10.0) -> None:
        self.repo = repo
        self.fee_bps: float = fee_bps_fallback
        self.last_price: float = 0.0
        self.realized_pnl: float = 0.0
        self.closed_wins: int = 0
        self.closed_total: int = 0
        self.max_equity: float = 0.0
        self.max_drawdown: float = 0.0
        self.usdt_balance: float = 0.0
        self.asset_qty: float = 0.0
        self.position_cost_usdt: float = 0.0
        self._trade_callback: Optional[Callable[[OrderResult], Awaitable[None]]] = None

        api_key = (os.getenv("COINEX_ACCESS_ID") or os.getenv("COINEX_API_KEY") or "").strip()
        api_secret = (os.getenv("COINEX_SECRET_KEY") or os.getenv("COINEX_API_SECRET") or "").strip()
        self._ex = ccxt.coinex({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            # reduce ccxt warnings
            "options": {"defaultType": "spot"},
        })

    async def close(self) -> None:
        try:
            await self._ex.close()
        except Exception:
            pass

    async def on_price(self, price: float) -> None:
        self.last_price = price
        await self._snapshot()

    async def _load_markets(self) -> None:
        try:
            await self._ex.load_markets(reload=False)
        except Exception:
            try:
                await self._ex.load_markets(reload=True)
            except Exception:
                pass

    def _symbol(self, pair: str) -> str:
        # ccxt uses formats like BTC/USDT; convert common ABCUSDT → ABC/USDT
        if "/" in pair:
            return pair
        if pair.endswith("USDT") and len(pair) > 4:
            return pair[:-4] + "/USDT"
        return pair

    def _round_to(self, value: float, step: float) -> float:
        if step <= 0:
            return value
        n = int(value / step)
        return max(step, n * step)

    async def _limits(self, symbol: str) -> Dict[str, float]:
        try:
            markets = getattr(self._ex, 'markets', None) or {}
            m = markets.get(symbol) or {}
            limits = m.get('limits') or {}
            amount_min = float(limits.get('amount', {}).get('min') or 0.0)
            price_min = float(limits.get('price', {}).get('min') or 0.0)
            precision = m.get('precision') or {}
            amount_step = 10 ** (-int(precision.get('amount', 0))) if precision.get('amount') is not None else 0.0
            price_step = 10 ** (-int(precision.get('price', 0))) if precision.get('price') is not None else 0.0
            return {"amount_min": amount_min, "price_min": price_min, "amount_step": amount_step, "price_step": price_step}
        except Exception:
            return {"amount_min": 0.0, "price_min": 0.0, "amount_step": 0.0, "price_step": 0.0}

    async def place_order(self, pair: str, side: str, qty: float, price: float) -> OrderResult:
        await self._load_markets()
        symbol = self._symbol(pair)
        limits = await self._limits(symbol)
        # round qty and price
        qty = max(qty, limits.get("amount_min", 0.0))
        if limits.get("amount_step", 0.0) > 0:
            qty = self._round_to(qty, limits["amount_step"])
        price = max(price, limits.get("price_min", 0.0))
        if limits.get("price_step", 0.0) > 0:
            price = self._round_to(price, limits["price_step"])

        ts = int(time.time() * 1000)
        # Prefer market orders with amount in base currency
        order = None
        try:
            if side.upper() == "BUY":
                order = await self._ex.create_order(symbol, "market", "buy", qty)
            else:
                order = await self._ex.create_order(symbol, "market", "sell", qty)
        except Exception:
            # fallback to limit IOC if market unsupported in some regions
            params = {"timeInForce": "IOC"}
            if side.upper() == "BUY":
                order = await self._ex.create_order(symbol, "limit", "buy", qty, price, params)
            else:
                order = await self._ex.create_order(symbol, "limit", "sell", qty, price, params)

        # Resolve fill price and fee
        fill_price = float(order.get("average") or order.get("price") or price)
        notional = float(fill_price) * float(order.get("amount") or qty)
        # If exchange returns fee, use that; else fallback
        fee_cost = 0.0
        if order.get("fee") and order["fee"].get("cost"):
            fee_cost = float(order["fee"]["cost"])
        else:
            fee_cost = notional * (self.fee_bps / 10000.0)

        # Update balances using fetch_balance for accuracy
        try:
            bal = await self._ex.fetch_balance()
            total_usdt = float(bal.get("total", {}).get("USDT") or 0.0)
            free_usdt = float(bal.get("free", {}).get("USDT") or total_usdt)
            self.usdt_balance = free_usdt
        except Exception:
            pass

        # PnL bookkeeping (approximate; accurate realized pnl requires cost basis tracking)
        realized = 0.0
        traded_qty = float(order.get("amount") or qty)
        if side.upper() == "BUY":
            cost = notional + fee_cost
            self.position_cost_usdt += cost
            self.asset_qty += traded_qty
        else:
            if self.asset_qty > 0 and traded_qty > 0:
                avg_cost = (self.position_cost_usdt / self.asset_qty) if self.asset_qty > 0 else 0.0
                cost_basis_sold = avg_cost * traded_qty
                proceeds = notional - fee_cost
                realized = proceeds - cost_basis_sold
                self.position_cost_usdt -= cost_basis_sold
                self.realized_pnl += realized
                self.asset_qty -= traded_qty
                self.closed_total += 1
                if realized > 0:
                    self.closed_wins += 1

        await self.repo.insert_trade(
            TradeRow(
                id=None,
                ts=ts,
                pair=pair,
                side=side.upper(),
                price=fill_price,
                qty=traded_qty,
                fee=fee_cost,
                pnl_realized=realized,
            )
        )
        await self._snapshot()
        res = OrderResult(side=side.upper(), price=fill_price, qty=traded_qty, fee=fee_cost, pnl_realized=realized)
        if self._trade_callback is not None:
            try:
                await self._trade_callback(res)
            except Exception:
                pass
        return res

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
        avg_cost = (self.position_cost_usdt / self.asset_qty) if self.asset_qty > 0 else 0.0
        unrealized = self.asset_qty * self.last_price - self.position_cost_usdt
        return {
            "USDT": self.usdt_balance,
            "ASSET_QTY": self.asset_qty,
            "ASSET_PRICE": self.last_price,
            "AVG_COST": avg_cost,
            "EQUITY": self.usdt_balance + self.asset_qty * self.last_price,
            "PNL_REALIZED": self.realized_pnl,
            "PNL_UNREALIZED": unrealized,
            "WIN_RATE": (self.closed_wins / self.closed_total * 100.0) if self.closed_total else 0.0,
            "MAX_DRAWDOWN": self.max_drawdown,
        }

    def set_trade_callback(self, cb: Optional[Callable[[OrderResult], Awaitable[None]]]) -> None:
        self._trade_callback = cb

    def reset(self, start_usdt: float) -> None:
        self.usdt_balance = start_usdt
        self.asset_qty = 0.0
        self.position_cost_usdt = 0.0
        self.last_price = 0.0
        self.realized_pnl = 0.0
        self.closed_wins = 0
        self.closed_total = 0
        self.max_equity = start_usdt
        self.max_drawdown = 0.0

