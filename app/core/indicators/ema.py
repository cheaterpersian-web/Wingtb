from typing import List


def ema(series: List[float], period: int) -> List[float]:
    if period <= 1 or not series:
        return series[:]
    k = 2.0 / (period + 1.0)
    out: List[float] = []
    ema_val = series[0]
    out.append(ema_val)
    for x in series[1:]:
        ema_val = k * x + (1.0 - k) * ema_val
        out.append(ema_val)
    return out

