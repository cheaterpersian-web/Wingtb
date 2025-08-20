from __future__ import annotations

import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from app.core.storage.db import SQLiteRepo
from app.execution.paper_exec import PaperExecutionGateway
from app.services.grid_service import GridService
from app.services.grid_service import ServiceConfig


logger = logging.getLogger(__name__)


def setup_handlers(dp: Dispatcher, repo: SQLiteRepo, exec_gateway: PaperExecutionGateway, *, grid_service: GridService | None = None):
    pending_actions: dict[int, str] = {}

    @dp.message(Command("start"))
    async def cmd_start(message: Message):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="مشاهده استراتژی", callback_data="show_strategy")],
            [InlineKeyboardButton(text="تغییر استراتژی/منطق", callback_data="edit_strategy")],
            [InlineKeyboardButton(text="معامله تستی", callback_data="test_trade"), InlineKeyboardButton(text="فروش تستی", callback_data="test_sell")],
            [InlineKeyboardButton(text="وضعیت", callback_data="show_status"), InlineKeyboardButton(text="تاریخچه", callback_data="show_history")],
            [InlineKeyboardButton(text="پاک کردن تاریخچه", callback_data="clear_history")],
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

    @dp.message(Command("grid_on"))
    async def cmd_grid_on(message: Message):
        if grid_service is None:
            await message.answer("Service not available")
            return
        await grid_service.start()
        # Immediately show status and strategy to confirm it's running
        b = exec_gateway.balances()
        await message.answer("Grid started\n" + _format_strategy())
        await message.answer(
            f"USDT={b['USDT']:.2f}, ASSET_QTY={b['ASSET_QTY']:.6f}, PRICE={b['ASSET_PRICE']:.2f}\n"
            f"EQUITY={b['EQUITY']:.2f}"
        )

    @dp.message(Command("grid_off"))
    async def cmd_grid_off(message: Message):
        if grid_service is None:
            await message.answer("Service not available")
            return
        await grid_service.stop()
        await message.answer("Grid stopped")

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

