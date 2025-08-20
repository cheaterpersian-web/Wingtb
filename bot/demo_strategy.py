from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Trade:
	market: str
	entry_price: float
	exit_price: Optional[float]
	qty: float
	timestamp_ms: int
	closed: bool = False
	profit_usdt: float = 0.0


@dataclass
class BacktestResult:
	market: str
	num_trades: int
	num_wins: int
	num_losses: int
	total_pnl: float
	win_rate: float
	trades: List[Trade] = field(default_factory=list)


class SimpleMAReversion:
	"""Very simple moving-average reversion demo strategy on klines.

	Rules:
	- Compute simple moving average (SMA) of close for window n (default 10)
	- Enter long when close < SMA * (1 - threshold)
	- Exit when close >= SMA or stop loss at SMA * (1 - 2*threshold)
	This is purely for demo; not financial advice.
	"""

	def __init__(self, window: int = 10, threshold: float = 0.003, position_size_usdt: float = 100.0) -> None:
		self.window = window
		self.threshold = threshold
		self.position_size_usdt = position_size_usdt

	def run(self, market: str, klines: List[Dict]) -> BacktestResult:
		closes: List[float] = []
		open_position: Optional[Trade] = None
		trades: List[Trade] = []

		for k in klines:
			close = float(k.get("close"))
			ts = int(k.get("created_at"))
			closes.append(close)

			if len(closes) < self.window:
				continue

			sma = sum(closes[-self.window:]) / self.window
			buy_trigger = close < sma * (1 - self.threshold)
			sell_trigger = close >= sma
			stop_loss = close < sma * (1 - 2 * self.threshold)

			if open_position is None and buy_trigger:
				qty = self.position_size_usdt / close
				open_position = Trade(market=market, entry_price=close, exit_price=None, qty=qty, timestamp_ms=ts)
				continue

			if open_position is not None and (sell_trigger or stop_loss):
				open_position.exit_price = close
				open_position.closed = True
				open_position.profit_usdt = (open_position.exit_price - open_position.entry_price) * open_position.qty
				trades.append(open_position)
				open_position = None

		# If position still open at the end, mark-to-market at last close
		if open_position is not None:
			open_position.exit_price = closes[-1]
			open_position.closed = True
			open_position.profit_usdt = (open_position.exit_price - open_position.entry_price) * open_position.qty
			trades.append(open_position)

		num_trades = len(trades)
		num_wins = sum(1 for t in trades if t.profit_usdt > 0)
		num_losses = sum(1 for t in trades if t.profit_usdt <= 0)
		total_pnl = sum(t.profit_usdt for t in trades)
		win_rate = (num_wins / num_trades * 100.0) if num_trades > 0 else 0.0

		return BacktestResult(
			market=market,
			num_trades=num_trades,
			num_wins=num_wins,
			num_losses=num_losses,
			total_pnl=total_pnl,
			win_rate=win_rate,
			trades=trades,
		)

	@staticmethod
	def format_summary(result: BacktestResult) -> str:
		return (
			f"Market: {result.market}\n"
			f"Trades: {result.num_trades} | Wins: {result.num_wins} | Losses: {result.num_losses}\n"
			f"Win Rate: {result.win_rate:.2f}% | Total PnL: {result.total_pnl:.4f} USDT"
		)

