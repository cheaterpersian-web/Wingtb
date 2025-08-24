from __future__ import annotations

from typing import Iterable, List


def compute_rsi(prices: Iterable[float], period: int = 14) -> List[float]:
    prices_list = list(prices)
    if period <= 0:
        raise ValueError("period must be positive")
    n = len(prices_list)
    if n < 2:
        return [50.0] * n

    gains: List[float] = [0.0] * n
    losses: List[float] = [0.0] * n
    for i in range(1, n):
        change = prices_list[i] - prices_list[i - 1]
        gains[i] = max(change, 0.0)
        losses[i] = max(-change, 0.0)

    def rma(values: List[float], period: int) -> List[float]:
        rma_values: List[float] = [0.0] * n
        # seed with SMA of first period
        if n >= period:
            seed = sum(values[1 : period + 1]) / period
            rma_values[period] = seed
            alpha = 1.0 / period
            for i in range(period + 1, n):
                rma_values[i] = alpha * values[i] + (1 - alpha) * rma_values[i - 1]
        return rma_values

    avg_gain = rma(gains, period)
    avg_loss = rma(losses, period)

    rsi_values: List[float] = [50.0] * n
    for i in range(n):
        denom = avg_loss[i]
        if denom == 0:
            rsi_values[i] = 100.0
        else:
            rs = avg_gain[i] / denom
            rsi_values[i] = 100.0 - (100.0 / (1.0 + rs))
    return rsi_values

