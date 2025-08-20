import logging
import os
from typing import List
from dotenv import load_dotenv

from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .coinex_client import CoinexClient
from .paper_grid_engine import PaperGridEngine
from .strategy_registry import list_strategies, get_strategy
from .demo_strategy import SimpleMAReversion
from .strategies.grid_config import GRID_CFG


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


HELP_TEXT = (
	"دستورات:\n"
	"/start — شروع (منوی دکمه‌ای)\n"
	"/search <query> — جستجوی نماد (مثلاً BTC, ETH, USDT)\n"
	"/ticker <market> — نمایش تیکر (مثلاً BTCUSDT)\n"
	"/klines <market> [period] [limit] — گرفتن کندل‌ها (پیش‌فرض: 1hour 100؛ دقیقه‌ای هم پشتیبانی می‌شود: 1min/5min/15min/30min)\n"
	"/demo <market> [period] [limit] — بک‌تست دمو با استراتژی ساده روی کندل‌ها\n"
	"/strategies — فهرست استراتژی‌ها و انتخاب\n"
	"/volatility <market> [period] [limit] — محاسبه نوسان سالیانه از روی کندل‌ها\n"
)


client = CoinexClient()
live_engines = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	keyboard = [
		[KeyboardButton(text="📊 Ticker"), KeyboardButton(text="📈 Klines")],
		[KeyboardButton(text="🔍 Search"), KeyboardButton(text="🤖 Strategies")],
		[KeyboardButton(text="📉 Volatility")],
		[KeyboardButton(text="▶️ Start Live"), KeyboardButton(text="⏹ Stop Live")],
		[KeyboardButton(text="ℹ️ Live Status")],
	]
	reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
	await update.message.reply_text(HELP_TEXT, reply_markup=reply_markup)


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
	if not context.args:
		await update.message.reply_text("مثال: /klines BTCUSDT 1min 50")
		return
	market = context.args[0].upper()
	period = context.args[1] if len(context.args) >= 2 else "1hour"
	limit = int(context.args[2]) if len(context.args) >= 3 else 100
	rows = client.get_kline(market=market, period=period, limit=limit)
	if not rows:
		await update.message.reply_text("کندلی پیدا نشد یا پارامتر period نامعتبر است.")
		return
	lines: List[str] = []
	for r in rows[:10]:
		lines.append(f"t={r.get('created_at')} o={r.get('open')} h={r.get('high')} l={r.get('low')} c={r.get('close')} v={r.get('volume')}")
	await update.message.reply_text("\n".join(lines))


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


def _engine_key(chat_id: int) -> str:
	return f"chat:{chat_id}"


