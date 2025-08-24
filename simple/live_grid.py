from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional


class LiveGridEngine:
	def __init__(
		self,
		get_price: Callable[[str], Awaitable[float]],
		paper: Any,
		notify: Callable[[int, str], Awaitable[Any]],
	) -> None:
		self._get_price = get_price
		self._paper = paper
		self._notify = notify
		self._market: str = "TRXUSDT"
		self._running: bool = False
		self._grid: Optional[Dict[str, Any]] = None
		self._task: Optional[asyncio.Task] = None

	def set_market(self, market: str) -> None:
		self._market = market

	def is_running(self) -> bool:
		return self._running

	def grid_state(self) -> Optional[Dict[str, Any]]:
		return self._grid

	async def start(self, chat_id: int, grids_n: int, step_p: float, tp_p: float, sl_p: float, amount: float) -> Dict[str, float]:
		if self._running:
			return {
				"center": float(self._grid.get("prev_px", 0.0)) if self._grid else 0.0,
				"grids_total": grids_n * 2 + 1,
				"step_pct": step_p,
				"tp_pct": tp_p,
				"sl_pct": sl_p,
				"amount": amount,
			}
		# clamp params
		grids_n = max(10, min(30, grids_n))
		step_p = max(0.003, min(0.01, step_p))
		tp_p = max(0.003, min(0.02, tp_p))
		sl_p = max(0.005, min(0.03, sl_p))
		center = await self._get_price(self._market)
		await self._paper.on_price(center)
		step_abs = center * step_p
		lb = center - step_abs * grids_n
		ub = center + step_abs * grids_n
		lines = [lb + i * step_abs for i in range(grids_n * 2 + 1)]
		occupied = {f"{ln:.8f}": False for ln in lines}
		self._grid = {
			"lb": lb,
			"ub": ub,
			"lines": lines,
			"tp_pct": tp_p,
			"sl_pct": sl_p,
			"amount": amount,
			"open_lots": [],
			"occupied": occupied,
			"prev_px": center,
		}
		self._running = True
		self._task = asyncio.create_task(self._loop(chat_id))
		return {
			"center": center,
			"grids_total": grids_n * 2 + 1,
			"step_pct": step_p,
			"tp_pct": tp_p,
			"sl_pct": sl_p,
			"amount": amount,
		}

	async def stop(self) -> None:
		self._running = False
		if self._task:
			self._task.cancel()
			with contextlib.suppress(Exception):
				await self._task
			self._task = None
		self._grid = None

	def levels_text(self, max_lines: int = 30) -> str:
		g = self._grid
		if not g:
			return "گرید خاموش است. از /grid_on استفاده کنید"
		lines = g["lines"]
		text = (
			f"کف={g['lb']:.8f} | سقف={g['ub']:.8f} | تعداد خطوط={len(lines)} | حدسود={g['tp_pct']*100:.2f}% | حدضرر={g['sl_pct']*100:.2f}%\n"
		)
		text += "خطوط:\n" + "\n".join(f"{lv:.8f}" for lv in lines[:min(len(lines), max_lines)])
		return text

	async def _loop(self, chat_id: int) -> None:
		try:
			while self._running:
				p = await self._get_price(self._market)
				await self._paper.on_price(p)
				g = self._grid
				if g:
					prev_px = g.get("prev_px", p)
					lines = g["lines"]
					amount = g["amount"]
					tp_pct = g["tp_pct"]
					sl_pct = g["sl_pct"]
					# 1) Process TP/SL on open lots
					new_open = []
					for lot in g["open_lots"]:
						if p <= lot.get("sl", 0.0):
							qty = lot["qty"]
							_ = await self._paper.sell(self._market, qty)
							await self._notify(chat_id, f"🔻 فروش با استاپ‌لاس {self._market} | مقدار={qty:.8f} | قیمت={p:.8f}")
							lk = lot.get("line_key")
							if lk is not None:
								g["occupied"][lk] = False
							continue
						if p >= lot["tp"]:
							qty = lot["qty"]
							_ = await self._paper.sell(self._market, qty)
							await self._notify(chat_id, f"✅ فروش با تارگت {self._market} | مقدار={qty:.8f} | قیمت={p:.8f}")
							lk = lot.get("line_key")
							if lk is not None:
								g["occupied"][lk] = False
							continue
						new_open.append(lot)
					g["open_lots"] = new_open
					# 2) Detect downward crosses and buy (limit fills)
					if self._paper.usdt > amount and g["lb"] <= p <= g["ub"]:
						for line in lines:
							if p <= line < prev_px:
								lk = f"{line:.8f}"
								if g["occupied"].get(lk):
									continue
								buy_amount = min(amount, self._paper.usdt)
								if buy_amount <= 0:
									break
								_ = await self._paper.buy(self._market, buy_amount)
								qty = buy_amount / max(p, 1e-9)
								g["open_lots"].append({
									"qty": qty,
									"entry": p,
									"cost": buy_amount + (buy_amount * (self._paper.fee_bps / 10000.0)),
									"tp": p * (1.0 + tp_pct),
									"sl": p * (1.0 - sl_pct),
									"line_key": lk,
								})
								g["occupied"][lk] = True
								await self._notify(chat_id, f"🟢 خرید {self._market} | مبلغ={buy_amount:.2f} | مقدار={qty:.8f} | قیمت={p:.8f} | حدسود={p*(1+tp_pct):.8f} | حدضرر={p*(1-sl_pct):.8f}")
					g["prev_px"] = p
				await asyncio.sleep(2)
		except Exception:
			# swallow and stop loop
			pass
		finally:
			self._running = False


import contextlib