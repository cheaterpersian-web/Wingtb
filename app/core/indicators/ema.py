from __future__ import annotations

from typing import Iterable, List


def compute_ema(prices: Iterable[float], period: int) -> List[float]:
    prices_list = list(prices)
    if period <= 0:
        raise ValueError("period must be positive")
    if not prices_list:
        return []
    k = 2.0 / (period + 1.0)
    ema_values: List[float] = []
    ema_prev: float | None = None
    for p in prices_list:
        if ema_prev is None:
            ema_prev = p
        else:
            ema_prev = p * k + ema_prev * (1.0 - k)
        ema_values.append(ema_prev)
    return ema_values