async def start_live(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	text_label = (update.message.text.strip().lower() if update.message and update.message.text else "")
	cfg_symbol = (GRID_CFG.get("symbol") or "BTC/USDT").replace("/", "").upper()
	default_market = cfg_symbol
	default_period = "1hour"
	default_balance = 200000.0
	default_poll = int(GRID_CFG.get("poll_sec", 2))

	if not context.args or text_label in {"▶️ start live", "start live"}:
		market = default_market
		period = default_period
		balance = default_balance
		poll_sec = default_poll
	else:
		market = context.args[0].upper()
		period = context.args[1] if len(context.args) >= 2 else default_period
		balance = float(context.args[2]) if len(context.args) >= 3 else default_balance
		poll_sec = int(context.args[3]) if len(context.args) >= 4 else default_poll

	chat_id = update.effective_chat.id if update.effective_chat else 0
	key = _engine_key(chat_id)
	if key in live_engines:
		await update.message.reply_text("در حال حاضر یک موتور لایو فعال است. ابتدا آن را متوقف کنید.")
		return

	engine = PaperGridEngine(
		client,
		market,
		period=period,
		balance_usdt=balance,
		levels_per_side=int(GRID_CFG.get("levels_per_side", 6)),
		upper_pct=float(GRID_CFG.get("upper_pct", 0.03)),
		lower_pct=float(GRID_CFG.get("lower_pct", 0.03)),
		quote_per_order=float(GRID_CFG.get("quote_per_order", 20.0)),
		recenter_on_break=bool(GRID_CFG.get("recenter_on_break", True)),
		poll_sec=poll_sec,
		spacing_mode=str(GRID_CFG.get("spacing_mode", "percent")),
		step_absolute=(float(GRID_CFG.get("step_absolute")) if GRID_CFG.get("step_absolute") is not None else None),
		center_price_source=str(GRID_CFG.get("center_price_source", "last")),
		ema_len_for_center=int(GRID_CFG.get("ema_len_for_center", 20)),
		kill_switch_pct=(float(GRID_CFG.get("kill_switch_pct")) if GRID_CFG.get("kill_switch_pct") is not None else None),
		only_buy_mode=bool(GRID_CFG.get("only_buy_mode", False)),
	)
	engine.start()
	panel = await update.message.reply_text(f"Live Grid demo شروع شد روی {market} @ {period} | بالانس {balance:.2f} | poll={poll_sec}s")
	job = context.application.job_queue.run_repeating(
		_live_update_job,
		interval=poll_sec,
		first=0,
		data={
			"key": key,
			"chat_id": update.effective_chat.id,
			"message_id": panel.message_id,
			"last_trade_idx": 0,
		},
		name=key,
	)
	live_engines[key] = {"engine": engine, "job": job, "chat_id": update.effective_chat.id, "message_id": panel.message_id, "last_trade_idx": 0}


async def stop_live(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	chat_id = update.effective_chat.id if update.effective_chat else 0
	key = _engine_key(chat_id)
	rec = live_engines.pop(key, None)
	if rec:
		engine = rec.get("engine")
		job = rec.get("job")
		if job:
			job.schedule_removal()
		if engine:
			engine.stop()
		await update.message.reply_text("Live demo trading متوقف شد.")
	else:
		await update.message.reply_text("موتور فعالی پیدا نشد.")


async def live_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	chat_id = update.effective_chat.id if update.effective_chat else 0
	key = _engine_key(chat_id)
	rec = live_engines.get(key)
	if not rec:
		await update.message.reply_text("موتور فعالی پیدا نشد.")
		return
	engine = rec.get("engine")
	s = engine.snapshot()
	mkt = s.get('market') if isinstance(s, dict) else None
	prd = s.get('period') if isinstance(s, dict) else None
	t_row = client.get_spot_ticker(mkt) if mkt else None
	k_rows = client.get_kline(market=mkt, period=prd, limit=5) if (mkt and prd) else []
	lines: List[str] = []
	if isinstance(s, dict) and s.get("type") == "grid":
		lines.append(
			f"وضعیت لایو (Grid)\nبازار: {s['market']} | تایم‌فریم: {s['period']}\n"
			f"بالانس: {s['balance_usdt']:.2f} USDT | BaseQty: {s['base_qty']:.6f} @ {s['avg_cost']:.2f}\n"
			f"Open Buys: {s['open_buys']} | Open Sells: {s['open_sells']} | Trades: {s['trades']}"
		)
	if t_row:
		try:
			last = float(t_row.get('last'))
			open_p = float(t_row.get('open'))
			high = float(t_row.get('high'))
			low = float(t_row.get('low'))
			vol = float(t_row.get('volume'))
			lines.append(f"\nCoinEx Ticker: last={last} | open24h={open_p} | high24h={high} | low24h={low} | vol={vol:.2f}")
		except Exception:
			pass
	if k_rows:
		lines.append("\nآخرین کندل‌ها:")
		for r in k_rows[-3:]:
			lines.append(f"t={r.get('created_at')} o={r.get('open')} h={r.get('high')} l={r.get('low')} c={r.get('close')} v={r.get('volume')}")
	await update.message.reply_text("\n".join(lines))


async def _live_update_job(context: ContextTypes.DEFAULT_TYPE) -> None:
	data = context.job.data or {}
	key = data.get("key")
	rec = live_engines.get(key)
	if not rec:
		return
	engine = rec.get("engine")
	chat_id = rec.get("chat_id")
	message_id = rec.get("message_id")
	last_idx = rec.get("last_trade_idx", 0)
	s = engine.snapshot()
	mkt = s.get('market') if isinstance(s, dict) else None
	prd = s.get('period') if isinstance(s, dict) else None
	t_row = client.get_spot_ticker(mkt) if mkt else None
	k_rows = client.get_kline(market=mkt, period=prd, limit=5) if (mkt and prd) else []
	lines: List[str] = []
	if isinstance(s, dict) and s.get("type") == "grid":
		lines.append(
			f"Live Panel (Grid) | {mkt} @ {prd}\nBal: {s['balance_usdt']:.2f} | BaseQty: {s['base_qty']:.6f} @ {s['avg_cost']:.2f}\nOpenBuys:{s['open_buys']} OpenSells:{s['open_sells']} Trades:{s['trades']}"
		)
	if t_row:
		try:
			last = float(t_row.get('last'))
			open_p = float(t_row.get('open'))
			high = float(t_row.get('high'))
			low = float(t_row.get('low'))
			chg = (last / open_p - 1.0) * 100.0 if open_p else 0.0
			lines.append(f"Price: {last} (24h {chg:.2f}%) High: {high} Low: {low}")
		except Exception:
			pass
	if k_rows:
		lines.append("Candles:")
		for r in k_rows[-3:]:
			lines.append(f"{r.get('created_at')}: o={r.get('open')} h={r.get('high')} l={r.get('low')} c={r.get('close')} v={r.get('volume')}")
	text = "\n".join(lines)
	try:
		await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
	except Exception:
		pass
	trades = getattr(engine, 'state').trades if hasattr(engine, 'state') else []
	if isinstance(trades, list) and last_idx < len(trades):
		for t in trades[last_idx:]:
			try:
				await context.bot.send_message(chat_id=chat_id, text=f"Trade: {t.side.upper()} {t.qty_base:.6f} @ {t.price} | PnL: {t.pnl_usdt:.4f}")
			except Exception:
				pass
		rec["last_trade_idx"] = len(trades)


def build_application() -> Application:
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
	app.add_handler(CommandHandler("strategies", strategies))
	app.add_handler(CommandHandler("use", use_strategy))
	app.add_handler(CommandHandler("start_live", start_live))
	app.add_handler(CommandHandler("stop_live", stop_live))
	app.add_handler(CommandHandler("live_status", live_status))

	async def on_button_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
		if not update.message or not update.message.text:
			return
		t = update.message.text.strip()
		l = t.casefold()
		if l == "▶️ start live" or l == "start live":
			await start_live(update, context)
			return
		if l == "⏹ stop live" or l == "stop live":
			await stop_live(update, context)
			return
		if l == "ℹ️ live status" or l == "live status":
			await live_status(update, context)
			return
	app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), on_button_text))
	return app


def main() -> None:
	app = build_application()
	app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
	main()

