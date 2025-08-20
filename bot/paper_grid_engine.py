import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

from .coinex_client import CoinexClient


@dataclass
class GridLevel:
	price: float
	qty_base: float
	filled: bool = False


@dataclass
class GridTrade:
	ts_ms: int
	side: str  # buy/sell
	price: float
	qty_base: float
	pnl_usdt: float
	balance_after: float


@dataclass
class GridState:
	market: str
	period: str
	levels_per_side: int
	upper_pct: float
	lower_pct: float
	quote_per_order: float
	balance_usdt: float
	base_qty: float = 0.0
	avg_cost_per_unit: float = 0.0
	buy_levels: List[GridLevel] = field(default_factory=list)
	sell_levels: List[GridLevel] = field(default_factory=list)
	trades: List[GridTrade] = field(default_factory=list)
	# legs created from filled buys, managed for TP/SL and sell execution
	# historical trades already tracked in trades


class PaperGridEngine:
	def __init__(
		self,
		client: CoinexClient,
		market: str,
		period: str = "1hour",
		balance_usdt: float = 200000.0,
		levels_per_side: int = 6,
		upper_pct: float = 0.03,
		lower_pct: float = 0.03,
		quote_per_order: float = 20.0,
		recenter_on_break: bool = True,
		poll_sec: int = 5,
	) -> None:
		self.client = client
		self.market = market
		self.period = period
		self.poll_sec = poll_sec
		self.recenter_on_break = recenter_on_break
		self.state = GridState(
			market=market,
			period=period,
			levels_per_side=levels_per_side,
			upper_pct=upper_pct,
			lower_pct=lower_pct,
			quote_per_order=quote_per_order,
			balance_usdt=balance_usdt,
		)
		self._stop = threading.Event()
		self._thread: Optional[threading.Thread] = None
		self._last_ts: Optional[int] = None
		self._center_price: Optional[float] = None

	def start(self) -> None:
		if self._thread and self._thread.is_alive():
			return
		self._stop.clear()
		self._bootstrap_grid()
		self._thread = threading.Thread(target=self._run_loop, daemon=True)
		self._thread.start()

	def stop(self) -> None:
		self._stop.set()
		if self._thread:
			self._thread.join(timeout=5)

	def snapshot(self) -> dict:
		return {
			"type": "grid",
			"market": self.state.market,
			"period": self.state.period,
			"balance_usdt": self.state.balance_usdt,
			"base_qty": self.state.base_qty,
			"avg_cost": self.state.avg_cost_per_unit,
			"open_buys": sum(1 for l in self.state.buy_levels if not l.filled),
			"open_sells": sum(1 for l in self.state.sell_levels if not l.filled),
			"trades": len(self.state.trades),
		}

	def _bootstrap_grid(self) -> None:
		# center from latest kline close
		rows = self.client.get_kline(self.market, self.period, limit=1)
		if not rows:
			raise RuntimeError("Could not fetch initial price for grid")
		center = float(rows[-1].get("close"))
		self._center_price = center
		self.state.buy_levels = []
		self.state.sell_levels = []
		# build levels
		buy_step = self.state.lower_pct / self.state.levels_per_side
		sell_step = self.state.upper_pct / self.state.levels_per_side
		for i in range(1, self.state.levels_per_side + 1):
			buy_price = center * (1 - buy_step * i)
			qty = self.state.quote_per_order / buy_price
			self.state.buy_levels.append(GridLevel(price=buy_price, qty_base=qty))
			sell_price = center * (1 + sell_step * i)
			qty_s = self.state.quote_per_order / sell_price
			self.state.sell_levels.append(GridLevel(price=sell_price, qty_base=qty_s))

	def _run_loop(self) -> None:
		while not self._stop.is_set():
			try:
				rows = self.client.get_kline(self.market, self.period, limit=1)
				if not rows:
					time.sleep(self.poll_sec)
					continue
				last = rows[-1]
				price = float(last.get("close"))
				ts = int(last.get("created_at"))
				if self._last_ts == ts:
					time.sleep(self.poll_sec)
					continue
				self._last_ts = ts
				self._process(price, ts)
			except Exception:
				time.sleep(self.poll_sec)

	def _process(self, price: float, ts: int) -> None:
		# recenter if price beyond range
		if self.recenter_on_break and self._center_price:
			low_bound = self._center_price * (1 - self.state.lower_pct)
			high_bound = self._center_price * (1 + self.state.upper_pct)
			if price < low_bound or price > high_bound:
				self._bootstrap_grid()
				return

		# fill buys where price <= level
		for lvl in self.state.buy_levels:
			if lvl.filled:
				continue
			if price <= lvl.price and self.state.balance_usdt >= self.state.quote_per_order:
				# execute buy
				cost = self.state.quote_per_order
				self.state.balance_usdt -= cost
				# update avg cost and inventory
				prev_qty = self.state.base_qty
				prev_cost_total = prev_qty * self.state.avg_cost_per_unit
				new_qty = prev_qty + lvl.qty_base
				new_cost_total = prev_cost_total + cost
				self.state.base_qty = new_qty
				self.state.avg_cost_per_unit = new_cost_total / new_qty if new_qty > 0 else 0.0
				lvl.filled = True
				self.state.trades.append(GridTrade(ts, "buy", price, lvl.qty_base, 0.0, self.state.balance_usdt))

		# fill sells where price >= level and we have inventory
		for lvl in self.state.sell_levels:
			if lvl.filled:
				continue
			if price >= lvl.price and self.state.base_qty >= lvl.qty_base:
				proceeds = lvl.qty_base * price
				# realized pnl vs avg cost
				pnl = (price - self.state.avg_cost_per_unit) * lvl.qty_base
				self.state.base_qty -= lvl.qty_base
				self.state.balance_usdt += proceeds
				lvl.filled = True
				self.state.trades.append(GridTrade(ts, "sell", price, lvl.qty_base, pnl, self.state.balance_usdt))
				# avg cost remains same for remaining qty (moving-average method)

