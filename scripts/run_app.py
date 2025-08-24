from __future__ import annotations

import asyncio
import os
import logging

from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from app.core.storage.db import SQLiteRepo
from app.execution.paper_exec import PaperExecutionGateway
from app.execution.real_exec_stub import RealExecutionGateway
from app.execution.coinex_exec import CoinExExecutionGateway
from app.execution.execution_router import ExecutionRouter
from app.bot.handlers import setup_handlers


async def main() -> None:
	load_dotenv()
	logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
	# enable very verbose http logs
	logging.getLogger("httpx").setLevel(logging.DEBUG)
	logging.getLogger("httpcore").setLevel(logging.DEBUG)
	logging.getLogger("aiogram").setLevel(logging.INFO)
	logging.getLogger("live_grid").setLevel(logging.INFO)
	logging.getLogger("coinex_feed").setLevel(logging.INFO)
	token = os.getenv("BOT_TOKEN")
	if not token:
		raise RuntimeError("BOT_TOKEN missing")
	start_balance = float(os.getenv("START_BALANCE_USDT", "200000"))
	fee_bps = float(os.getenv("FEE_BPS", "10"))
	slippage_bps = float(os.getenv("SLIPPAGE_BPS", "2"))

	repo = SQLiteRepo()
	repo.connect()
	paper = PaperExecutionGateway(repo, start_balance, fee_bps, slippage_bps)
	# Prefer real CoinEx gateway; fall back to stub if env keys missing
	try:
		real = CoinExExecutionGateway(repo, fee_bps_fallback=fee_bps)
	except Exception:
		real = RealExecutionGateway(repo, fee_bps)
	exec_gateway = ExecutionRouter(paper, real)

	bot = Bot(token)
	dp = Dispatcher()
	setup_handlers(dp, repo, exec_gateway)
	await dp.start_polling(bot)


if __name__ == "__main__":
	asyncio.run(main())