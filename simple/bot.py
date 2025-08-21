from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Awaitable, Callable

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message


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
		return r.json().get("data", [])


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
				center = p if dynamic_center and p > 0 else anchor_px
				if center > 0:
					for i in range(grid_per_side):
						buy_lv = center * (1.0 - step_pct * (i + 1))
						sell_lv = center * (1.0 + step_pct * (i + 1))
						if p <= buy_lv:
							await bot.send_message(chat_id, f"✅ BUY signal {market} @ {p:.8f} (lvl={buy_lv:.8f})")
							break
						if p >= sell_lv:
							await bot.send_message(chat_id, f"✅ SELL signal {market} @ {p:.8f} (lvl={sell_lv:.8f})")
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
		center = await cx.price(market) if dynamic_center else anchor_px
		if center <= 0:
			await message.answer("Grid not started. /grid_on")
			return
		levels_down = [center * (1.0 - step_pct * (i + 1)) for i in range(grid_per_side)]
		levels_up = [center * (1.0 + step_pct * (i + 1)) for i in range(grid_per_side)]
		text = "Buy levels:\n" + "\n".join(f"{lv:.8f}" for lv in levels_down) + "\nSell levels:\n" + "\n".join(f"{lv:.8f}" for lv in levels_up)
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