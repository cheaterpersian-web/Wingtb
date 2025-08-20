from dataclasses import dataclass
from typing import Optional


@dataclass
class Candle:
	created_at_ms: int
	open: float
	high: float
	low: float
	close: float
	volume: float


@dataclass
class Signal:
	action: str  # 'buy' | 'sell' | 'hold'
	strength: float = 1.0  # 0..1
	reason: Optional[str] = None


class LiveStrategy:
	"""Interface for strategies that can operate incrementally on new candles."""

	def on_candle(self, candle: Candle) -> Signal:
		raise NotImplementedError

