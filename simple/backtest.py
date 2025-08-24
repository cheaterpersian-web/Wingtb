from __future__ import annotations

from typing import Dict, List, Any


def run_grid_backtest(
	closes: List[float],
	lower: float | None = None,
	upper: float | None = None,
	grids: int = 20,
	tp_pct: float = 0.01,
	amount_usdt: float = 50.0,
) -> Dict[str, Any]:
	if not closes or len(closes) < 10:
		return {"error": "داده کافی نیست"}
	lb = float(lower) if lower else (min(closes) * 0.99)
	ub = float(upper) if upper else (max(closes) * 1.01)
	if ub <= lb:
		lb, ub = min(lb, ub), max(lb, ub)
	step = (ub - lb) / max(grids, 1)
	grid_lines = [lb + i * step for i in range(grids + 1)]
	usdt = 10000.0
	fee_bps = 10.0
	open_lots: List[Dict[str, float]] = []
	wins = losers = entries = exits = 0
	forced_exits = 0
	profit_usdt = loss_usdt = 0.0
	cutoff_bars = max(5, int(0.02 * len(closes)))
	for i in range(1, len(closes)):
		prev_px = closes[i - 1]
		px = closes[i]
		new_open: List[Dict[str, float]] = []
		for lot in open_lots:
			if px >= lot["tp"]:
				notional = lot["qty"] * px
				fee = notional * (fee_bps / 10000.0)
				proceeds = notional - fee
				realized = proceeds - lot["cost"]
				usdt += proceeds
				exits += 1
				if realized > 0:
					wins += 1
					profit_usdt += realized
				else:
					losers += 1
					loss_usdt += (-realized)
			else:
				new_open.append(lot)
		open_lots = new_open
		if usdt > amount_usdt and lb <= px <= ub and i < len(closes) - cutoff_bars:
			for line in grid_lines:
				if px <= line < prev_px:
					amount = min(amount_usdt, usdt)
					if amount <= 0:
						break
					qty = amount / max(px, 1e-9)
					fee_in = amount * (fee_bps / 10000.0)
					cost = amount + fee_in
					usdt -= cost
					open_lots.append({"qty": qty, "entry": px, "cost": cost, "tp": px * (1.0 + tp_pct)})
					entries += 1
	final_px = closes[-1]
	for lot in open_lots:
		notional = lot["qty"] * final_px
		fee = notional * (fee_bps / 10000.0)
		proceeds = notional - fee
		usdt += proceeds
		exits += 1
		forced_exits += 1
	win_rate = (wins / exits * 100.0) if exits else 0.0
	return {
		"entries": entries,
		"exits": exits,
		"wins": wins,
		"losers": losers,
		"forced_exits": forced_exits,
		"profit_usdt": profit_usdt,
		"loss_usdt": loss_usdt,
		"final_equity": usdt,
		"win_rate": win_rate,
	}