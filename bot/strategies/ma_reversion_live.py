from collections import deque
from typing import Deque

from .base import Candle, LiveStrategy, Signal


class MAReversionLive(LiveStrategy):
	def __init__(self, window: int = 10, threshold: float = 0.003) -> None:
		self.window = window
		self.threshold = threshold
		self.closes: Deque[float] = deque(maxlen=window)

	def on_candle(self, candle: Candle) -> Signal:
		self.closes.append(candle.close)
		if len(self.closes) < self.window:
			return Signal(action="hold", strength=0.0, reason="warmup")
		sma = sum(self.closes)/len(self.closes)
		if candle.close < sma * (1 - self.threshold):
			return Signal(action="buy", strength=1.0, reason="below_sma")
		if candle.close >= sma:
			return Signal(action="sell", strength=1.0, reason="mean_revert")
		return Signal(action="hold", strength=0.0)

