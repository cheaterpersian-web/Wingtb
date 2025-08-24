from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import List, Dict, Any

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from dotenv import load_dotenv
from app.datafeed.coinex_rest import CoinExREST


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
logger = logging.getLogger("simple")


COINEX_V2 = "https://api.coinex.com/v2"


def ema(values: List[float], period: int) -> List[float]:
	if period <= 0 or not values:
		return []
	k = 2.0 / (period + 1.0)
	out: List[float] = []
	p = None
	for v in values:
		p = v if p is None else (v * k + p * (1.0 - k))
		out.append(p)
	return out


def rsi(values: List[float], period: int = 14) -> List[float]:
	if len(values) < 2:
		return [50.0] * len(values)
	gains = [0.0]
	losses = [0.0]
	for i in range(1, len(values)):
		delta = values[i] - values[i - 1]
		gains.append(max(delta, 0.0))
		losses.append(max(-delta, 0.0))
	def rma(arr: List[float], n: int) -> List[float]:
		if len(arr) < n + 1:
			return [0.0] * len(arr)
		out = [0.0] * len(arr)
		seed = sum(arr[1 : n + 1]) / n
		out[n] = seed
		alpha = 1.0 / n
		for i in range(n + 1, len(arr)):
			out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
		return out
	avg_gain = rma(gains, period)
	avg_loss = rma(losses, period)
	rsivals: List[float] = [50.0] * len(values)
	for i in range(len(values)):
		l = avg_loss[i]
		if l == 0:
			rsivals[i] = 100.0
		else:
			rs = avg_gain[i] / l
			rsivals[i] = 100.0 - (100.0 / (1.0 + rs))
	return rsivals


def sma(values: List[float], period: int) -> List[float]:
	if period <= 0 or not values:
		return []
	out: List[float] = []
	window: List[float] = []
	sum_v = 0.0
	for v in values:
		window.append(v)
		sum_v += v
		if len(window) > period:
			sum_v -= window.pop(0)
		out.append(sum_v / len(window))
	return out


def rolling_std(values: List[float], period: int) -> List[float]:
	if period <= 1 or not values:
		return [0.0] * len(values)
	out: List[float] = []
	window: List[float] = []
	for v in values:
		window.append(v)
		if len(window) > period:
			window.pop(0)
		if len(window) < 2:
			out.append(0.0)
			continue
		m = sum(window) / len(window)
		var = sum((x - m) * (x - m) for x in window) / (len(window) - 1)
		out.append(var ** 0.5)
	return out


def rolling_max(values: List[float], period: int) -> List[float]:
	if period <= 0 or not values:
		return []
	out: List[float] = []
	window: List[float] = []
	for v in values:
		window.append(v)
		if len(window) > period:
			window.pop(0)
		out.append(max(window))
	return out


def rolling_min(values: List[float], period: int) -> List[float]:
	if period <= 0 or not values:
		return []
	out: List[float] = []
	window: List[float] = []
	for v in values:
		window.append(v)
		if len(window) > period:
			window.pop(0)
		out.append(min(window))
	return out


def atr_series(klines: List[Dict[str, Any]], period: int = 14) -> List[float]:
	if len(klines) < 2:
		return [0.0] * len(klines)
	trs: List[float] = [0.0]
	prev_close = float(klines[0].get("close") or 0.0)
	for i in range(1, len(klines)):
		h = float(klines[i].get("high") or 0.0)
		l = float(klines[i].get("low") or 0.0)
		tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
		trs.append(tr)
		prev_close = float(klines[i].get("close") or prev_close)
	atr_vals = ema(trs, period)
	if not atr_vals:
		atr_vals = [0.0] * len(trs)
	return atr_vals


