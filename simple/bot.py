from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Awaitable, Callable, List, Dict, Any

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
logger = logging.getLogger("simple")


COINEX_V2 = "https://api.coinex.com/v2"


class CoinExClient:
	def __init__(self) -> None:
		self.client = httpx.AsyncClient(timeout=10)

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

	async def klines(self, market: str, period: str = "5min", limit: int = 100):
		r = await self.client.get(f"{COINEX_V2}/spot/kline", params={"market": market, "period": period, "limit": limit})
		r.raise_for_status()
		data = r.json().get("data", [])
		# expect list of dicts with open/high/low/close
		return data


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
	# Wilder's RMA
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


def atr(klines: List[Dict[str, Any]], period: int = 14) -> float:
	# Expect items with high/low/close
	if len(klines) < 2:
		return 0.0
	trs: List[float] = []
	prev_close = float(klines[0].get("close") or 0.0)
	for i in range(1, len(klines)):
		h = float(klines[i].get("high") or 0.0)
		l = float(klines[i].get("low") or 0.0)
		tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
		trs.append(tr)
		prev_close = float(klines[i].get("close") or prev_close)
	if not trs:
		return 0.0
	# Smooth with EMA
	atr_series = ema(trs, period)
	return atr_series[-1] if atr_series else 0.0


class Paper:
	def __init__(self, usdt: float = 1000.0, fee_bps: float = 10.0) -> None:
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
    # Load .env so BOT_TOKEN, DEFAULT_PAIR are available without exporting each run
    load_dotenv()
	token = os.getenv("BOT_TOKEN")
	if not token:
		raise RuntimeError("BOT_TOKEN missing")
	market = os.getenv("DEFAULT_PAIR", "BTCUSDT")

	bot = Bot(token)
	dp = Dispatcher()
	cx = CoinExClient()
	paper = Paper(usdt=10000)
	grid_per_side = 6
	step_pct = 0.005
	anchor_px: float = 0.0
	dynamic_center = True
	last_signal_ts: float = 0.0
	last_signal_side: str = ""
	min_cooldown = 8.0  # seconds between signals
	k_period = "5min"
	ind = {"ema_fast": 0.0, "ema_slow": 0.0, "rsi": 50.0, "atr": 0.0}

	@dp.message(Command("start"))
	async def start(message: Message):
		await message.answer("Simple CoinEx Paper Bot online. /status /buy /sell /grid_on /grid_off /price /grid_levels")

	@dp.message(Command("status"))
	async def status(message: Message):
		await message.answer(paper.status())

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
				# refresh indicators every ~10s
				if int(time.time()) % 10 == 0:
					kl = await cx.klines(market, k_period, 120)
					closes = [float(k.get("close") or 0.0) for k in kl]
					fast = ema(closes, 12)
					slow = ema(closes, 26)
					r = rsi(closes, 14)
					a = atr(kl, 14)
					ind["ema_fast"] = fast[-1] if fast else 0.0
					ind["ema_slow"] = slow[-1] if slow else 0.0
					ind["rsi"] = r[-1] if r else 50.0
					ind["atr"] = a
				center = p if dynamic_center and p > 0 else anchor_px
				if center > 0:
					for i in range(grid_per_side):
						# ATR-informed step (clamped)
						step = ind["atr"] if ind["atr"] > 0 else (center * step_pct)
						step = max(min(step, center * 0.01), center * 0.002)
						buy_lv = center - step * (i + 1)
						sell_lv = center + step * (i + 1)
						if p <= buy_lv:
							# Filters: RSI low or uptrend
							if ind["rsi"] <= 65 and ind["ema_fast"] >= ind["ema_slow"]:
								if (time.time() - last_signal_ts) > min_cooldown or last_signal_side != "BUY":
									await bot.send_message(chat_id, f"✅ BUY {market} @ {p:.8f} | lvl={buy_lv:.8f} | RSI={ind['rsi']:.1f}")
									last_signal_ts = time.time()
									last_signal_side = "BUY"
							break
						if p >= sell_lv:
							# Filters: RSI high or downtrend
							if ind["rsi"] >= 35 and ind["ema_fast"] <= ind["ema_slow"]:
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
		# refresh ATR to show real step
		kl = await cx.klines(market, k_period, 120)
		a = atr(kl, 14)
		step = max(min(a if a > 0 else center * step_pct, center * 0.01), center * 0.002)
		levels_down = [center - step * (i + 1) for i in range(grid_per_side)]
		levels_up = [center + step * (i + 1) for i in range(grid_per_side)]
		text = f"Center: {center:.8f} | step≈{step:.8f}\n"
		text += "Buy levels:\n" + "\n".join(f"{lv:.8f}" for lv in levels_down)
		text += "\nSell levels:\n" + "\n".join(f"{lv:.8f}" for lv in levels_up)
		await message.answer(text)

	@dp.message(Command("buy"))
	async def buy(message: Message):
		txt = await paper.buy(market, 50)
		await message.answer(txt)

	@dp.message(Command("sell"))
	async def sell(message: Message):
		txt = await paper.sell(market, paper.qty)
		await message.answer(txt)

	await dp.start_polling(bot)


if __name__ == "__main__":
	asyncio.run(main())