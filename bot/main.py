import logging
import os
from typing import List
from dotenv import load_dotenv

from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from .coinex_client import CoinexClient
from .demo_strategy import SimpleMAReversion
from .strategy_registry import list_strategies, get_strategy


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


HELP_TEXT = (
	"دستورات:\n"
	"/start — شروع (منوی دکمه‌ای)\n"
	"/search <query> — جستجوی نماد (مثلاً BTC, ETH, USDT)\n"
	"/ticker <market> — نمایش تیکر (مثلاً BTCUSDT)\n"
	"/klines <market> [period] [limit] — گرفتن کندل‌ها (پیش‌فرض: 1hour 100)\n"
	"/demo <market> [period] [limit] — بک‌تست دمو با استراتژی ساده روی کندل‌ها\n"
	"/strategies — فهرست استراتژی‌ها و انتخاب\n"
)


client = CoinexClient()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	keyboard = [
		[KeyboardButton(text="📊 Ticker"), KeyboardButton(text="📈 Klines")],
		[KeyboardButton(text="🔍 Search"), KeyboardButton(text="🤖 Strategies")],
	]
	reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
	await update.message.reply_text(HELP_TEXT, reply_markup=reply_markup)


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	if update.message and update.message.text and update.message.text.strip().lower() in {"🔍 search", "search"}:
		await update.message.reply_text("مثال: /search BTC")
		return
	if not context.args:
		await update.message.reply_text("مثال: /search BTC")
		return
	query = " ".join(context.args)
	markets = client.search_markets(query)
	if not markets:
		await update.message.reply_text("چیزی پیدا نشد.")
		return
	preview = "\n".join(markets[:30])
	await update.message.reply_text(f"نتایج:\n{preview}")


async def ticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	if update.message and update.message.text and update.message.text.strip().lower() in {"📊 ticker", "ticker"}:
		await update.message.reply_text("مثال: /ticker BTCUSDT")
		return
	if not context.args:
		await update.message.reply_text("مثال: /ticker BTCUSDT")
		return
	market = context.args[0].upper()
	row = client.get_spot_ticker(market)
	if not row:
		await update.message.reply_text("بازار پیدا نشد.")
		return
	await update.message.reply_text(CoinexClient.format_ticker_row(row))


async def klines(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	if update.message and update.message.text and update.message.text.strip().lower() in {"📈 klines", "klines"}:
		await update.message.reply_text("مثال: /klines BTCUSDT 1hour 50")
		return
	if not context.args:
		await update.message.reply_text("مثال: /klines BTCUSDT 1hour 50")
		return
	market = context.args[0].upper()
	period = context.args[1] if len(context.args) >= 2 else "1hour"
	limit = int(context.args[2]) if len(context.args) >= 3 else 100

	rows = client.get_kline(market=market, period=period, limit=limit)
	if not rows:
		await update.message.reply_text("کندلی پیدا نشد یا پارامتر period نامعتبر است.")
		return

	# show first few candles summary
	lines: List[str] = []
	for r in rows[:10]:
		lines.append(f"t={r.get('created_at')} o={r.get('open')} h={r.get('high')} l={r.get('low')} c={r.get('close')} v={r.get('volume')}")
	await update.message.reply_text("\n".join(lines))


async def demo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	if not context.args:
		await update.message.reply_text("مثال: /demo BTCUSDT 1hour 200")
		return
async def strategies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	items = list_strategies()
	lines = ["لیست استراتژی‌ها:"]
	for s in items:
		lines.append(f"- {s.key}: {s.name} — {s.description}")
	lines.append("\nبرای اجرا: /use <strategy_key> <market> [period] [limit]")
	await update.message.reply_text("\n".join(lines))


async def use_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	if not context.args:
		await update.message.reply_text("مثال: /use ma_reversion BTCUSDT 1hour 200")
		return
	key = context.args[0]
	info = get_strategy(key)
	if not info:
		await update.message.reply_text("استراتژی یافت نشد.")
		return
	if len(context.args) < 2:
		await update.message.reply_text("بازار را مشخص کنید. مثال: /use ma_reversion BTCUSDT 1hour 200")
		return
	market = context.args[1].upper()
	period = context.args[2] if len(context.args) >= 3 else "1hour"
	limit = int(context.args[3]) if len(context.args) >= 4 else 200

	rows = client.get_kline(market=market, period=period, limit=limit)
	if not rows or len(rows) < 15:
		await update.message.reply_text("داده‌ی کافی برای بک‌تست وجود ندارد.")
		return

	runner = info.runner_factory()
	if hasattr(runner, "run"):
		result = runner.run(market=market, klines=rows)  # type: ignore[attr-defined]
		if hasattr(runner, "format_summary"):
			text = runner.format_summary(result)  # type: ignore[attr-defined]
			await update.message.reply_text(text)
			return
	await update.message.reply_text("اجرای استراتژی انجام شد.")
	market = context.args[0].upper()
	period = context.args[1] if len(context.args) >= 2 else "1hour"
	limit = int(context.args[2]) if len(context.args) >= 3 else 200

	rows = client.get_kline(market=market, period=period, limit=limit)
	if not rows or len(rows) < 15:
		await update.message.reply_text("داده‌ی کافی برای بک‌تست وجود ندارد.")
		return

	strategy = SimpleMAReversion(window=10, threshold=0.003, position_size_usdt=100.0)
	result = strategy.run(market=market, klines=rows)
	await update.message.reply_text(SimpleMAReversion.format_summary(result))


def build_application() -> Application:
	# Load .env if present
	load_dotenv()
	telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
	if not telegram_token:
		raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is required.")

	app = Application.builder().token(telegram_token).build()
	app.add_handler(CommandHandler("start", start))
	app.add_handler(CommandHandler("help", start))
	app.add_handler(CommandHandler("search", search))
	app.add_handler(CommandHandler("ticker", ticker))
	app.add_handler(CommandHandler("klines", klines))
	app.add_handler(CommandHandler("demo", demo))
	app.add_handler(CommandHandler("strategies", strategies))
	app.add_handler(CommandHandler("use", use_strategy))
	return app


def main() -> None:
	app = build_application()
	app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
	main()

