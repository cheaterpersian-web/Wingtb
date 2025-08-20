import json
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .coinex_client import CoinexClient
from .strategies.base import Candle, LiveStrategy, Signal


@dataclass
class Position:
	qty: float = 0.0
	avg_price: float = 0.0


@dataclass
class TradeFill:
	timestamp_ms: int
	action: str
	price: float
	qty: float
	pnl: float
	balance_after: float


@dataclass
class PaperState:
	market: str
	period: str
	balance_usdt: float
	position: Position = field(default_factory=Position)
	trade_log: List[TradeFill] = field(default_factory=list)


class PaperEngine:
	"""Live paper-trading engine that polls CoinEx klines and executes signals."""

	def __init__(self, client: CoinexClient, market: str, period: str, balance_usdt: float, strategy: LiveStrategy, poll_sec: int = 5) -> None:
		self.client = client
		self.market = market
		self.period = period
		self.state = PaperState(market=market, period=period, balance_usdt=balance_usdt)
		self.strategy = strategy
		self.poll_sec = poll_sec
		self._stop = threading.Event()
		self._thread: Optional[threading.Thread] = None
		self._last_candle_ts: Optional[int] = None

	def start(self) -> None:
		if self._thread and self._thread.is_alive():
			return
		self._stop.clear()
		self._thread = threading.Thread(target=self._run_loop, daemon=True)
		self._thread.start()

	def stop(self) -> None:
		self._stop.set()
		if self._thread:
			self._thread.join(timeout=5)

	def snapshot(self) -> Dict:
		p = self.state.position
		return {
			"market": self.market,
			"period": self.period,
			"balance_usdt": self.state.balance_usdt,
			"position": {"qty": p.qty, "avg_price": p.avg_price},
			"trades": len(self.state.trade_log),
		}

	def _run_loop(self) -> None:
		while not self._stop.is_set():
			try:
				rows = self.client.get_kline(self.market, self.period, limit=2)
				if not rows:
					time.sleep(self.poll_sec)
					continue
				last = rows[-1]
				candle = Candle(
					created_at_ms=int(last.get("created_at")),
					open=float(last.get("open")),
					high=float(last.get("high")),
					low=float(last.get("low")),
					close=float(last.get("close")),
					volume=float(last.get("volume")),
				)
				if self._last_candle_ts == candle.created_at_ms:
					time.sleep(self.poll_sec)
					continue
				self._last_candle_ts = candle.created_at_ms
				sig = self.strategy.on_candle(candle)
				self._execute_signal(candle, sig)
			except Exception:
				# keep running despite transient errors
				time.sleep(self.poll_sec)

	def _execute_signal(self, candle: Candle, sig: Signal) -> None:
		price = candle.close
		if sig.action == "buy":
			self._buy(price, candle.created_at_ms)
		elif sig.action == "sell":
			self._sell(price, candle.created_at_ms)

	def _buy(self, price: float, ts: int) -> None:
		# simple sizing: 1% of balance per buy
		size_usdt = max(0.0, self.state.balance_usdt * 0.01)
		if size_usdt < 1e-6:
			return
		qty = size_usdt / price
		p = self.state.position
		new_qty = p.qty + qty
		p.avg_price = (p.avg_price * p.qty + price * qty) / new_qty if new_qty > 0 else 0.0
		p.qty = new_qty
		self.state.balance_usdt -= size_usdt
		self.state.trade_log.append(TradeFill(ts, "buy", price, qty, 0.0, self.state.balance_usdt))

	def _sell(self, price: float, ts: int) -> None:
		p = self.state.position
		if p.qty <= 0:
			return
		qty = p.qty * 0.5  # take half profits per sell signal
		proceeds = qty * price
		pnl = qty * (price - p.avg_price)
		p.qty -= qty
		self.state.balance_usdt += proceeds
		self.state.trade_log.append(TradeFill(ts, "sell", price, qty, pnl, self.state.balance_usdt))

