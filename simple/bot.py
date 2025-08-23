from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import List, Dict, Any

import httpx
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
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
		return f"USDT={self.usdt:.2f} QTY={self.qty:.8f} PX={self.price:.8f} EQ={equity:.2f}"


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
	k_period = "5min"
	ind = {"ema_fast": 0.0, "ema_slow": 0.0, "rsi": 50.0, "atr": 0.0}
	base_amount_usdt: float = float(os.getenv("BASE_ORDER_USDT", "50"))

	@dp.message(Command("start"))
	async def start(message: Message):
		await message.answer("Simple CoinEx Paper Bot online. /status /buy /sell /grid_on /grid_off /price /grid_levels /backtest /optimize")

	@dp.message(Command("status"))
	async def status(message: Message):
		await message.answer(paper.status() + f" | BASE={base_amount_usdt:.2f} USDT")

	@dp.message(Command("price"))
	async def cmd_price(message: Message):
		try:
			p = await cx.price(market)
			await paper.on_price(p)
			await message.answer(f"PX={p:.8f}")
		except Exception as e:
			await message.answer(f"ERR: {e}")

	running = {"on": False}

	async def loop_prices(chat_id: int):
		while running["on"]:
			try:
				p = await cx.price(market)
				await paper.on_price(p)
				logger.info("price updated: %.8f", p)
				if int(time.time()) % 10 == 0:
					kl = await cx.klines(market, k_period, 120)
					closes = [float(k.get("close") or 0.0) for k in kl]
					fast = ema(closes, 12)
					slow = ema(closes, 26)
					r = rsi(closes, 14)
					a_list = atr_series(kl, 14)
					ind["ema_fast"] = fast[-1] if fast else 0.0
					ind["ema_slow"] = slow[-1] if slow else 0.0
					ind["rsi"] = r[-1] if r else 50.0
					ind["atr"] = a_list[-1] if a_list else 0.0
				center = p if dynamic_center and p > 0 else anchor_px
				if center > 0:
					for i in range(grid_per_side):
						step = ind["atr"] if ind["atr"] > 0 else (center * step_pct)
						step = max(min(step, center * 0.003), center * 0.0005)
						buy_lv = center - step * (i + 1)
						sell_lv = center + step * (i + 1)
						if p <= buy_lv:
							if ind["rsi"] <= 35 and ind["ema_fast"] < ind["ema_slow"]:
								if (time.time() - last_signal_ts) > min_cooldown or last_signal_side != "BUY":
									await bot.send_message(chat_id, f"✅ BUY {market} @ {p:.8f} | lvl={buy_lv:.8f} | RSI={ind['rsi']:.1f}")
									last_signal_ts = time.time()
									last_signal_side = "BUY"
							break
						if p >= sell_lv:
							if ind["rsi"] >= 65 and ind["ema_fast"] > ind["ema_slow"]:
								if (time.time() - last_signal_ts) > min_cooldown or last_signal_side != "SELL":
									await bot.send_message(chat_id, f"✅ SELL {market} @ {p:.8f} | lvl={sell_lv:.8f} | RSI={ind['rsi']:.1f}")
									last_signal_ts = time.time()
									last_signal_side = "SELL"
							break
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
		running["on"] = True
		asyncio.create_task(loop_prices(message.chat.id))
		await message.answer(f"Streaming live prices… anchor={anchor_px:.8f}")

	@dp.message(Command("grid_off"))
	async def grid_off(message: Message):
		running["on"] = False
		await message.answer("Stopped.")

	@dp.message(Command("grid_levels"))
	async def grid_levels(message: Message):
		center = await cx.price(market)
		kl = await cx.klines(market, k_period, 120)
		a_list = atr_series(kl, 14)
		step_val = a_list[-1] if a_list else 0.0
		step = max(min(step_val if step_val > 0 else center * step_pct, center * 0.006), center * 0.001)
		levels_down = [center - step * (i + 1) for i in range(grid_per_side)]
		levels_up = [center + step * (i + 1) for i in range(grid_per_side)]
		text = f"Center: {center:.8f} | step≈{step:.8f}\n"
		text += "Buy levels:\n" + "\n".join(f"{lv:.8f}" for lv in levels_down)
		text += "\nSell levels:\n" + "\n".join(f"{lv:.8f}" for lv in levels_up)
		await message.answer(text)

	@dp.message(Command("backtest"))
	async def backtest(message: Message):
		parts = message.text.split()
		scope = parts[1].lower() if len(parts) > 1 else "day"
		period_map = {"hour": ("1min", 120), "day": ("5min", 288), "15d": ("30min", 720), "month": ("30min", 1440)}
		if scope not in period_map:
			scope = "day"
		period, limit = period_map[scope]
		try:
			kl = await cx.klines(market, period, limit)
			if not kl and scope == "month":
				period, limit = "15min", 1440
				kl = await cx.klines(market, period, limit)
			if not kl and scope == "15d":
				period, limit = "5min", 576
				kl = await cx.klines(market, period, limit)
			if not kl:
				await message.answer("ERR backtest: no klines returned")
				return
			closes = [float(k.get("close") or 0.0) for k in kl]
			if not closes:
				await message.answer("ERR backtest: no closes extracted")
				return
			fast = ema(closes, 12)
			slow = ema(closes, 26)
			r = rsi(closes, 14)
			a_list = atr_series(kl, 14)
			start = 30 if len(closes) > 30 else 1
			usdt = 10000.0
			fee_bps = 10.0
			base_usdt = 50.0
			open_lots: List[Dict[str, float]] = []
			wins = 0
			closed = 0
			trades = 0
			profit_usdt = 0.0
			loss_usdt = 0.0
			entries = 0
			last_idx = -9999
			cooldown_bars = 1
			for i in range(start, len(closes)):
				px = closes[i]
				center_bt = slow[i] if i < len(slow) else px
				atr_i = a_list[i] if i < len(a_list) else 0.0
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
							profit_usdt += realized
						else:
							loss_usdt += (-realized)
					else:
						new_open.append(lot)
				open_lots = new_open
				# entries
				r_i = r[i] if i < len(r) else 50.0
				f_i = fast[i] if i < len(fast) else px
				s_i = slow[i] if i < len(slow) else px
				if i - last_idx >= cooldown_bars and r_i <= 45 and f_i < s_i:
					for j in range(grid_per_side):
						buy_lv = center_bt - stepv * (j + 1)
						if px <= buy_lv and usdt > base_amount_usdt:
							amount = base_amount_usdt
							q = amount / max(px, 1e-9)
							fee_in = amount * (fee_bps / 10000.0)
							cost = amount + fee_in
							usdt -= cost
							# per lot TP/SL
							tp = px + atr_i * 0.5
							sl = px - atr_i * 0.9
							open_lots.append({"qty": q, "entry": px, "cost": cost, "tp": tp, "sl": sl, "trail": 0.0})
							trades += 1
							entries += 1
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
					profit_usdt += realized
				else:
					loss_usdt += (-(proceeds - lot["cost"]))
			win_rate = (wins / closed * 100.0) if closed else 0.0
			losers = closed - wins
			await message.answer(
				f"Backtest ({scope})\n"
				f"Entries={entries} | Exits={closed} | Wins={wins} | Losers={losers} | WinRate={win_rate:.2f}%\n"
				f"Profit={profit_usdt:.2f} | Loss={loss_usdt:.2f} | Final Equity={usdt:.2f}"
			)
		except Exception as e:
			await message.answer(f"ERR backtest: {e}")

	@dp.message(Command("optimize"))
	async def optimize(message: Message):
		parts = message.text.split()
		scope = parts[1].lower() if len(parts) > 1 else "day"
		period_map = {"hour": ("1min", 120), "day": ("5min", 288), "15d": ("30min", 720), "month": ("30min", 1440)}
		period, limit = period_map.get(scope, ("5min", 576))
		kl = await cx.klines(market, period, limit)
		if not kl and scope == "month":
			period, limit = "15min", 1440
			kl = await cx.klines(market, period, limit)
		if not kl and scope == "15d":
			period, limit = "5min", 576
			kl = await cx.klines(market, period, limit)
		if not kl:
			await message.answer("ERR optimize: no klines returned")
			return
		closes = [float(k.get("close") or 0.0) for k in kl]
		if not closes:
			await message.answer("ERR optimize: no closes extracted")
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
		await message.answer("Top configs (" + scope + ")\n" + "\n".join(lines))

	@dp.message(Command("set_amount"))
	async def set_amount(message: Message):
		parts = message.text.split()
		if len(parts) < 2:
			await message.answer("usage: /set_amount 100")
			return
		try:
			val = float(parts[1])
			if val <= 0:
				raise ValueError("non-positive")
		except Exception:
			await message.answer("amount must be a positive number")
			return
		nonlocal base_amount_usdt
		base_amount_usdt = float(val)
		await message.answer(f"Base amount set to {base_amount_usdt:.2f} USDT")

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