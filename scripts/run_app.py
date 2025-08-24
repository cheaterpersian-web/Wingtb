from __future__ import annotations

import asyncio
import os

from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from app.core.storage.db import SQLiteRepo
from app.execution.paper_exec import PaperExecutionGateway
from app.bot.handlers import setup_handlers


async def main() -> None:
	load_dotenv()
	token = os.getenv("BOT_TOKEN")
	if not token:
		raise RuntimeError("BOT_TOKEN missing")
	start_balance = float(os.getenv("START_BALANCE_USDT", "200000"))
	fee_bps = float(os.getenv("FEE_BPS", "10"))
	slippage_bps = float(os.getenv("SLIPPAGE_BPS", "2"))

	repo = SQLiteRepo()
	repo.connect()
	exec_gateway = PaperExecutionGateway(repo, start_balance, fee_bps, slippage_bps)

	bot = Bot(token)
	dp = Dispatcher()
	setup_handlers(dp, repo, exec_gateway)
	await dp.start_polling(bot)


if __name__ == "__main__":
	asyncio.run(main())