def normalize_klines(raw: Any) -> List[Dict[str, float]]:
	payload = raw
	if isinstance(raw, dict):
		payload = raw.get("data", raw)
	rows = None
	if isinstance(payload, dict):
		for key in ("list", "klines", "candles"):
			v = payload.get(key)
			if isinstance(v, list):
				rows = v
				break
		if rows is None:
			rows = [payload]
	else:
		rows = payload if isinstance(payload, list) else [payload]
	out: List[Dict[str, float]] = []
	for row in rows:
		close_v = None
		high_v = None
		low_v = None
		if isinstance(row, dict):
			def _getf(d: Dict[str, Any], keys: List[str]) -> float | None:
				for k in keys:
					v = d.get(k)
					if v is not None:
						try:
							return float(v)
						except Exception:
							pass
				return None
			close_v = _getf(row, ["close", "c", "last", "price"])
			high_v = _getf(row, ["high", "h", "max"])
			low_v = _getf(row, ["low", "l", "min"])
			if close_v is None:
				for key in ("values", "kline"):
					vals = row.get(key)
					if isinstance(vals, (list, tuple)) and len(vals) >= 5:
						try:
							ns = [float(x) for x in vals]
							close_v = ns[2]
							high_v = ns[3]
							low_v = ns[4]
						except Exception:
							pass
		elif isinstance(row, (list, tuple)):
			try:
				ns = [float(x) for x in row]
				if len(ns) >= 5:
					close_v = ns[2]
					high_v = ns[3]
					low_v = ns[4]
				elif len(ns) >= 3:
					close_v = ns[2]
					high_v = max(ns[1:3])
					low_v = min(ns[1:3])
			except Exception:
				pass
		elif isinstance(row, str):
			try:
				obj = json.loads(row)
				out.extend(normalize_klines(obj))
				continue
			except Exception:
				pass
		if close_v is None:
			continue
		if high_v is None:
			high_v = close_v
		if low_v is None:
			low_v = close_v
		out.append({"close": float(close_v), "high": float(high_v), "low": float(low_v)})
	return out


class CoinExClient:
	def __init__(self) -> None:
		self.client = httpx.AsyncClient(timeout=12)
		self.rest = CoinExREST()

	async def price(self, market: str) -> float:
		r = await self.client.get(f"{COINEX_V2}/spot/ticker", params={"market": market})
		r.raise_for_status()
		payload = r.json()
		data = payload.get("data", payload)
		last = None
		if isinstance(data, dict):
			last = data.get("last") or data.get("price")
			if last is None and isinstance(data.get("ticker"), dict):
				d = data["ticker"]
				last = d.get("last") or d.get("price")
		elif isinstance(data, list):
			for d in data:
				if not isinstance(d, dict):
					continue
				if (not d.get("market") or d.get("market") == market):
					last = d.get("last") or d.get("price")
					if last is None and isinstance(d.get("ticker"), dict):
						td = d["ticker"]
						last = td.get("last") or td.get("price")
					if last is not None:
						break
		if last is None:
			raise ValueError("ticker: last price not found")
		return float(last)

	async def klines(self, market: str, period: str = "5min", limit: int = 100) -> List[Dict[str, float]]:
		try:
			rows = await self.rest.get_klines(market, period, limit)
			if not rows and period == "5min" and limit >= 2000:
				# auto-downgrade month -> 15d if API returns empty
				rows = await self.rest.get_klines(market, "15min", 1440)
			return normalize_klines({"data": rows})
		except Exception:
			# final fallback to direct v2
			r = await self.client.get(f"{COINEX_V2}/spot/kline", params={"market": market, "period": period, "limit": limit})
			r.raise_for_status()
			return normalize_klines(r.json())


class Paper:
	def __init__(self, usdt: float = 10000.0, fee_bps: float = 10.0) -> None:
		self.usdt = usdt
		self.qty = 0.0
		self.price = 0.0
		self.fee_bps = fee_bps

	async def on_price(self, p: float) -> None:
		self.price = p

	async def buy(self, market: str, usdt_amount: float) -> str:
		usdt_amount = min(usdt_amount, self.usdt)
		if usdt_amount <= 0:
			return "NO_BALANCE"
		qty = usdt_amount / max(self.price, 1e-9)
		fee = usdt_amount * (self.fee_bps / 10000.0)
		self.usdt -= (usdt_amount + fee)
		self.qty += qty
		return f"BUY {market} qty={qty:.8f} @ {self.price:.8f} fee={fee:.4f}"

	async def sell(self, market: str, qty: float) -> str:
		qty = min(qty, self.qty)
		if qty <= 0:
			return "NO_POSITION"
		proceeds = qty * self.price
		fee = proceeds * (self.fee_bps / 10000.0)
		self.usdt += (proceeds - fee)
		self.qty -= qty
		return f"SELL {market} qty={qty:.8f} @ {self.price:.8f} fee={fee:.4f}"

	def status(self) -> str:
		equity = self.usdt + self.qty * self.price
		return f"موجودی={self.usdt:.2f} USDT | مقدار={self.qty:.8f} | قیمت={self.price:.8f} | ارزش={equity:.2f}"


