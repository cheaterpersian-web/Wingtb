from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MetricsSnapshot:
    equity: float
    realized_pnl: float
    win_rate: float
    max_drawdown: float

