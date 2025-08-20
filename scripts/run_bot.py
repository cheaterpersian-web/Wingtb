from __future__ import annotations

import asyncio
import logging
import os
import signal

try:
    import uvloop  # type: ignore

    uvloop.install()
except Exception:
    pass

from aiogram import Bot, Dispatcher

from app.config.settings import load_settings
from app.infra.logging import configure_logging
from app.infra.scheduler import Scheduler
from app.core.storage.db import SQLiteRepo
from app.datafeed.coinex_datafeed import CoinExDataFeed
from app.execution.paper_exec import PaperExecutionGateway
from app.bot.handlers import setup_handlers
from app.bot.notifications import Notifier
from app.services.grid_service import GridService, ServiceConfig


async def main() -> None:
    configure_logging(logging.INFO)
    settings = load_settings()

    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN missing in environment")

    repo = SQLiteRepo()
    repo.connect()

    datafeed = CoinExDataFeed()
    exec_gateway = PaperExecutionGateway(
        repo=repo,
        start_usdt=settings.start_balance_usdt,
        fee_bps=settings.fee_bps,
        slippage_bps=settings.slippage_bps,
    )

    service_cfg = ServiceConfig(
        pair=settings.default_pair,
        timeframe=settings.default_tf,
        fee_bps=settings.fee_bps,
        slippage_bps=settings.slippage_bps,
        base_order_usdt=100.0,
        lower_price=20000.0,
        upper_price=120000.0,
        grid_count=25,
        step_type="percent",
        use_rsi_filter=False,
        use_ema_filter=False,
    )
    grid_service = GridService(datafeed, repo, exec_gateway, service_cfg)

    bot = Bot(settings.bot_token)
    dp = Dispatcher()
    setup_handlers(dp, repo, exec_gateway, grid_service=grid_service)

    scheduler = Scheduler()
    scheduler.start()

    # Optional: set a chat ID for notifications via env NOTIFY_CHAT_ID
    notify_chat_id = os.getenv("NOTIFY_CHAT_ID")
    if notify_chat_id:
        notifier = Notifier(bot, notify_chat_id)

        async def trade_cb(result):
            b = exec_gateway.balances()
            await notifier.on_trade(result, service_cfg.pair, b["EQUITY"], b["WIN_RATE"])

        exec_gateway.set_trade_callback(trade_cb)

    async def runner():
        await asyncio.gather(
            dp.start_polling(bot),
            grid_service.start(),
        )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _shutdown():
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown)

    try:
        await runner()
    finally:
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())