async def main() -> None:
	load_dotenv()
	token = os.getenv("BOT_TOKEN")
	if not token:
		raise RuntimeError("BOT_TOKEN missing")
	market = os.getenv("DEFAULT_PAIR", "TRXUSDT")

	bot = Bot(token)
	dp = Dispatcher()
	cx = CoinExClient()
	paper = Paper(usdt=10000)
	grid_per_side = 16
	step_pct = 0.005
	anchor_px: float = 0.0
	dynamic_center = True
	last_signal_ts: float = 0.0
	last_signal_side: str = ""
	min_cooldown = 3.0
	k_period = "15min"
	ind = {"ema_fast": 0.0, "ema_slow": 0.0, "rsi": 50.0, "atr": 0.0}
	base_amount_usdt: float = float(os.getenv("BASE_ORDER_USDT", "50"))

	@dp.message(Command("start"))
	async def start(message: Message):
		kb = InlineKeyboardMarkup(inline_keyboard=[
			[InlineKeyboardButton(text="وضعیت 💼", callback_data="menu:status"), InlineKeyboardButton(text="قیمت ⚡", callback_data="menu:price")],
			[InlineKeyboardButton(text="روشن کردن گرید ▶️", callback_data="grid:on"), InlineKeyboardButton(text="خاموش کردن گرید ⏹", callback_data="grid:off")],
			[InlineKeyboardButton(text="سطوح گرید 📐", callback_data="grid:levels")],
			[InlineKeyboardButton(text="افزایش مبلغ +10", callback_data="amt:+10"), InlineKeyboardButton(text="کاهش مبلغ -10", callback_data="amt:-10")],
			[InlineKeyboardButton(text="پریست: 20 گرید، 0.5% گام، 1% حدسود/حدضرر", callback_data="preset:20:0.005:0.01:0.01")],
		])
		await message.answer("ربات گرید ترید (نسخه آزمایشی) آماده است.", reply_markup=kb)

	@dp.callback_query(F.data == "menu:status")
	async def cb_status(q: CallbackQuery):
		await q.message.edit_text(paper.status() + f" | مبلغ پایه={base_amount_usdt:.2f} USDT")
		await q.answer()

	@dp.callback_query(F.data == "menu:price")
	async def cb_price(q: CallbackQuery):
		p = await cx.price(market)
		await paper.on_price(p)
		await q.message.edit_text(f"قیمت={p:.8f}")
		await q.answer()

	def clamp_params(gr: int, st: float, tp: float, sl: float, amt: float):
		gr = max(10, min(30, gr))
		st = max(0.003, min(0.01, st))
		tp = max(0.003, min(0.02, tp))
		sl = max(0.005, min(0.03, sl))
		amt = max(1.0, amt)
		return gr, st, tp, sl, amt

	def preset_text(p: Dict[str, float | int]) -> str:
		return (
			f"ویرایش پریست\n"
			f"گریدها={p['grids']} | گام={float(p['step'])*100:.2f}% | حدسود={float(p['tp'])*100:.2f}% | حدضرر={float(p['sl'])*100:.2f}% | مبلغ={float(p['amount']):.2f} USDT"
		)

	def preset_kb(p: Dict[str, float | int]) -> InlineKeyboardMarkup:
		return InlineKeyboardMarkup(inline_keyboard=[
			[InlineKeyboardButton(text="گرید -2", callback_data="edit:gr:-2"), InlineKeyboardButton(text="گرید +2", callback_data="edit:gr:+2")],
			[InlineKeyboardButton(text="گام -0.1%", callback_data="edit:step:-0.001"), InlineKeyboardButton(text="گام +0.1%", callback_data="edit:step:+0.001")],
			[InlineKeyboardButton(text="حدسود -0.2%", callback_data="edit:tp:-0.002"), InlineKeyboardButton(text="حدسود +0.2%", callback_data="edit:tp:+0.002")],
			[InlineKeyboardButton(text="حدضرر -0.2%", callback_data="edit:sl:-0.002"), InlineKeyboardButton(text="حدضرر +0.2%", callback_data="edit:sl:+0.002")],
			[InlineKeyboardButton(text="مبلغ -10", callback_data="edit:amt:-10"), InlineKeyboardButton(text="مبلغ +10", callback_data="edit:amt:+10")],
			[InlineKeyboardButton(text="شروع ▶️", callback_data="edit:start"), InlineKeyboardButton(text="انصراف ❌", callback_data="edit:cancel")],
		])

	async def start_grid(grids_n: int, step_p: float, tp_p: float, sl_p: float, amt: float, chat_id: int):
		if running["on"]:
			await bot.send_message(chat_id, "Already ON")
			return
		# clamp params
		grids_n = max(10, min(30, grids_n))
		step_p = max(0.003, min(0.01, step_p))
		tp_p = max(0.003, min(0.02, tp_p))
		sl_p = max(0.005, min(0.03, sl_p))
		center = await cx.price(market)
		await paper.on_price(center)
		step_abs = center * step_p
		lb = center - step_abs * grids_n
		ub = center + step_abs * grids_n
		lines = [lb + i * step_abs for i in range(grids_n * 2 + 1)]
		occupied = {f"{ln:.8f}": False for ln in lines}
		live["grid"] = {"lb": lb, "ub": ub, "lines": lines, "tp_pct": tp_p, "sl_pct": sl_p, "amount": amt, "open_lots": [], "occupied": occupied, "prev_px": center}
		running["on"] = True
		asyncio.create_task(loop_prices(chat_id))
		await bot.send_message(chat_id, f"گرید روشن شد (۱۵ دقیقه) | مرکز={center:.8f} | گریدها={grids_n*2+1} | گام={step_p*100:.2f}% | حدسود={tp_p*100:.2f}% | حدضرر={sl_p*100:.2f}% | مبلغ={amt:.2f}\nدر حال اجرا…")

	@dp.callback_query(F.data == "grid:on")
	async def cb_grid_on(q: CallbackQuery):
		await start_grid(20, 0.005, 0.01, 0.01, base_amount_usdt, q.message.chat.id)
		await q.answer("گرید روشن شد")

	@dp.callback_query(F.data == "grid:off")
	async def cb_grid_off(q: CallbackQuery):
		running["on"] = False
		live["grid"] = None
		await bot.send_message(q.message.chat.id, "متوقف شد.")
		await q.answer("Grid OFF")

	@dp.callback_query(F.data == "grid:levels")
	async def cb_grid_levels(q: CallbackQuery):
		g = live.get("grid")
		if not g:
			await bot.send_message(q.message.chat.id, "Grid is OFF. Use /grid_on")
		else:
			lines = g["lines"]
			text = f"کف={g['lb']:.8f} | سقف={g['ub']:.8f} | تعداد خطوط={len(lines)} | حدسود={g['tp_pct']*100:.2f}% | حدضرر={g['sl_pct']*100:.2f}%\n"
			text += "خطوط:\n" + "\n".join(f"{lv:.8f}" for lv in lines[:min(len(lines), 30)])
			await bot.send_message(q.message.chat.id, text)
		await q.answer()

	@dp.callback_query(F.data.startswith("amt:"))
	async def cb_amount(q: CallbackQuery):
		nonlocal base_amount_usdt
		try:
			if q.data == "amt:+10":
				base_amount_usdt += 10
			else:
				base_amount_usdt = max(1.0, base_amount_usdt - 10)
			await q.message.edit_text(paper.status() + f" | مبلغ پایه={base_amount_usdt:.2f} USDT")
		except Exception:
			pass
		await q.answer("Updated")

	@dp.callback_query(F.data.startswith("preset:"))
	async def cb_preset(q: CallbackQuery):
		parts = q.data.split(":")
		gr = int(parts[1]); st = float(parts[2]); tp = float(parts[3]); sl = float(parts[4])
		gr, st, tp, sl, amt = clamp_params(gr, st, tp, sl, base_amount_usdt)
		editor[q.message.chat.id] = {"grids": gr, "step": st, "tp": tp, "sl": sl, "amount": amt}
		p = editor[q.message.chat.id]
		await q.message.edit_text(preset_text(p), reply_markup=preset_kb(p))
		await q.answer("ویرایش پریست")

	@dp.callback_query(F.data.startswith("edit:"))
	async def cb_edit(q: CallbackQuery):
		p = editor.get(q.message.chat.id) or {"grids": 20, "step": 0.005, "tp": 0.01, "sl": 0.01, "amount": base_amount_usdt}
		cmd = q.data
		try:
			if cmd == "edit:start":
				await start_grid(int(p["grids"]), float(p["step"]), float(p["tp"]), float(p["sl"]), float(p["amount"]), q.message.chat.id)
				editor.pop(q.message.chat.id, None)
				await q.answer("گرید شروع شد")
				return
			if cmd == "edit:cancel":
				editor.pop(q.message.chat.id, None)
				await q.message.edit_text("ویرایش پریست لغو شد.")
				await q.answer()
				return
			kind, op = cmd.split(":")[1], cmd.split(":")[2]
			if kind == "gr":
				p["grids"] = int(p["grids"]) + (2 if op == "+2" else -2)
			elif kind == "step":
				p["step"] = float(p["step"]) + float(op)
			elif kind == "tp":
				p["tp"] = float(p["tp"]) + float(op)
			elif kind == "sl":
				p["sl"] = float(p["sl"]) + float(op)
			elif kind == "amt":
				p["amount"] = float(p["amount"]) + (10.0 if op == "+10" else -10.0)
			# clamp and persist
			p["grids"], p["step"], p["tp"], p["sl"], p["amount"] = clamp_params(int(p["grids"]), float(p["step"]), float(p["tp"]), float(p["sl"]), float(p["amount"]))
			editor[q.message.chat.id] = p
			await q.message.edit_text(preset_text(p), reply_markup=preset_kb(p))
			await q.answer("به‌روزرسانی شد")
		except Exception:
			await q.answer("خطا", show_alert=False)

	@dp.message(Command("status"))
	async def status(message: Message):
		await message.answer(paper.status() + f" | مبلغ پایه={base_amount_usdt:.2f} USDT")

	@dp.message(Command("price"))
	async def cmd_price(message: Message):
		try:
			p = await cx.price(market)
			await paper.on_price(p)
			await message.answer(f"قیمت={p:.8f}")
		except Exception as e:
			await message.answer(f"ERR: {e}")

	running = {"on": False}
	live = {"grid": None}  # {lb, ub, lines, tp_pct, sl_pct, amount, open_lots, prev_px}
	editor: Dict[int, Dict[str, float | int]] = {}

	async def loop_prices(chat_id: int):
		while running["on"]:
			try:
				p = await cx.price(market)
				await paper.on_price(p)
				logger.info("price updated: %.8f", p)
				# Live grid execution
				if live["grid"]:
					g = live["grid"]
					prev_px = g.get("prev_px", p)
					lines = g["lines"]
					amount = g["amount"]
					tp_pct = g["tp_pct"]
					sl_pct = g["sl_pct"]
					# 1) Process TP/SL on open lots
					new_open = []
					for lot in g["open_lots"]:
						# SL first
						if p <= lot.get("sl", 0.0):
							# execute sell
							qty = lot["qty"]
							_ = await paper.sell(market, qty)
							await bot.send_message(chat_id, f"🔻 فروش با استاپ‌لاس {market} | مقدار={qty:.8f} | قیمت={p:.8f}")
							lk = lot.get("line_key")
							if lk is not None:
								g["occupied"][lk] = False
							continue
						if p >= lot["tp"]:
							qty = lot["qty"]
							_ = await paper.sell(market, qty)
							await bot.send_message(chat_id, f"✅ فروش با تارگت {market} | مقدار={qty:.8f} | قیمت={p:.8f}")
							lk = lot.get("line_key")
							if lk is not None:
								g["occupied"][lk] = False
							continue
						new_open.append(lot)
					g["open_lots"] = new_open
					# 2) Detect downward crosses and buy (limit fills)
					if paper.usdt > amount and g["lb"] <= p <= g["ub"]:
						for line in lines:
							if p <= line < prev_px:
								lk = f"{line:.8f}"
								if g["occupied"].get(lk):
									continue
								buy_amount = min(amount, paper.usdt)
								if buy_amount <= 0:
									break
								_ = await paper.buy(market, buy_amount)
								qty = buy_amount / max(p, 1e-9)
								g["open_lots"].append({
									"qty": qty,
									"entry": p,
									"cost": buy_amount + (buy_amount * (paper.fee_bps / 10000.0)),
									"tp": p * (1.0 + tp_pct),
									"sl": p * (1.0 - sl_pct),
									"line_key": lk,
								})
								g["occupied"][lk] = True
								await bot.send_message(chat_id, f"🟢 خرید {market} | مبلغ={buy_amount:.2f} | مقدار={qty:.8f} | قیمت={p:.8f} | حدسود={p*(1+tp_pct):.8f} | حدضرر={p*(1-sl_pct):.8f}")
								# continue checking deeper lines
					g["prev_px"] = p
				await asyncio.sleep(2)
			except Exception as e:
				logger.warning("price loop error: %s", e)
				await asyncio.sleep(2)

	@dp.message(Command("grid_on"))
	async def grid_on(message: Message):
		if running["on"]:
			await message.answer("Already ON")
			return
		nonlocal anchor_px
		anchor_px = await cx.price(market)
		await paper.on_price(anchor_px)
		# parse optional params: /grid_on [grids step_pct tp_pct sl_pct amount]
		parts = message.text.split()
		grids_n = int(parts[1]) if len(parts) > 1 else 20
		step_p = float(parts[2]) if len(parts) > 2 else 0.005
		tp_p = float(parts[3]) if len(parts) > 3 else 0.01
		sl_p = float(parts[4]) if len(parts) > 4 else 0.01
		amt = float(parts[5]) if len(parts) > 5 else base_amount_usdt
		# clamp params
		grids_n = max(10, min(30, grids_n))
		step_p = max(0.003, min(0.01, step_p))
		tp_p = max(0.003, min(0.02, tp_p))
		sl_p = max(0.005, min(0.03, sl_p))
		# build symmetric grid around center with 15m context
		center = anchor_px
		step_abs = center * step_p
		lb = center - step_abs * grids_n
		ub = center + step_abs * grids_n
		lines = [lb + i * step_abs for i in range(grids_n * 2 + 1)]
		occupied = {f"{ln:.8f}": False for ln in lines}
		live["grid"] = {"lb": lb, "ub": ub, "lines": lines, "tp_pct": tp_p, "sl_pct": sl_p, "amount": amt, "open_lots": [], "occupied": occupied, "prev_px": center}
		running["on"] = True
		asyncio.create_task(loop_prices(message.chat.id))
		await message.answer(f"Grid ON (15m) | center={center:.8f} grids={grids_n*2+1} step={step_p*100:.2f}% tp={tp_p*100:.2f}% sl={sl_p*100:.2f}% amount={amt:.2f}\nStreaming…")

	@dp.message(Command("grid_off"))
	async def grid_off(message: Message):
		running["on"] = False
		live["grid"] = None
		await message.answer("Stopped.")

	@dp.message(Command("grid_levels"))
	async def grid_levels(message: Message):
		g = live.get("grid")
		if not g:
			await message.answer("Grid is OFF. Use /grid_on")
			return
		lines = g["lines"]
		text = f"کف={g['lb']:.8f} | سقف={g['ub']:.8f} | تعداد خطوط={len(lines)} | حدسود={g['tp_pct']*100:.2f}% | حدضرر={g['sl_pct']*100:.2f}%\n"
		text += "خطوط:\n" + "\n".join(f"{lv:.8f}" for lv in lines[:min(len(lines), 30)])
		await message.answer(text)

	@dp.message(Command("backtest"))
	async def backtest(message: Message):
		parts = message.text.split()
		mode_grid = False
		scope = "day"
		if len(parts) > 1 and parts[1].lower() == "grid":
			mode_grid = True
			scope = parts[2].lower() if len(parts) > 2 else "day"
		else:
			scope = parts[1].lower() if len(parts) > 1 else "day"
		period_map = {"hour": ("1min", 120), "day": ("5min", 288), "15d": ("15min", 1440), "month": ("1hour", 720), "2m": ("4hour", 360), "3m": ("1day", 90)}
		if scope not in period_map:
			scope = "day"
		period, limit = period_map[scope]
		try:
			kl = await cx.klines(market, period, limit)
			# fallbacks per scope
			if not kl and scope == "day":
				period, limit = "1min", 1440
				kl = await cx.klines(market, period, limit)
			if not kl and scope == "15d":
				period, limit = "30min", 720
				kl = await cx.klines(market, period, limit)
			if not kl and scope == "month":
				period, limit = "30min", 1440
				kl = await cx.klines(market, period, limit)
			if not kl and scope == "month":
				period, limit = "2hour", 360
				kl = await cx.klines(market, period, limit)
			if not kl and scope == "2m":
				period, limit = "2hour", 720
				kl = await cx.klines(market, period, limit)
			if not kl and scope == "2m":
				period, limit = "1hour", 1440
				kl = await cx.klines(market, period, limit)
			if not kl and scope == "3m":
				period, limit = "4hour", 540
				kl = await cx.klines(market, period, limit)
			if not kl and scope == "3m":
				period, limit = "1hour", 2160
				kl = await cx.klines(market, period, limit)
			if not kl:
				await message.answer("خطا بک‌تست: داده‌ای بازنگشت")
				return
			closes = [float(k.get("close") or 0.0) for k in kl]
			if not closes:
				await message.answer("خطا بک‌تست: قیمت‌های پایانی استخراج نشد")
				return
			# Only grid mode supported now (default to grid if not specified)
			if not mode_grid:
				mode_grid = True
			# Grid parameters from args: /backtest grid <scope> [lower upper grids tp_pct base]
			last_px = closes[-1]
			def _get(idx: int, cast):
				try:
					return cast(parts[idx])
				except Exception:
					return None
			arg_start = 3 if len(parts) > 1 and parts[1].lower() == "grid" else 0
			lb = _get(arg_start, float) or (min(closes) * 0.99)
			ub = _get(arg_start + 1, float) or (max(closes) * 1.01)
			grids_n = int(_get(arg_start + 2, int) or 20)
			tp_pct = (_get(arg_start + 3, float) or 1.0) / 100.0
			amount_usdt = float(_get(arg_start + 4, float) or base_amount_usdt)
			if ub <= lb:
				lb, ub = min(lb, ub), max(lb, ub)
			step = (ub - lb) / max(grids_n, 1)
			grid_lines = [lb + i * step for i in range(grids_n + 1)]
			cutoff_bars = max(5, int(0.02 * len(closes)))
			usdt = 10000.0
			fee_bps = 10.0
			open_lots: List[Dict[str, float]] = []
			wins = losers = entries = exits = 0
			forced_exits = 0
			profit_usdt = loss_usdt = 0.0
			for i in range(1, len(closes)):
				prev_px = closes[i - 1]
				px = closes[i]
				# process TP sells
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
				# detect downward crosses of grid lines and buy (avoid buys near the very end)
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
							# continue checking lower lines too in same bar
			# finalize: liquidate remaining at last price (don't count in wins/losers)
			final_px = closes[-1]
			for lot in open_lots:
				notional = lot["qty"] * final_px
				fee = notional * (fee_bps / 10000.0)
				proceeds = notional - fee
				realized = proceeds - lot["cost"]
				usdt += proceeds
				exits += 1
				forced_exits += 1
			win_rate = (wins / exits * 100.0) if exits else 0.0
			await message.answer(
				f"بک‌تست ({scope})\n"
				f"ورودها={entries} | خروج‌ها={exits} | بردها={wins} | باخت‌ها={losers} | بستن اجباری={forced_exits} | نرخ برد={win_rate:.2f}%\n"
				f"سود={profit_usdt:.2f} | ضرر={loss_usdt:.2f} | ارزش نهایی={usdt:.2f}"
			)
			return
		except Exception as e:
			await message.answer(f"خطا بک‌تست: {e}")

	@dp.message(Command("optimize"))
	async def optimize(message: Message):
		parts = message.text.split()
		scope = parts[1].lower() if len(parts) > 1 else "day"
		period_map = {"hour": ("1min", 120), "day": ("5min", 288), "15d": ("15min", 1440), "month": ("1hour", 720), "2m": ("4hour", 360), "3m": ("1day", 90)}
		if scope not in period_map:
			scope = "day"
		period, limit = period_map[scope]
		kl = await cx.klines(market, period, limit)
		# fallbacks per scope
		if not kl and scope == "day":
			period, limit = "1min", 1440
			kl = await cx.klines(market, period, limit)
		if not kl and scope == "15d":
			period, limit = "30min", 720
			kl = await cx.klines(market, period, limit)
		if not kl and scope == "month":
			period, limit = "30min", 1440
			kl = await cx.klines(market, period, limit)
		if not kl and scope == "2m":
			period, limit = "2hour", 720
			kl = await cx.klines(market, period, limit)
		if not kl and scope == "3m":
			period, limit = "4hour", 540
			kl = await cx.klines(market, period, limit)
		if not kl:
			await message.answer("خطا بهینه‌سازی: داده‌ای بازنگشت")
			return
		closes = [float(k.get("close") or 0.0) for k in kl]
		if not closes:
			await message.answer("خطا بهینه‌سازی: قیمت‌های پایانی استخراج نشد")
			return
		fast_all = ema(closes, 12)
		slow_all = ema(closes, 26)
		rsi_all = rsi(closes, 14)
		atr_all = atr_series(kl, 14)
		start = 30 if len(closes) > 30 else 1
		candidates = []
		buy_rsi_list = [30, 35, 40]
		sell_rsi_list = [60, 65, 70]
		tp_mult_list = [0.4, 0.6, 0.8]
		sl_mult_list = [0.8, 1.0, 1.2]
		grids_list = [8, 10, 12]
		for buy_rsi_thr in buy_rsi_list:
			for sell_rsi_thr in sell_rsi_list:
				for tp_mult in tp_mult_list:
					for sl_mult in sl_mult_list:
						for grids in grids_list:
							usdt = 10000.0
							fee_bps = 10.0
							open_lots: List[Dict[str, float]] = []
							wins = closed = trades = 0
							last_idx = -9999
							cooldown_bars = 2
							for i in range(start, len(closes)):
								px = closes[i]
								center_bt = slow_all[i] if i < len(slow_all) else px
								atr_i = atr_all[i] if i < len(atr_all) else 0.0
								stepv = max(min(atr_i if atr_i > 0 else center_bt * step_pct, center_bt * 0.003), center_bt * 0.0005)
								# exits
								new_open: List[Dict[str, float]] = []
								for lot in open_lots:
									trail = lot.get("trail", 0.0)
									if px > lot["entry"] + atr_i * 0.5:
										trail = max(trail, px - atr_i * 0.5)
										lot["trail"] = trail
									hit_tp = px >= lot["tp"]
									hit_sl = px <= lot["sl"]
									hit_tr = trail > 0 and px <= trail
									if hit_tp or hit_sl or hit_tr:
										notional = lot["qty"] * px
										fee = notional * (fee_bps / 10000.0)
										proceeds = notional - fee
										realized = proceeds - lot["cost"]
										usdt += proceeds
										closed += 1
										trades += 1
										if realized > 0:
											wins += 1
									else:
										new_open.append(lot)
								open_lots = new_open
								# entries
								r_i = rsi_all[i] if i < len(rsi_all) else 50.0
								f_i = fast_all[i] if i < len(fast_all) else px
								s_i = slow_all[i] if i < len(slow_all) else px
								if i - last_idx >= cooldown_bars and r_i <= max(35, buy_rsi_thr - 5) and f_i < s_i:
									for j in range(grids):
										buy_lv = center_bt - stepv * (j + 1)
										if px <= buy_lv and usdt > base_amount_usdt:
											amount = base_amount_usdt
											q = amount / max(px, 1e-9)
											fee_in = amount * (fee_bps / 10000.0)
											cost = amount + fee_in
											usdt -= cost
											open_lots.append({
												"qty": q,
												"entry": px,
												"cost": cost,
												"tp": px + atr_i * tp_mult,
												"sl": px - atr_i * sl_mult,
												"trail": 0.0,
											})
											trades += 1
											last_idx = i
											break
							# finalize
							final_px = closes[-1]
							for lot in open_lots:
								notional = lot["qty"] * final_px
								fee = notional * (fee_bps / 10000.0)
								proceeds = notional - fee
								realized = proceeds - lot["cost"]
								usdt += proceeds
								closed += 1
								if realized > 0:
									wins += 1
								else:
									loss_usdt += (-(proceeds - lot["cost"]))
							win_rate = (wins / closed * 100.0) if closed else 0.0
							candidates.append({
								"buy_rsi": buy_rsi_thr,
								"sell_rsi": sell_rsi_thr,
								"tp": tp_mult,
								"sl": sl_mult,
								"grids": grids,
								"win_rate": win_rate,
								"equity": usdt,
								"trades": trades,
							})
		# sort by equity then win_rate
		candidates.sort(key=lambda x: (x["equity"], x["win_rate"]), reverse=True)
		top = candidates[:5]
		lines = [
			f"#{i+1} eq={c['equity']:.2f} wr={c['win_rate']:.1f}% tr={c['trades']} tp={c['tp']} sl={c['sl']} grids={c['grids']} rsiB={c['buy_rsi']} rsiS={c['sell_rsi']}"
			for i, c in enumerate(top)
		]
		await message.answer("بهترین پیکربندی‌ها (" + scope + ")\n" + "\n".join(lines))

	@dp.message(Command("set_amount"))
	async def set_amount(message: Message):
		parts = message.text.split()
		if len(parts) < 2:
			await message.answer("نحوه استفاده: /set_amount 100")
			return
		try:
			val = float(parts[1])
			if val <= 0:
				raise ValueError("non-positive")
		except Exception:
			await message.answer("مبلغ باید یک عدد مثبت باشد")
			return
		nonlocal base_amount_usdt
		base_amount_usdt = float(val)
		await message.answer(f"مبلغ پایه روی {base_amount_usdt:.2f} USDT تنظیم شد")

	@dp.message(Command("buy"))
	async def buy(message: Message):
		txt = await paper.buy(market, base_amount_usdt)
		await message.answer(txt)

	@dp.message(Command("sell"))
	async def sell(message: Message):
		txt = await paper.sell(market, paper.qty)
		await message.answer(txt)

	await dp.start_polling(bot)


if __name__ == "__main__":
	asyncio.run(main())