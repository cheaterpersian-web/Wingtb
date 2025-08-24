from __future__ import annotations

from typing import Dict, List, Any


def run_fixed_grid_backtest(
	closes: List[float],
	lower: float | None = None,
	upper: float | None = None,
	grids: int = 20,
	tp_pct: float = 0.01,
	amount_usdt: float = 50.0,
	fee_bps: float = 10.0,
	start_usdt: float = 10000.0,
	cap_usdt: float = 300.0,
	max_open_lots: int = 4,
) -> Dict[str, Any]:
	if not closes or len(closes) < 10:
		return {"error": "داده کافی نیست"}
	lb = float(lower) if lower else (min(closes) * 0.99)
	ub = float(upper) if upper else (max(closes) * 1.01)
	if ub <= lb:
		lb, ub = min(lb, ub), max(lb, ub)
	step = (ub - lb) / max(grids, 1)
	grid_lines = [lb + i * step for i in range(grids + 1)]
	usdt = float(start_usdt)
	open_lots: List[Dict[str, float]] = []
	wins = losers = entries = exits = 0
	forced_exits = 0
	profit_usdt = loss_usdt = 0.0
	gross_profit_usdt = gross_loss_usdt = 0.0
	fees_total = 0.0
	fees_buy = 0.0
	fees_sell = 0.0
	engaged_usdt_max = 0.0
	cutoff_bars = max(5, int(0.02 * len(closes)))
	for i in range(1, len(closes)):
		prev_px = closes[i - 1]
		px = closes[i]
		new_open: List[Dict[str, float]] = []
		for lot in open_lots:
			if px >= lot["tp"]:
				notional = lot["qty"] * px
				fee = notional * (fee_bps / 10000.0)
				fees_total += fee
				fees_sell += fee
				proceeds = notional - fee
				realized = proceeds - lot["cost"]
				usdt += proceeds
				exits += 1
				# gross without fees
				realized_gross = (lot["qty"] * px) - (lot["qty"] * lot["entry"])
				if realized > 0:
					wins += 1
					profit_usdt += realized
					gross_profit_usdt += max(realized_gross, 0.0)
				else:
					losers += 1
					loss_usdt += (-realized)
					gross_loss_usdt += max(-realized_gross, 0.0)
			else:
				new_open.append(lot)
		open_lots = new_open
		if usdt > 0 and lb <= px <= ub and i < len(closes) - cutoff_bars:
			for line in grid_lines:
				if px <= line < prev_px:
					# enforce max open lots
					if len(open_lots) >= int(max_open_lots):
						break
					# enforce cap_usdt on engaged capital
					engaged_now = sum(l["cost"] for l in open_lots) if open_lots else 0.0
					remain_cap = max(0.0, float(cap_usdt) - engaged_now)
					max_by_cap = remain_cap / (1.0 + fee_bps / 10000.0)
					amount = min(amount_usdt, usdt, max_by_cap)
					if amount <= 0:
						break
					qty = amount / max(px, 1e-9)
					fee_in = amount * (fee_bps / 10000.0)
					fees_total += fee_in
					fees_buy += fee_in
					cost = amount + fee_in
					usdt -= cost
					open_lots.append({"qty": qty, "entry": px, "cost": cost, "tp": px * (1.0 + tp_pct)})
					entries += 1
		# update engaged peak after this bar's operations
		engaged_now = sum(l["cost"] for l in open_lots) if open_lots else 0.0
		if engaged_now > engaged_usdt_max:
			engaged_usdt_max = engaged_now
	final_px = closes[-1]
	for lot in open_lots:
		notional = lot["qty"] * final_px
		fee = notional * (fee_bps / 10000.0)
		fees_total += fee
		fees_sell += fee
		proceeds = notional - fee
		realized = proceeds - lot["cost"]
		usdt += proceeds
		exits += 1
		forced_exits += 1
		# gross without fees
		realized_gross = (lot["qty"] * final_px) - (lot["qty"] * lot["entry"])
		if realized > 0:
			wins += 1
			profit_usdt += realized
			gross_profit_usdt += max(realized_gross, 0.0)
		else:
			losers += 1
			loss_usdt += (-realized)
			gross_loss_usdt += max(-realized_gross, 0.0)
	win_rate = (wins / exits * 100.0) if exits else 0.0
	net_profit_usdt = usdt - start_usdt
	return {
		"entries": entries,
		"exits": exits,
		"wins": wins,
		"losers": losers,
		"forced_exits": forced_exits,
		"profit_usdt": profit_usdt,
		"loss_usdt": loss_usdt,
		"gross_profit_usdt": gross_profit_usdt,
		"gross_loss_usdt": gross_loss_usdt,
		"fees_total": fees_total,
		"fees_buy": fees_buy,
		"fees_sell": fees_sell,
		"engaged_usdt_max": engaged_usdt_max,
		"final_equity": usdt,
		"win_rate": win_rate,
		"net_profit_usdt": net_profit_usdt,
	}