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

	async def _notify(chat_id: int, text: str):
		try:
			bot = dp.bot  # type: ignore[attr-defined]
			await bot.send_message(chat_id, text)
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
			[InlineKeyboardButton(text="مشاهده استراتژی", callback_data="show_strategy")],
			[InlineKeyboardButton(text="تغییر استراتژی/منطق", callback_data="edit_strategy")],
			[InlineKeyboardButton(text="معامله تستی", callback_data="test_trade"), InlineKeyboardButton(text="فروش تستی", callback_data="test_sell")],
			[InlineKeyboardButton(text="وضعیت", callback_data="show_status"), InlineKeyboardButton(text="تاریخچه", callback_data="show_history")],
			[InlineKeyboardButton(text="پاک کردن تاریخچه", callback_data="clear_history")],
			[InlineKeyboardButton(text="روشن کردن گرید", callback_data="grid_on_btn")],
			[InlineKeyboardButton(text="خاموش کردن گرید", callback_data="grid_off_btn"), InlineKeyboardButton(text="نمایش خطوط گرید", callback_data="grid_levels_btn")],
			[InlineKeyboardButton(text="تنظیم API صرافی CoinEx", callback_data="api:open")],
		])
		await message.answer("Grid bot online.", reply_markup=kb)

	@dp.message(Command("status"))
	async def cmd_status(message: Message):
		b = exec_gateway.balances()
		text = (
			f"USDT={b['USDT']:.2f}, ASSET_QTY={b['ASSET_QTY']:.6f}, PRICE={b['ASSET_PRICE']:.2f}, AVG_COST={b.get('AVG_COST',0):.2f}\n"
			f"EQUITY={b['EQUITY']:.2f}, PNL_REAL={b['PNL_REALIZED']:.2f}, PNL_UNREAL={b.get('PNL_UNREALIZED',0):.2f}, WIN_RATE={b['WIN_RATE']:.2f}%\n"
			f"MAX_DD={b['MAX_DRAWDOWN']:.2f}"
		)
		await message.answer(text)

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
		args = message.text.split()
		scope = args[1].lower() if len(args) > 1 else "day"
		period_map = {"hour": ("1min", 120), "day": ("5min", 288), "15d": ("15min", 1440), "month": ("1hour", 720), "2m": ("4hour", 360), "3m": ("1day", 90)}
		if scope not in period_map:
			scope = "day"
		tf, limit = period_map[scope]
		feed = CoinExDataFeed()
		try:
			candles = await feed.get_klines(grid_service.cfg.pair if grid_service else "TRXUSDT", tf, limit)
			closes: list[float] = []
			for c in candles:
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
			# parse optional params: /backtest <scope> [lower upper grids tp_pct amount]
			def _get(idx: int, cast):
				try:
					return cast(args[idx])
				except Exception:
					return None
			lb = _get(2, float) or (min(closes) * 0.99)
			ub = _get(3, float) or (max(closes) * 1.01)
			grids = int(_get(4, int) or 20)
			tp_pct = (_get(5, float) or 1.0) / 100.0
			amount = float(_get(6, float) or (grid_service.cfg.base_order_usdt if grid_service else 50.0))
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
					kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="نمایش داشبورد", callback_data="show_dashboard")]])
					await message.answer(
						f"گرید روشن شد ✅ (حالت ثابت)\nمرکز={info['center']:.4f} | خطوط={info['grids_total']} | گام={info['step_pct']*100:.2f}% | TP/SL={info['tp_pct']*100:.2f}%/{info['sl_pct']*100:.2f}%",
						reply_markup=kb,
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
				kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="نمایش داشبورد", callback_data="show_dashboard")]])
				await query.message.answer(
					f"گرید روشن شد ✅ (حالت ثابت)\nمرکز={info['center']:.4f} | خطوط={info['grids_total']} | گام={info['step_pct']*100:.2f}% | TP/SL={info['tp_pct']*100:.2f}%/{info['sl_pct']*100:.2f}%",
					reply_markup=kb,
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
				kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="نمایش داشبورد", callback_data="show_dashboard")]])
				await query.message.answer(
					f"گرید روشن شد ✅ (حالت ثابت)\nمرکز={info['center']:.4f} | خطوط={info['grids_total']} | گام={info['step_pct']*100:.2f}% | TP/SL={info['tp_pct']*100:.2f}%/{info['sl_pct']*100:.2f}%",
					reply_markup=kb,
				)
			except Exception as e:
				await query.message.answer(f"❌ خطا در روشن‌کردن گرید: {e}")
		asyncio.create_task(run())

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
			prompt = "API Key را بفرستید (متن)" if action == "set_key" else "API Secret را بفرستید (متن)"
			await query.message.answer(prompt)
			await query.answer()
			return
		if action == "clear":
			await repo.update_settings({"coinex_api_key": "", "coinex_api_secret": ""})
			await query.message.answer("کلیدها حذف شد.")
			await query.answer()
			return

	@dp.message(F.text)
	async def maybe_api_input(message: Message):
		uid = message.from_user.id if message.from_user else None
		if uid is None:
			return
		action = pending_actions.get(uid)
		if action not in ("set_key", "set_secret"):
			return
		val = message.text.strip()
		if not val:
			await message.answer("ورودی خالی است")
			return
		if action == "set_key":
			await repo.update_settings({"coinex_api_key": val})
			await message.answer("API Key ذخیره شد")
		else:
			await repo.update_settings({"coinex_api_secret": val})
			await message.answer("API Secret ذخیره شد")
		pending_actions.pop(uid, None)

