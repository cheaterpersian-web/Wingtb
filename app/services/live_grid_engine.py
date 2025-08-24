from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Awaitable, Callable, Dict, Optional
import logging


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
		self._log = logging.getLogger("live_grid")

	def set_market(self, market: str) -> None:
		self._market = market

	def is_running(self) -> bool:
		return self._running

	def grid_state(self) -> Optional[Dict[str, Any]]:
		return self._grid

	async def start(self, chat_id: int, grids_n: int, step_p: float, tp_p: float, sl_p: float, amount: float, *, cap_usdt: float | None = None, max_open_lots: int | None = None, w_bottom: float = 1.0, w_top: float = 1.0) -> Dict[str, float]:
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
		tp_p = max(0.003, min(0.10, tp_p))
		sl_p = max(0.005, min(0.03, sl_p))
		w_bottom = max(0.0, float(w_bottom))
		w_top = max(0.0, float(w_top))
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
			"cap_usdt": float(cap_usdt) if cap_usdt is not None else None,
			"max_open_lots": int(max_open_lots) if max_open_lots is not None else None,
			"w_bottom": w_bottom,
			"w_top": w_top,
		}
		self._running = True
		self._task = asyncio.create_task(self._loop(chat_id))
		self._log.info("grid started: market=%s center=%.6f lines=%d step_pct=%.4f tp_pct=%.4f sl_pct=%.4f amount=%.2f", self._market, center, len(lines), step_p, tp_p, sl_p, amount)
		return {
			"center": center,
			"grids_total": grids_n * 2 + 1,
			"grids_per_side": grids_n,
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
		self._log.info("grid stopped: market=%s", self._market)

	def levels_text(self, max_lines: int = 30) -> str:
		g = self._grid
		if not g:
			return "گرید خاموش است. از پریست شروع کنید."
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
				self._log.info("tick: market=%s price=%.8f", self._market, p)
				g = self._grid
				if g:
					prev_px = g.get("prev_px", p)
					lines = g["lines"]
					amount = g["amount"]
					tp_pct = g["tp_pct"]
					sl_pct = g["sl_pct"]
					cap = g.get("cap_usdt")
					max_lots = g.get("max_open_lots")
					w_bottom = float(g.get("w_bottom", 1.0))
					w_top = float(g.get("w_top", 1.0))
					# 1) Process TP/SL on open lots
					new_open = []
					for lot in g["open_lots"]:
						if p <= lot.get("sl", 0.0):
							qty = lot["qty"]
							_ = await self._paper.place_order(self._market, "SELL", qty, p)
							await self._notify(chat_id, f"🔻 فروش با استاپ‌لاس {self._market} | مقدار={qty:.8f} | قیمت={p:.8f}")
							self._log.info("sell SL: qty=%.8f price=%.8f", qty, p)
							lk = lot.get("line_key")
							if lk is not None:
								g["occupied"][lk] = False
							continue
						if p >= lot["tp"]:
							qty = lot["qty"]
							_ = await self._paper.place_order(self._market, "SELL", qty, p)
							await self._notify(chat_id, f"✅ فروش با تارگت {self._market} | مقدار={qty:.8f} | قیمت={p:.8f}")
							self._log.info("sell TP: qty=%.8f price=%.8f", qty, p)
							lk = lot.get("line_key")
							if lk is not None:
								g["occupied"][lk] = False
							continue
						new_open.append(lot)
					g["open_lots"] = new_open
					# 2) Detect downward crosses and buy (limit fills)
					if g["lb"] <= p <= g["ub"]:
						for idx, line in enumerate(lines):
							if p <= line < prev_px:
								lk = f"{line:.8f}"
								if g["occupied"].get(lk):
									continue
								if max_lots is not None and len(g["open_lots"]) >= int(max_lots):
									break
								# compute weighted amount and enforce cap
								den = max(1, len(lines) - 1)
								w = float(w_bottom) + (float(w_top) - float(w_bottom)) * (idx / den)
								buy_amount = amount * max(0.0, w)
								if cap is not None:
									engaged_now = sum(l["cost"] for l in g["open_lots"]) if g["open_lots"] else 0.0
									remain_cap = max(0.0, float(cap) - engaged_now)
									max_by_cap = remain_cap / (1.0 + (self._paper.fee_bps / 10000.0)) if getattr(self._paper, 'fee_bps', None) is not None else remain_cap
									buy_amount = min(buy_amount, max_by_cap)
								buy_amount = min(buy_amount, self._paper.usdt_balance)
								if buy_amount <= 0:
									break
								_ = await self._paper.place_order(self._market, "BUY", buy_amount / max(p, 1e-9), p)
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
								self._log.info("buy: amount=%.2f qty=%.8f price=%.8f tp=%.8f sl=%.8f", buy_amount, qty, p, p*(1+tp_pct), p*(1-sl_pct))
					g["prev_px"] = p
				await asyncio.sleep(2)
		except Exception:
			# swallow and stop loop
			self._log.exception("grid loop error")
		finally:
			self._running = False