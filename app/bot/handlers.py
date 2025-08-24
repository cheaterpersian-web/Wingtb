from __future__ import annotations

import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from app.core.storage.db import SQLiteRepo
from app.execution.paper_exec import PaperExecutionGateway
from app.services.grid_service import GridService
from app.services.grid_service import ServiceConfig
from app.datafeed.coinex_datafeed import CoinExDataFeed
from app.services.backtest_runner import run_fixed_grid_backtest
from app.services.live_grid_engine import LiveGridEngine
from pathlib import Path


logger = logging.getLogger(__name__)


def setup_handlers(dp: Dispatcher, repo: SQLiteRepo, exec_gateway: PaperExecutionGateway, *, grid_service: GridService | None = None):
	pending_actions: dict[int, str] = {}

	# Live fixed-range grid engine (modular)
	feed = CoinExDataFeed()

	# Defaults aligned with simple version
	market = os.getenv("DEFAULT_PAIR", "TRXUSDT")
	base_amount_usdt: float = float(os.getenv("BASE_ORDER_USDT", "50"))

	@dp.callback_query(F.data == "menu:status")
	async def cb_menu_status(query: CallbackQuery):
		b = exec_gateway.balances()
		text = (
			f"موجودی={b['USDT']:.2f} USDT | مقدار={b['ASSET_QTY']:.8f} | قیمت={b['ASSET_PRICE']:.8f} | ارزش={b['EQUITY']:.2f} | مبلغ پایه={base_amount_usdt:.2f} USDT"
		)
		await query.message.answer(text)
		await query.answer()

	@dp.callback_query(F.data == "menu:price")
	async def cb_menu_price(query: CallbackQuery):
		try:
			px = await feed.now_price(market)
			await exec_gateway.on_price(float(px))
			await query.message.answer(f"قیمت={float(px):.8f}")
		except Exception as e:
			await query.message.answer(f"خطا: {e}")
		await query.answer()

	# Preset editor (like simple)
	editor: dict[int, dict[str, float | int]] = {}

	def clamp_params(gr: int, st: float, tp: float, sl: float, amt: float):
		gr = max(10, min(30, gr))
		st = max(0.003, min(0.01, st))
		tp = max(0.003, min(0.02, tp))
		sl = max(0.005, min(0.03, sl))
		amt = max(1.0, amt)
		return gr, st, tp, sl, amt

	def preset_text(p: dict[str, float | int]) -> str:
		return (
			f"ویرایش پریست\n"
			f"گریدها={p['grids']} | گام={float(p['step'])*100:.2f}% | حدسود={float(p['tp'])*100:.2f}% | حدضرر={float(p['sl'])*100:.2f}% | مبلغ={float(p['amount']):.2f} USDT"
		)

	def preset_kb(p: dict[str, float | int]) -> InlineKeyboardMarkup:
		return InlineKeyboardMarkup(inline_keyboard=[
			[InlineKeyboardButton(text="گرید -2", callback_data="edit:gr:-2"), InlineKeyboardButton(text="گرید +2", callback_data="edit:gr:+2")],
			[InlineKeyboardButton(text="گام -0.1%", callback_data="edit:step:-0.001"), InlineKeyboardButton(text="گام +0.1%", callback_data="edit:step:+0.001")],
			[InlineKeyboardButton(text="حدسود -0.2%", callback_data="edit:tp:-0.002"), InlineKeyboardButton(text="حدسود +0.2%", callback_data="edit:tp:+0.002")],
			[InlineKeyboardButton(text="حدضرر -0.2%", callback_data="edit:sl:-0.002"), InlineKeyboardButton(text="حدضرر +0.2%", callback_data="edit:sl:+0.002")],
			[InlineKeyboardButton(text="مبلغ -10", callback_data="edit:amt:-10"), InlineKeyboardButton(text="مبلغ +10", callback_data="edit:amt:+10")],
			[InlineKeyboardButton(text="شروع ▶️", callback_data="edit:start"), InlineKeyboardButton(text="انصراف ❌", callback_data="edit:cancel")],
		])

	@dp.callback_query(F.data.startswith("preset:"))
	async def cb_preset(query: CallbackQuery):
		parts = query.data.split(":")
		gr = int(parts[1]); st = float(parts[2]); tp = float(parts[3]); sl = float(parts[4])
		g2, st2, tp2, sl2, amt2 = clamp_params(gr, st, tp, sl, base_amount_usdt)
		editor[query.message.chat.id] = {"grids": g2, "step": st2, "tp": tp2, "sl": sl2, "amount": amt2}
		p = editor[query.message.chat.id]
		await query.message.answer(preset_text(p), reply_markup=preset_kb(p))
		await query.answer()

	@dp.callback_query(F.data.startswith("edit:"))
	async def cb_edit(query: CallbackQuery):
		p = editor.get(query.message.chat.id) or {"grids": 20, "step": 0.005, "tp": 0.01, "sl": 0.01, "amount": base_amount_usdt}
		cmd = query.data
		try:
			if cmd == "edit:start":
				info = await engine.start(query.message.chat.id, int(p["grids"]), float(p["step"]), float(p["tp"]), float(p["sl"]), float(p["amount"]))
				editor.pop(query.message.chat.id, None)
				await query.message.answer(
					f"گرید روشن شد (۱۵ دقیقه) | مرکز={info['center']:.8f} | گریدها={info['grids_total']} | گام={info['step_pct']*100:.2f}% | حدسود={info['tp_pct']*100:.2f}% | حدضرر={info['sl_pct']*100:.2f}% | مبلغ={info['amount']:.2f}"
				)
				await query.answer("گرید شروع شد")
				return
			if cmd == "edit:cancel":
				editor.pop(query.message.chat.id, None)
				await query.message.answer("ویرایش پریست لغو شد.")
				await query.answer()
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
			p["grids"], p["step"], p["tp"], p["sl"], p["amount"] = clamp_params(int(p["grids"]), float(p["step"]), float(p["tp"]), float(p["sl"]), float(p["amount"]))
			editor[query.message.chat.id] = p
			await query.message.edit_text(preset_text(p), reply_markup=preset_kb(p))
			await query.answer("به‌روزرسانی شد")
		except Exception:
			await query.answer("خطا", show_alert=False)

	# Pair selector like simple
	@dp.callback_query(F.data.startswith("pair:"))
	async def cb_pair(query: CallbackQuery):
		nonlocal market
		try:
			parts = query.data.split(":")
			action = parts[1] if len(parts) > 1 else ""
			PAIRS = ["TRXUSDT","BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","BNBUSDT","TONUSDT","LINKUSDT","LTCUSDT","OPUSDT","ARBUSDT","TIAUSDT","AVAXUSDT","NEARUSDT"]
			if action in ("open", "page"):
				page = int(parts[2]) if len(parts) > 2 else 0
				page_size = 6
				total_pages = (len(PAIRS) + page_size - 1) // page_size
				page = max(0, min(total_pages - 1, page))
				start_i = page * page_size
				page_pairs = PAIRS[start_i : start_i + page_size]
				rows = [[InlineKeyboardButton(text=sym, callback_data=f"pair:set:{sym}")] for sym in page_pairs]
				nav = []
				if page > 0:
					nav.append(InlineKeyboardButton(text="◀️ قبلی", callback_data=f"pair:page:{page-1}"))
				if page < total_pages - 1:
					nav.append(InlineKeyboardButton(text="بعدی ▶️", callback_data=f"pair:page:{page+1}"))
				bottom = [
					InlineKeyboardButton(text="ورود دستی 📝", callback_data="pair:manual"),
					InlineKeyboardButton(text="انصراف ❌", callback_data="pair:cancel"),
				]
				inline = rows + ([nav] if nav else []) + [bottom]
				kb = InlineKeyboardMarkup(inline_keyboard=inline)
				await query.message.answer(f"نماد فعلی: {market}\nیک نماد را انتخاب کنید:", reply_markup=kb)
				await query.answer()
				return
			if action == "manual":
				await query.message.answer("برای تنظیم نماد دلخواه، دستور زیر را ارسال کنید:\n/set_pair TRXUSDT")
				await query.answer()
				return
			if action == "cancel":
				await query.message.answer("انتخاب نماد لغو شد.")
				await query.answer()
				return
			if action == "set" and len(parts) > 2:
				sym = parts[2].upper()
				try:
					px = await feed.now_price(sym)
				except Exception:
					await query.answer("نماد نامعتبر یا در دسترس نیست", show_alert=True)
					return
				# stop engine and update market
				try:
					await engine.stop()
					await query.message.answer("گرید متوقف شد به‌خاطر تغییر نماد.")
				except Exception:
					pass
				market = sym
				try:
					engine.set_market(sym)
				except Exception:
					pass
				# optionally reconfigure service
				if grid_service is not None:
					cfg = grid_service.cfg
					new_cfg = ServiceConfig(
						pair=sym,
						timeframe=cfg.timeframe,
						fee_bps=cfg.fee_bps,
						slippage_bps=cfg.slippage_bps,
						base_order_usdt=cfg.base_order_usdt,
						lower_price=cfg.lower_price,
						upper_price=cfg.upper_price,
						grid_count=cfg.grid_count,
						step_type=cfg.step_type,
						use_rsi_filter=cfg.use_rsi_filter,
						use_ema_filter=cfg.use_ema_filter,
					)
					try:
						await grid_service.reconfigure(new_cfg)
					except Exception:
						pass
				await exec_gateway.on_price(float(px))
				await query.message.answer(f"نماد به {market} تغییر کرد. قیمت فعلی={float(px):.8f}\nبرای شروع، «روشن کردن گرید ▶️» را بزنید.")
				await query.answer("نماد تنظیم شد")
				return
		except Exception:
			await query.answer("خطا", show_alert=False)

	async def _notify(chat_id: int, text: str):
		try:
			cfg = await repo.get_settings()
			admin_id_val = (cfg.get("admin_chat_id") or "").strip()
			send_id = int(admin_id_val) if str(admin_id_val).isdigit() else chat_id
		except Exception:
			send_id = chat_id
		try:
			bot = dp.bot  # type: ignore[attr-defined]
			await bot.send_message(send_id, text)
		except Exception:
			pass

	async def _get_admin_id() -> int | None:
		try:
			cfg = await repo.get_settings()
			val = (cfg.get("admin_chat_id") or "").strip()
			return int(val) if str(val).isdigit() else None
		except Exception:
			return None

	async def _reply(message: Message, text: str, reply_markup=None):
		ok = True
		try:
			await message.answer(text, reply_markup=reply_markup)
		except Exception:
			ok = False
		aid = await _get_admin_id()
		if aid and aid != message.chat.id:
			try:
				prefix = "" if ok else f"[relay from chat {message.chat.id}]\n"
				bot = dp.bot  # type: ignore[attr-defined]
				await bot.send_message(aid, prefix + text)
			except Exception:
				pass

	engine = LiveGridEngine(get_price=feed.now_price, paper=exec_gateway, notify=_notify)
	if grid_service is not None:
		try:
			engine.set_market(grid_service.cfg.pair)
		except Exception:
			pass

	@dp.message(Command("start"))
	async def cmd_start(message: Message):
		kb = InlineKeyboardMarkup(inline_keyboard=[
			[InlineKeyboardButton(text="وضعیت 💼", callback_data="menu:status"), InlineKeyboardButton(text="قیمت ⚡", callback_data="menu:price")],
			[InlineKeyboardButton(text="روشن کردن گرید ▶️", callback_data="grid:on"), InlineKeyboardButton(text="خاموش کردن گرید ⏹", callback_data="grid:off")],
			[InlineKeyboardButton(text="سطوح گرید 📐", callback_data="grid:levels")],
			[InlineKeyboardButton(text="انتخاب ارز 🎯", callback_data="pair:open:0")],
			[InlineKeyboardButton(text="تنظیم API صرافی CoinEx 🔐", callback_data="env:open")],
			[InlineKeyboardButton(text="پریست: 20 گرید، 0.5% گام، 1% حدسود/حدضرر", callback_data="preset:20:0.005:0.01:0.01")],
		])
		mode_label = "حالت: اصلی" if getattr(exec_gateway, "get_mode", lambda: "paper")() == "real" else "حالت: آزمایشی"
		kb.inline_keyboard.insert(2, [InlineKeyboardButton(text=mode_label + " 🔁", callback_data="mode:toggle")])
		# Compose intro with training note and API status
		intro = ""
		env_access = os.getenv("COINEX_ACCESS_ID", "").strip()
		env_secret = os.getenv("COINEX_SECRET_KEY", "").strip()
		if env_access and env_secret:
			b = exec_gateway.balances()
			fee = getattr(exec_gateway, "fee_bps", 0.0)
			intro += (
				"🔐 تنظیم API: فعال (از فایل .env)\n"
				"صرافی: CoinEx\n"
				f"کارمزد اسپات: {fee:.2f} بیس‌پوینت\n"
				"وضعیت حساب (آزمایشی):\n"
				f"  - USDT: {b['USDT']:.2f}\n"
				f"  - دارایی: {b['ASSET_QTY']:.8f}\n"
				f"  - قیمت: {b['ASSET_PRICE']:.8f}\n"
				f"  - ارزش کل: {b['EQUITY']:.2f}\n\n"
				"ویرایش API:\n"
				"  - از طریق ربات @wingtbbot، بخش «ربات‌های من»\n"
			)
		else:
			intro += "❗️ API تنظیم نشده است. لطفاً از طریق ربات @wingtbbot در بخش «ربات‌های من» آن را تنظیم کنید.\n\n"
		await _reply(message, intro, reply_markup=kb)

	@dp.message(Command("status"))
	async def cmd_status(message: Message):
		b = exec_gateway.balances()
		text = (
			f"USDT={b['USDT']:.2f}, ASSET_QTY={b['ASSET_QTY']:.6f}, PRICE={b['ASSET_PRICE']:.2f}, AVG_COST={b.get('AVG_COST',0):.2f}\n"
			f"EQUITY={b['EQUITY']:.2f}, PNL_REAL={b['PNL_REALIZED']:.2f}, PNL_UNREAL={b.get('PNL_UNREALIZED',0):.2f}, WIN_RATE={b['WIN_RATE']:.2f}%\n"
			f"MAX_DD={b['MAX_DRAWDOWN']:.2f}"
		)
		await _reply(message, text)

	@dp.message(Command("history"))
	async def cmd_history(message: Message):
		rows = await repo.fetch_trades(10)
		if not rows:
			await message.answer("No trades yet")
			return
		lines = [
			f"{r.ts} {r.side} {r.pair} px={r.price:.2f} qty={r.qty:.6f} fee={r.fee:.4f} pnl={r.pnl_realized:.2f}"
			for r in rows
		]
		await message.answer("\n".join(lines))

	@dp.message(Command("export_csv"))
	async def cmd_export(message: Message):
		path = await repo.export_trades_csv("/workspace/exports/trades.csv")
		await message.answer(f"Exported to {path}")

	@dp.message(Command("backtest"))
	async def cmd_backtest(message: Message):
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
		tf, limit = period_map[scope]
		feed = CoinExDataFeed()
		try:
			candles = await feed.get_klines(market, tf, limit)
			# fallbacks per scope like simple
			if (not candles) and scope == "day":
				candles = await feed.get_klines(market, "1min", 1440)
			if (not candles) and scope == "15d":
				candles = await feed.get_klines(market, "30min", 720)
			if (not candles) and scope == "month":
				candles = await feed.get_klines(market, "30min", 1440)
			if (not candles) and scope == "month":
				candles = await feed.get_klines(market, "2hour", 360)
			if (not candles) and scope == "2m":
				candles = await feed.get_klines(market, "2hour", 720)
			if (not candles) and scope == "2m":
				candles = await feed.get_klines(market, "1hour", 1440)
			if (not candles) and scope == "3m":
				candles = await feed.get_klines(market, "4hour", 540)
			if (not candles) and scope == "3m":
				candles = await feed.get_klines(market, "1hour", 2160)
			closes: list[float] = []
			for c in candles or []:
				v = None
				if isinstance(c, dict):
					v = c.get("close") or c.get("c") or c.get("last") or c.get("price")
				elif isinstance(c, (list, tuple)) and len(c) >= 3:
					v = c[2]
				if v is not None:
					try:
						closes.append(float(v))
					except Exception:
						pass
			if not closes:
				await message.answer("خطا بک‌تست: داده‌ای بازنگشت")
				return
			# Only grid mode supported now (default to grid)
			if not mode_grid:
				mode_grid = True
			# parse args like simple: /backtest grid <scope> [lower upper grids tp_pct amount]
			def _get(idx: int, cast):
				try:
					return cast(parts[idx])
				except Exception:
					return None
			arg_start = 3 if len(parts) > 1 and parts[1].lower() == "grid" else 2
			lb = _get(arg_start, float) or (min(closes) * 0.99)
			ub = _get(arg_start + 1, float) or (max(closes) * 1.01)
			grids = int(_get(arg_start + 2, int) or 20)
			tp_pct = (_get(arg_start + 3, float) or 1.0) / 100.0
			amount = float(_get(arg_start + 4, float) or base_amount_usdt)
			res = run_fixed_grid_backtest(closes, lb, ub, grids, tp_pct, amount)
			if res.get("error"):
				await message.answer(f"خطا بک‌تست: {res['error']}")
				return
			await message.answer(
				f"بک‌تست ({scope})\n"
				f"ورودها={res['entries']} | خروج‌ها={res['exits']} | بردها={res['wins']} | باخت‌ها={res['losers']} | بستن اجباری={res['forced_exits']} | نرخ برد={res['win_rate']:.2f}%\n"
				f"سود={res['profit_usdt']:.2f} | ضرر={res['loss_usdt']:.2f} | ارزش نهایی={res['final_equity']:.2f}"
			)
		except Exception as e:
			await message.answer(f"خطا بک‌تست: {e}")

	def _format_strategy(cfg_override: ServiceConfig | None = None) -> str:
		if grid_service is None:
			return "Service not available"
		cfg = cfg_override or grid_service.cfg
		p = grid_service.strategy.params if hasattr(grid_service, "strategy") else None
		use_rsi = cfg.use_rsi_filter
		use_ema = cfg.use_ema_filter
		return (
			f"استراتژی: Grid\n"
			f"جفت: {cfg.pair} | تایم‌فریم: {cfg.timeframe}\n"
			f"بازه قیمت: {cfg.lower_price:.2f} → {cfg.upper_price:.2f}\n"
			f"تعداد گرید: {cfg.grid_count} | گام: {cfg.step_type}\n"
			f"سفارش پایه (USDT): {cfg.base_order_usdt:.2f}\n"
			f"کارمزد (bps): {cfg.fee_bps:.2f} | اسلیپیج (bps): {cfg.slippage_bps:.2f}\n"
			f"فیلتر RSI: {'فعال' if use_rsi else 'غیرفعال'} (خرید < 30 ، فروش > 70)\n"
			f"فیلتر EMA: {'فعال' if use_ema else 'غیرفعال'} (خرید: EMA12 < EMA26 ، فروش: EMA12 > EMA26)\n"
		)

	async def _format_dashboard() -> str:
		if grid_service is None:
			return "Service not available"
		cfg = grid_service.cfg
		b = exec_gateway.balances()
		# last 5 trades
		rows = await repo.fetch_trades(5)
		trades_text = "\n".join(
			[
				f"{r.ts} {r.side} {r.pair} px={r.price:.2f} qty={r.qty:.6f} fee={r.fee:.4f} pnl={r.pnl_realized:.2f}"
				for r in rows
			]
		) if rows else "No trades yet"
		return (
			f"وضعیت گرید (روشن):\n"
			f"جفت: {cfg.pair} | TF: {cfg.timeframe}\n"
			f"Range: {cfg.lower_price:.2f} → {cfg.upper_price:.2f} | Grids/side: {cfg.grid_count} | Step: {cfg.step_type}\n"
			f"Base USDT: {cfg.base_order_usdt:.2f} | Fee bps: {cfg.fee_bps:.2f} | Slippage bps: {cfg.slippage_bps:.2f}\n"
			f"USDT={b['USDT']:.2f} | QTY={b['ASSET_QTY']:.6f} | Price={b['ASSET_PRICE']:.2f} | Equity={b['EQUITY']:.2f}\n"
			f"RealPNL={b['PNL_REALIZED']:.2f} | UnrlPNL={b.get('PNL_UNREALIZED',0):.2f} | WinRate={b['WIN_RATE']:.2f}%\n"
			f"— آخرین معاملات —\n{trades_text}"
		)

	@dp.callback_query(F.data == "show_dashboard")
	async def cb_show_dashboard(query: CallbackQuery):
		text = await _format_dashboard()
		await query.message.answer(text)
		await query.answer()

	@dp.message(Command("strategy"))
	async def cmd_strategy(message: Message):
		await message.answer(_format_strategy())

	@dp.callback_query(F.data == "show_strategy")
	async def cb_show_strategy(query: CallbackQuery):
		await query.message.answer(_format_strategy())
		await query.answer()

	@dp.callback_query(F.data == "show_status")
	async def cb_show_status(query: CallbackQuery):
		b = exec_gateway.balances()
		text = (
			f"USDT={b['USDT']:.2f}, ASSET_QTY={b['ASSET_QTY']:.6f}, PRICE={b['ASSET_PRICE']:.2f}, AVG_COST={b.get('AVG_COST',0):.2f}\n"
			f"EQUITY={b['EQUITY']:.2f}, PNL_REAL={b['PNL_REALIZED']:.2f}, PNL_UNREAL={b.get('PNL_UNREALIZED',0):.2f}, WIN_RATE={b['WIN_RATE']:.2f}%\n"
			f"MAX_DD={b['MAX_DRAWDOWN']:.2f}"
		)
		await query.message.answer(text)
		await query.answer()

	@dp.callback_query(F.data == "show_history")
	async def cb_show_history(query: CallbackQuery):
		rows = await repo.fetch_trades(10)
		if not rows:
			await query.message.answer("No trades yet")
			await query.answer()
			return
		lines = [
			f"{r.ts} {r.side} {r.pair} px={r.price:.2f} qty={r.qty:.6f} fee={r.fee:.4f} pnl={r.pnl_realized:.2f}"
			for r in rows
		]
		await query.message.answer("\n".join(lines))
		await query.answer()

	@dp.callback_query(F.data == "clear_history")
	async def cb_clear_history(query: CallbackQuery):
		await query.answer("در حال پاک‌سازی…")
		async def run():
			await repo.clear_history()
			await query.message.answer("تاریخچه پاک شد.")
		asyncio.create_task(run())

	def _strategy_menu_kb(cfg_override: ServiceConfig | None = None):
		if grid_service is None:
			return InlineKeyboardMarkup(inline_keyboard=[])
		cfg = cfg_override or grid_service.cfg
		rsi_label = "خاموش کردن RSI" if cfg.use_rsi_filter else "روشن کردن RSI"
		ema_label = "خاموش کردن EMA" if cfg.use_ema_filter else "روشن کردن EMA"
		step_label = "گام: درصدی" if cfg.step_type == "percent" else "گام: ثابت"
		return InlineKeyboardMarkup(inline_keyboard=[
			[InlineKeyboardButton(text=rsi_label, callback_data="toggle_rsi")],
			[InlineKeyboardButton(text=ema_label, callback_data="toggle_ema")],
			[InlineKeyboardButton(text=step_label, callback_data="toggle_step")],
			[InlineKeyboardButton(text="معامله تستی", callback_data="test_trade"), InlineKeyboardButton(text="فروش تستی", callback_data="test_sell")],
			[InlineKeyboardButton(text="ویرایش پارامترها", callback_data="edit_params")],
		])

	@dp.callback_query(F.data == "test_trade")
	async def cb_test_trade(query: CallbackQuery):
		if grid_service is None:
			await query.answer("Service not available", show_alert=True)
			return
		cfg = grid_service.cfg
		try:
			price = await grid_service.datafeed.now_price(cfg.pair)  # type: ignore[attr-defined]
		except Exception:
			price = exec_gateway.last_price if getattr(exec_gateway, 'last_price', 0.0) else 0.0
		if price <= 0.0:
			await query.answer("قیمت در دسترس نیست", show_alert=True)
			return
		qty = max(cfg.base_order_usdt / float(price), 0.000001)
		await query.answer("در حال اجرای معامله تست…")

		async def _run():
			try:
				# ensure engine has current price for proper snapshot/metrics
				await exec_gateway.on_price(float(price))
				res = await exec_gateway.place_order(cfg.pair, "BUY", qty, float(price))
				b = exec_gateway.balances()
				text = (
					f"✅ معامله تستی BUY {cfg.pair} | qty={res.qty:.6f} | price={res.price:.2f} | "
					f"fee={res.fee:.4f} | pnl={res.pnl_realized:.2f}\n"
					f"Equity={b['EQUITY']:.2f}, WinRate={b['WIN_RATE']:.2f}%"
				)
				await query.message.answer(text)
			except Exception as e:
				await query.message.answer(f"❌ خطا در معامله تستی: {e}")

		asyncio.create_task(_run())

	@dp.callback_query(F.data == "test_sell")
	async def cb_test_sell(query: CallbackQuery):
		if grid_service is None:
			await query.answer("Service not available", show_alert=True)
			return
		cfg = grid_service.cfg
		try:
			price = await grid_service.datafeed.now_price(cfg.pair)  # type: ignore[attr-defined]
		except Exception:
			price = exec_gateway.last_price if getattr(exec_gateway, 'last_price', 0.0) else 0.0
		if price <= 0.0:
			await query.answer("قیمت در دسترس نیست", show_alert=True)
			return
		base_qty = max(cfg.base_order_usdt / float(price), 0.00000001)
		avail = getattr(exec_gateway, 'asset_qty', 0.0)
		qty = min(avail, base_qty)
		if qty <= 0:
			await query.answer("موجودی برای فروش وجود ندارد", show_alert=True)
			return
		await query.answer("در حال اجرای فروش تست…")

		async def _run():
			try:
				await exec_gateway.on_price(float(price))
				res = await exec_gateway.place_order(cfg.pair, "SELL", qty, float(price))
				b = exec_gateway.balances()
				text = (
					f"✅ فروش تستی SELL {cfg.pair} | qty={res.qty:.6f} | price={res.price:.2f} | "
					f"fee={res.fee:.4f} | pnl={res.pnl_realized:.2f}\n"
					f"Equity={b['EQUITY']:.2f}, WinRate={b['WIN_RATE']:.2f}%"
				)
				await query.message.answer(text)
			except Exception as e:
				await query.message.answer(f"❌ خطا در فروش تستی: {e}")

		asyncio.create_task(_run())

	@dp.callback_query(F.data == "edit_strategy")
	async def cb_edit_strategy(query: CallbackQuery):
		await query.message.answer(_format_strategy(), reply_markup=_strategy_menu_kb())
		await query.answer()

	@dp.callback_query(F.data == "toggle_rsi")
	async def cb_toggle_rsi(query: CallbackQuery):
		if grid_service is None:
			await query.answer("Service not available", show_alert=True)
			return
		cfg = grid_service.cfg
		new_cfg = ServiceConfig(
			pair=cfg.pair,
			timeframe=cfg.timeframe,
			fee_bps=cfg.fee_bps,
			slippage_bps=cfg.slippage_bps,
			base_order_usdt=cfg.base_order_usdt,
			lower_price=cfg.lower_price,
			upper_price=cfg.upper_price,
			grid_count=cfg.grid_count,
			step_type=cfg.step_type,
			use_rsi_filter=not cfg.use_rsi_filter,
			use_ema_filter=cfg.use_ema_filter,
		)
		await query.answer("در حال به‌روزرسانی…")
		async def apply():
			# update local state early so UI reflects immediately
			grid_service.cfg = new_cfg  # type: ignore[attr-defined]
			await grid_service.reconfigure(new_cfg)
			# try to update the same message UI
			try:
				await query.message.edit_text(_format_strategy(new_cfg), reply_markup=_strategy_menu_kb(new_cfg))
			except Exception:
				await query.message.answer("وضعیت RSI تغییر کرد", reply_markup=_strategy_menu_kb(new_cfg))
		asyncio.create_task(apply())

	@dp.callback_query(F.data == "toggle_ema")
	async def cb_toggle_ema(query: CallbackQuery):
		if grid_service is None:
			await query.answer("Service not available", show_alert=True)
			return
		cfg = grid_service.cfg
		new_cfg = ServiceConfig(
			pair=cfg.pair,
			timeframe=cfg.timeframe,
			fee_bps=cfg.fee_bps,
			slippage_bps=cfg.slippage_bps,
			base_order_usdt=cfg.base_order_usdt,
			lower_price=cfg.lower_price,
			upper_price=cfg.upper_price,
			grid_count=cfg.grid_count,
			step_type=cfg.step_type,
			use_rsi_filter=cfg.use_rsi_filter,
			use_ema_filter=not cfg.use_ema_filter,
		)
		await query.answer("در حال به‌روزرسانی…")
		async def apply():
			grid_service.cfg = new_cfg  # type: ignore[attr-defined]
			await grid_service.reconfigure(new_cfg)
			try:
				await query.message.edit_text(_format_strategy(new_cfg), reply_markup=_strategy_menu_kb(new_cfg))
			except Exception:
				await query.message.answer("وضعیت EMA تغییر کرد", reply_markup=_strategy_menu_kb(new_cfg))
		asyncio.create_task(apply())

	@dp.callback_query(F.data == "toggle_step")
	async def cb_toggle_step(query: CallbackQuery):
		if grid_service is None:
			await query.answer("Service not available", show_alert=True)
			return
		cfg = grid_service.cfg
		new_step = "fixed" if cfg.step_type == "percent" else "percent"
		new_cfg = ServiceConfig(
			pair=cfg.pair,
			timeframe=cfg.timeframe,
			fee_bps=cfg.fee_bps,
			slippage_bps=cfg.slippage_bps,
			base_order_usdt=cfg.base_order_usdt,
			lower_price=cfg.lower_price,
			upper_price=cfg.upper_price,
			grid_count=cfg.grid_count,
			step_type=new_step,
			use_rsi_filter=cfg.use_rsi_filter,
			use_ema_filter=cfg.use_ema_filter,
		)
		await query.answer("در حال به‌روزرسانی…")
		async def apply():
			grid_service.cfg = new_cfg  # type: ignore[attr-defined]
			await grid_service.reconfigure(new_cfg)
			try:
				await query.message.edit_text(_format_strategy(new_cfg), reply_markup=_strategy_menu_kb(new_cfg))
			except Exception:
				await query.message.answer(f"گام به {('درصدی' if new_step=='percent' else 'ثابت')} تغییر کرد", reply_markup=_strategy_menu_kb(new_cfg))
		asyncio.create_task(apply())

	@dp.callback_query(F.data == "edit_params")
	async def cb_edit_params(query: CallbackQuery):
		pending_actions[query.from_user.id] = "await_params"
		await query.message.answer("مقادیر را به این شکل بفرست:\n/set_grid lower upper grids base_order_usdt fee_bps slippage_bps\nمثال:\n/set_grid 30000 70000 20 100 10 2")
		await query.answer()

	@dp.message(F.text)
	async def maybe_params(message: Message):
		uid = message.from_user.id if message.from_user else None
		if uid is None:
			return
		if pending_actions.get(uid) != "await_params":
			return
		# Allow user to paste either full /set_grid or just values
		parts = message.text.strip().split()
		if parts and parts[0] == "/set_grid":
			parts = parts[1:]
		if len(parts) != 6:
			await message.answer("فرمت نادرست است. نمونه: 30000 70000 20 100 10 2")
			return
		lower, upper, grids, base_usdt, fee_bps, slip_bps = parts
		if grid_service is None:
			await message.answer("Service not available")
			return
		cfg = grid_service.cfg
		new_cfg = ServiceConfig(
			pair=cfg.pair,
			timeframe=cfg.timeframe,
			fee_bps=float(fee_bps),
			slippage_bps=float(slip_bps),
			base_order_usdt=float(base_usdt),
			lower_price=float(lower),
			upper_price=float(upper),
			grid_count=int(grids),
			step_type=cfg.step_type,
			use_rsi_filter=cfg.use_rsi_filter,
			use_ema_filter=cfg.use_ema_filter,
		)
		await grid_service.reconfigure(new_cfg)
		pending_actions.pop(uid, None)
		await message.answer("تنظیمات به‌روزرسانی شد. /strategy")

	async def _compute_auto_cfg_async() -> ServiceConfig:
		cfg = grid_service.cfg  # type: ignore[attr-defined]
		grid_per_side = 6  # 12 total
		step_pct = 0.005
		last_px = getattr(exec_gateway, 'last_price', 0.0)
		if not last_px and grid_service is not None:
			try:
				last_px = float(await grid_service.datafeed.now_price(cfg.pair))  # type: ignore[attr-defined]
			except Exception:
				last_px = 0.0
		if last_px <= 0:
			last_px = 1.0
		lower = last_px * (1.0 - step_pct * grid_per_side)
		upper = last_px * (1.0 + step_pct * grid_per_side)
		usdt_bal = exec_gateway.usdt_balance if hasattr(exec_gateway, 'usdt_balance') else 100.0
		base_usdt = max(5.0, min(usdt_bal * 0.001, 100.0))
		return ServiceConfig(
			pair=cfg.pair,
			timeframe=cfg.timeframe,
			fee_bps=cfg.fee_bps,
			slippage_bps=cfg.slippage_bps,
			base_order_usdt=base_usdt,
			lower_price=lower,
			upper_price=upper,
			grid_count=grid_per_side,
			step_type="percent",
			use_rsi_filter=cfg.use_rsi_filter,
			use_ema_filter=cfg.use_ema_filter,
		)

	@dp.message(Command("grid_on"))
	async def cmd_grid_on(message: Message):
		if grid_service is None:
			await message.answer("Service not available")
			# ادامه با موتور لایو ساده
		try:
			await message.answer("در حال روشن کردن…")
			async def run():
				try:
					# stop old service if running
					try:
						await grid_service.stop()  # type: ignore[union-attr]
					except Exception:
						pass
					# defaults: 6 per side (12 total), 0.5% step, TP/SL=1%
					info = await engine.start(message.chat.id, grids_n=6, step_p=0.005, tp_p=0.01, sl_p=0.01, amount=max(5.0, exec_gateway.usdt_balance * 0.001) if hasattr(exec_gateway, 'usdt_balance') else 50.0)
					await message.answer(
						f"گرید روشن شد ✅ (حالت ثابت)\nمرکز={info['center']:.4f} | خطوط={info['grids_total']} | گام={info['step_pct']*100:.2f}% | TP/SL={info['tp_pct']*100:.2f}%/{info['sl_pct']*100:.2f}%"
					)
				except Exception as e:
					await message.answer(f"❌ خطا در روشن‌کردن گرید: {e}")
			asyncio.create_task(run())
		except Exception as e:
			await message.answer(f"❌ خطا در روشن‌کردن گرید: {e}")

	@dp.callback_query(F.data == "grid_on_btn")
	async def cb_grid_on(query: CallbackQuery):
		if grid_service is None:
			await query.answer("Service not available", show_alert=False)
		await query.answer("در حال روشن کردن…")
		await query.message.answer("در حال روشن کردن…")
		async def run():
			try:
				try:
					await grid_service.stop()  # type: ignore[union-attr]
				except Exception:
					pass
				info = await engine.start(query.message.chat.id, grids_n=6, step_p=0.005, tp_p=0.01, sl_p=0.01, amount=max(5.0, exec_gateway.usdt_balance * 0.001) if hasattr(exec_gateway, 'usdt_balance') else 50.0)
				await query.message.answer(
					f"گرید روشن شد ✅ (حالت ثابت)\nمرکز={info['center']:.4f} | خطوط={info['grids_total']} | گام={info['step_pct']*100:.2f}% | TP/SL={info['tp_pct']*100:.2f}%/{info['sl_pct']*100:.2f}%"
				)
			except Exception as e:
				await query.message.answer(f"❌ خطا در روشن‌کردن گرید: {e}")
		asyncio.create_task(run())

	@dp.message(Command("grid_off"))
	async def cmd_grid_off(message: Message):
		if grid_service is None:
			await message.answer("Service not available")
		try:
			await grid_service.stop()
		except Exception:
			pass
		try:
			await engine.stop()
		except Exception:
			pass
		await message.answer("گرید متوقف شد")

	@dp.message(Command("grid_levels"))
	async def cmd_grid_levels(message: Message):
		try:
			await message.answer(engine.levels_text())
		except Exception as e:
			await message.answer(f"خطا: {e}")

	@dp.message(Command("reset_demo"))
	async def cmd_reset(message: Message):
		exec_gateway.reset(200000.0)
		await message.answer("Demo reset.")

	@dp.message(Command("set_pair"))
	async def cmd_set_pair(message: Message):
		if grid_service is None:
			await message.answer("Service not available")
			return
		args = message.text.split()
		if len(args) != 2:
			await message.answer("Usage: /set_pair BTCUSDT")
			return
		pair = args[1].upper()
		cfg = grid_service.cfg
		new_cfg = ServiceConfig(
			pair=pair,
			timeframe=cfg.timeframe,
			fee_bps=cfg.fee_bps,
			slippage_bps=cfg.slippage_bps,
			base_order_usdt=cfg.base_order_usdt,
			lower_price=cfg.lower_price,
			upper_price=cfg.upper_price,
			grid_count=cfg.grid_count,
			step_type=cfg.step_type,
			use_rsi_filter=cfg.use_rsi_filter,
			use_ema_filter=cfg.use_ema_filter,
		)
		# stop engine, set market, then reconfigure service
		try:
			await engine.stop()
		except Exception:
			pass
		try:
			engine.set_market(pair)
		except Exception:
			pass
		await grid_service.reconfigure(new_cfg)
		await message.answer(f"Pair set to {pair}")

	@dp.message(Command("set_tf"))
	async def cmd_set_tf(message: Message):
		if grid_service is None:
			await message.answer("Service not available")
			return
		args = message.text.split()
		if len(args) != 2:
			await message.answer("Usage: /set_tf 1m|5m|15m|1h|4h")
			return
		tf = args[1]
		cfg = grid_service.cfg
		new_cfg = ServiceConfig(
			pair=cfg.pair,
			timeframe=tf,
			fee_bps=cfg.fee_bps,
			slippage_bps=cfg.slippage_bps,
			base_order_usdt=cfg.base_order_usdt,
			lower_price=cfg.lower_price,
			upper_price=cfg.upper_price,
			grid_count=cfg.grid_count,
			step_type=cfg.step_type,
			use_rsi_filter=cfg.use_rsi_filter,
			use_ema_filter=cfg.use_ema_filter,
		)
		await grid_service.reconfigure(new_cfg)
		await message.answer(f"TF set to {tf}")

	@dp.message(Command("set_grid"))
	async def cmd_set_grid(message: Message):
		if grid_service is None:
			await message.answer("Service not available")
			return
		args = message.text.split()
		if len(args) != 7:
			await message.answer("Usage: /set_grid lower upper grids base_order_usdt fee_bps slippage_bps")
			return
		_, lower, upper, grids, base_usdt, fee_bps, slip_bps = args
		cfg = grid_service.cfg
		new_cfg = ServiceConfig(
			pair=cfg.pair,
			timeframe=cfg.timeframe,
			fee_bps=float(fee_bps),
			slippage_bps=float(slip_bps),
			base_order_usdt=float(base_usdt),
			lower_price=float(lower),
			upper_price=float(upper),
			grid_count=int(grids),
			step_type=cfg.step_type,
			use_rsi_filter=cfg.use_rsi_filter,
			use_ema_filter=cfg.use_ema_filter,
		)
		await grid_service.reconfigure(new_cfg)
		await message.answer("Grid updated")

	@dp.callback_query(F.data == "grid_off_btn")
	async def cb_grid_off_btn(query: CallbackQuery):
		await query.answer("در حال خاموش کردن…")
		try:
			await grid_service.stop()  # type: ignore[union-attr]
		except Exception:
			pass
		try:
			await engine.stop()
		except Exception:
			pass
		# Try editing the original message; if not allowed, send a new message
		try:
			await query.message.edit_text("گرید متوقف شد ✅")
		except Exception:
			await query.message.answer("گرید متوقف شد ✅")

	@dp.callback_query(F.data == "grid_levels_btn")
	async def cb_grid_levels_btn(query: CallbackQuery):
		try:
			await query.message.answer(engine.levels_text())
		except Exception as e:
			await query.message.answer(f"خطا: {e}")
		await query.answer()

	# Aliases for older inline buttons to ensure backward compatibility
	@dp.callback_query(F.data == "grid:off")
	async def cb_grid_off_alias(query: CallbackQuery):
		await query.answer("در حال خاموش کردن…")
		try:
			await grid_service.stop()  # type: ignore[union-attr]
		except Exception:
			pass
		try:
			await engine.stop()
		except Exception:
			pass
		try:
			await query.message.edit_text("گرید متوقف شد ✅")
		except Exception:
			await query.message.answer("گرید متوقف شد ✅")

	@dp.callback_query(F.data == "grid:levels")
	async def cb_grid_levels_alias(query: CallbackQuery):
		try:
			await query.message.answer(engine.levels_text())
		except Exception as e:
			await query.message.answer(f"خطا: {e}")
		await query.answer()

	@dp.callback_query(F.data == "grid:on")
	async def cb_grid_on_alias(query: CallbackQuery):
		await query.answer("در حال روشن کردن…")
		async def run():
			try:
				try:
					await grid_service.stop()  # type: ignore[union-attr]
				except Exception:
					pass
				info = await engine.start(query.message.chat.id, grids_n=6, step_p=0.005, tp_p=0.01, sl_p=0.01, amount=max(5.0, exec_gateway.usdt_balance * 0.001) if hasattr(exec_gateway, 'usdt_balance') else 50.0)
				await query.message.answer(
					f"گرید روشن شد ✅ (حالت ثابت)\nمرکز={info['center']:.4f} | خطوط={info['grids_total']} | گام={info['step_pct']*100:.2f}% | TP/SL={info['tp_pct']*100:.2f}%/{info['sl_pct']*100:.2f}%"
				)
			except Exception as e:
				await query.message.answer(f"❌ خطا در روشن‌کردن گرید: {e}")
		asyncio.create_task(run())

	@dp.callback_query(F.data == "mode:toggle")
	async def cb_mode_toggle(query: CallbackQuery):
		try:
			mode_now = getattr(exec_gateway, "get_mode", lambda: "paper")()
			new_mode = "paper" if mode_now == "real" else "real"
			setter = getattr(exec_gateway, "set_mode", None)
			if setter:
				setter(new_mode)
			label = "حالت: اصلی" if new_mode == "real" else "حالت: آزمایشی"
			await query.message.answer(f"حالت اجرا تغییر کرد: {label}")
			await query.answer()
		except Exception as e:
			await query.answer(f"خطا: {e}", show_alert=False)

	def _mask(v: str) -> str:
		v = v or ""
		if len(v) <= 4:
			return "*" * len(v)
		return v[:2] + "*" * max(0, len(v) - 4) + v[-2:]

	@dp.callback_query(F.data.startswith("api:"))
	async def cb_api(query: CallbackQuery):
		parts = query.data.split(":")
		action = parts[1] if len(parts) > 1 else "open"
		if action == "open":
			cfg = await repo.get_settings()
			ak = _mask(cfg.get("coinex_api_key", ""))
			sk = _mask(cfg.get("coinex_api_secret", ""))
			kb = InlineKeyboardMarkup(inline_keyboard=[
				[InlineKeyboardButton(text="ثبت API Key", callback_data="api:set_key"), InlineKeyboardButton(text="ثبت API Secret", callback_data="api:set_secret")],
				[InlineKeyboardButton(text="حذف کلیدها", callback_data="api:clear")],
			])
			await query.message.answer(f"کلیدهای فعلی:\nAPI Key: {ak or '-'}\nAPI Secret: {sk or '-'}\n\nبرای ثبت، روی دکمه‌ها بزنید.", reply_markup=kb)
			await query.answer()
			return
		if action in ("set_key", "set_secret"):
			uid = query.from_user.id if query.from_user else 0
			pending_actions[uid] = action
			# also map by chat id to handle private/group chats consistently
			pending_actions[query.message.chat.id] = action
			prompt = "API Key را بفرستید (متن)" if action == "set_key" else "API Secret را بفرستید (متن)"
			await query.message.answer(prompt)
			await query.answer()
			return
		if action == "clear":
			await repo.update_settings({"coinex_api_key": "", "coinex_api_secret": ""})
			await query.message.answer("کلیدها حذف شد.")
			await query.answer()
			return

	# Legacy: keep but point to .env
	@dp.message(Command("set_api_key"))
	async def cmd_set_api_key(message: Message):
		parts = message.text.split(maxsplit=1)
		await _reply(message, "این دستور غیرفعال است. لطفاً COINEX_ACCESS_ID را در .env تنظیم کنید یا از دکمه تنظیم API استفاده کنید.")
		return

	@dp.message(Command("set_api_secret"))
	async def cmd_set_api_secret(message: Message):
		await _reply(message, "این دستور غیرفعال است. لطفاً COINEX_SECRET_KEY را در .env تنظیم کنید یا از دکمه تنظیم API استفاده کنید.")
		return

	@dp.message(Command("set_api"))
	async def cmd_set_api(message: Message):
		await _reply(message, "این دستور غیرفعال است. لطفاً COINEX_ACCESS_ID و COINEX_SECRET_KEY را در .env تنظیم کنید یا از دکمه تنظیم API استفاده کنید.")
		return

	@dp.message(Command("status_api"))
	async def cmd_status_api(message: Message):
		env_access = os.getenv("COINEX_ACCESS_ID", "")
		env_secret = os.getenv("COINEX_SECRET_KEY", "")
		def _m(v: str) -> str:
			v = v or ""
			return (v[:2] + "*" * max(0, len(v) - 4) + v[-2:]) if v else "-"
		await _reply(message, f"وضعیت .env:\nCOINEX_ACCESS_ID: {_m(env_access)}\nCOINEX_SECRET_KEY: {_m(env_secret)}")

	# Helpers to read/write .env safely
	def _env_file_path() -> Path:
		p = os.getenv("ENV_FILE", ".env")
		return Path(p)

	def _env_read_all() -> dict[str, str]:
		path = _env_file_path()
		if not path.exists():
			return {}
		d: dict[str, str] = {}
		for line in path.read_text(encoding="utf-8").splitlines():
			if not line or line.strip().startswith("#") or "=" not in line:
				continue
			k, v = line.split("=", 1)
			d[k.strip()] = v.strip()
		return d

	def _env_write_var(key: str, value: str) -> None:
		path = _env_file_path()
		lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
		new_lines: list[str] = []
		found = False
		for line in lines:
			if line.strip().startswith("#") or "=" not in line:
				new_lines.append(line)
				continue
			k, _ = line.split("=", 1)
			if k.strip() == key:
				new_lines.append(f"{key}={value}")
				found = True
			else:
				new_lines.append(line)
		if not found:
			new_lines.append(f"{key}={value}")
		path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
		os.environ[key] = value

	@dp.message(F.text)
	async def maybe_api_input(message: Message):
		txt = (message.text or "").strip()
		# اگر دستور است، عبور بده تا به هندلر خودش برسد
		if txt.startswith("/"):
			return
		uid = message.from_user.id if message.from_user else None
		if uid is None:
			return
		action = pending_actions.get(uid) or pending_actions.get(message.chat.id)
		if action not in ("env_set_key", "env_set_secret"):
			return
		if not txt:
			await _reply(message, "ورودی خالی است")
			return
		if action == "env_set_key":
			_env_write_var("COINEX_ACCESS_ID", txt)
			await _reply(message, "COINEX_ACCESS_ID در .env ذخیره شد")
		else:
			_env_write_var("COINEX_SECRET_KEY", txt)
			await _reply(message, "COINEX_SECRET_KEY در .env ذخیره شد")
		pending_actions.pop(uid, None)
		pending_actions.pop(message.chat.id, None)

	@dp.callback_query(F.data == "env:open")
	async def cb_env(query: CallbackQuery):
		envs = _env_read_all()
		ak = envs.get("COINEX_ACCESS_ID", "")
		sk = envs.get("COINEX_SECRET_KEY", "")
		def _mask(v: str) -> str:
			return (v[:2] + "*" * max(0, len(v) - 4) + v[-2:]) if v else "-"
		text = (
			"تنظیم API صرافی CoinEx\n"
			f"وضعیت فعلی (.env):\nCOINEX_ACCESS_ID: {_mask(ak)}\nCOINEX_SECRET_KEY: {_mask(sk)}\n\n"
			"برای تغییر و تنظیم API تنها این راه را استفاده کنید:\nبه ربات @wingtbbot مراجعه کنید → بخش «ربات‌های من».\n"
		)
		await query.message.answer(text)
		await query.answer()

	@dp.message(Command("apia"))
	async def cmd_apia(message: Message):
		await _reply(message, "این دستور غیرفعال است. لطفاً COINEX_ACCESS_ID را در .env تنظیم کنید یا از دکمه تنظیم API استفاده کنید.")
		return

	@dp.message(Command("apis"))
	async def cmd_apis(message: Message):
		await _reply(message, "این دستور غیرفعال است. لطفاً COINEX_SECRET_KEY را در .env تنظیم کنید یا از دکمه تنظیم API استفاده کنید.")
		return

	@dp.message(Command("ping"))
	async def cmd_ping(message: Message):
		await message.answer("pong")

	@dp.message(Command("set_admin"))
	async def cmd_set_admin(message: Message):
		parts = message.text.split()
		if len(parts) != 2 or not parts[1].isdigit():
			await _reply(message, "نحوه استفاده: /set_admin 123456789 (chat id)")
			return
		aid = int(parts[1])
		await repo.update_settings({"admin_chat_id": str(aid)})
		await _reply(message, f"admin_chat_id تنظیم شد: {aid}")

	@dp.message(Command("who_admin"))
	async def cmd_who_admin(message: Message):
		cfg = await repo.get_settings()
		val = (cfg.get("admin_chat_id") or "").strip()
		await _reply(message, f"admin_chat_id = {val or '-'}")

