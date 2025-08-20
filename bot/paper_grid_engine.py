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
		# grid tuning options (official-style)
		spacing_mode: str = "percent",  # "percent" or "arithmetic"
		step_absolute: Optional[float] = None,  # used when spacing_mode == "arithmetic"
		center_price_source: str = "last",  # "last" or "ema"
		ema_len_for_center: int = 20,
		kill_switch_pct: Optional[float] = None,  # if recenter_on_break is False and price breaks beyond this, stop
		only_buy_mode: bool = False,
	) -> None:
		self.client = client
		self.market = market
		self.period = period
		self.poll_sec = poll_sec
		self.recenter_on_break = recenter_on_break
		self.spacing_mode = spacing_mode
		self.step_absolute = step_absolute
		self.center_price_source = center_price_source
		self.ema_len_for_center = ema_len_for_center
		self.kill_switch_pct = kill_switch_pct
		self.only_buy_mode = only_buy_mode
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
		center = self._compute_center_price()
		self._center_price = center
		self.state.buy_levels = []
		self.state.sell_levels = []
		self._build_levels(center)

	def _compute_center_price(self) -> float:
		# Source center from last price or EMA of close
		if self.center_price_source == "ema":
			limit = self.ema_len_for_center if self.ema_len_for_center > 1 else 20
			rows = self.client.get_kline(self.market, self.period, limit=limit)
			if not rows:
				raise RuntimeError("Could not fetch klines for EMA center price")
			# compute standard EMA over closes
			closes = [float(r.get("close")) for r in rows if r.get("close") is not None]
			if not closes:
				raise RuntimeError("No closing prices for EMA center price")
			alpha = 2.0 / (min(len(closes), self.ema_len_for_center) + 1.0)
			ema = closes[0]
			for c in closes[1:]:
				ema = alpha * c + (1.0 - alpha) * ema
			return ema
		# default: last close
		rows = self.client.get_kline(self.market, self.period, limit=1)
		if not rows:
			raise RuntimeError("Could not fetch last close for center price")
		return float(rows[-1].get("close"))

	def _build_levels(self, center: float) -> None:
		levels = self.state.levels_per_side
		if levels <= 0:
			return
		if self.spacing_mode == "arithmetic":
			# fixed absolute steps; fallback to percent-derived step if not provided
			buy_step_abs = self.step_absolute if self.step_absolute is not None else (center * self.state.lower_pct / levels)
			sell_step_abs = self.step_absolute if self.step_absolute is not None else (center * self.state.upper_pct / levels)
			for i in range(1, levels + 1):
				buy_price = center - buy_step_abs * i
				if buy_price > 0:
					qty = self.state.quote_per_order / buy_price
					self.state.buy_levels.append(GridLevel(price=buy_price, qty_base=qty))
				if not self.only_buy_mode:
					sell_price = center + sell_step_abs * i
					qty_s = self.state.quote_per_order / sell_price
					self.state.sell_levels.append(GridLevel(price=sell_price, qty_base=qty_s))
		else:
			# percent spacing
			buy_step_pct = self.state.lower_pct / levels
			sell_step_pct = self.state.upper_pct / levels
			for i in range(1, levels + 1):
				buy_price = center * (1 - buy_step_pct * i)
				qty = self.state.quote_per_order / buy_price
				self.state.buy_levels.append(GridLevel(price=buy_price, qty_base=qty))
				if not self.only_buy_mode:
					sell_price = center * (1 + sell_step_pct * i)
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
		# kill switch: if not recentering and break beyond kill threshold, stop the engine
		if (not self.recenter_on_break) and self._center_price and self.kill_switch_pct is not None:
			low_kill = self._center_price * (1 - self.kill_switch_pct)
			high_kill = self._center_price * (1 + self.kill_switch_pct)
			if price < low_kill or price > high_kill:
				self._stop.set()
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

