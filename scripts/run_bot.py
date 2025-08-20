import asyncio
import os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from app.config import Settings
from app.bot.handlers import router


async def main() -> None:
    load_dotenv()
    settings = Settings.load()
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher(storage=MemoryStorage())

    # Dependency injection for settings
    dp.workflow_data.update({"settings": settings})
    dp.include_router(router)

    await dp.start_polling(bot, settings=settings)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass

