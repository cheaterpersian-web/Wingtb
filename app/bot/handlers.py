from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from dataclasses import dataclass
from typing import Dict

from app.config import Settings
from app.datafeed.coinex_rest import CoinexREST
from app.core.indicators.ema import ema
from app.core.indicators.rsi import rsi
from app.core.strategy.grid import GridConfig, GridStrategy
from app.core.paper_engine import PaperEngine


router = Router()


@dataclass
class Session:
    pair: str
    tf: str
    engine: PaperEngine
    strat: GridStrategy


sessions: Dict[int, Session] = {}


def _session(chat_id: int, settings: Settings) -> Session:
    if chat_id not in sessions:
        engine = PaperEngine(
            start_usdt=settings.start_balance_usdt,
            fee_bps=settings.fee_bps,
            slippage_bps=settings.slippage_bps,
            pair=settings.default_pair,
        )
        cfg = GridConfig(
            lower_price=20000.0,
            upper_price=100000.0,
            grid_count=12,
            step_type="percent",
            base_order_usdt=20.0,
            max_position_usdt=10000.0,
            fee_bps=settings.fee_bps,
            slippage_bps=settings.slippage_bps,
        )
        strat = GridStrategy(cfg)
        sessions[chat_id] = Session(settings.default_pair, settings.default_tf, engine, strat)
    return sessions[chat_id]


@router.message(Command("start"))
async def cmd_start(m: Message, settings: Settings) -> None:
    s = _session(m.chat.id, settings)
    await m.answer("ربات آماده است. /status | /grid_on | /grid_off | /set_pair | /set_tf | /set_grid")


@router.message(Command("status"))
async def cmd_status(m: Message, settings: Settings) -> None:
    s = _session(m.chat.id, settings)
    rest = CoinexREST()
    t = rest.ticker(s.pair)
    last = float(t.get("last", 0.0)) if t else 0.0
    unreal = (last - s.engine.avg_cost) * s.engine.asset_qty if s.engine.asset_qty > 0 else 0.0
    realized = sum(tr.pnl_realized for tr in s.engine.trades)
    await m.answer(
        f"Pair: {s.pair}\nBal USDT: {s.engine.usdt:.2f}\nAsset Qty: {s.engine.asset_qty:.6f} @ {s.engine.avg_cost:.2f}\nLast: {last}\nPnL R: {realized:.2f} | U: {unreal:.2f}\nTrades: {len(s.engine.trades)}"
    )


@router.message(Command("grid_on"))
async def cmd_grid_on(m: Message, settings: Settings) -> None:
    s = _session(m.chat.id, settings)
    await m.answer("Grid will execute on each /tick (WS not wired in v1 demo). Use /tick for manual step.")


@router.message(Command("tick"))
async def cmd_tick(m: Message, settings: Settings) -> None:
    s = _session(m.chat.id, settings)
    rest = CoinexREST()
    kl = rest.klines(s.pair, s.tf, 200)
    closes = [float(k["close"]) for k in kl] if kl and isinstance(kl[0], dict) else [float(k.get("close", 0.0)) for k in kl]
    if not closes:
        await m.answer("No klines.")
        return
    rsi_vals = rsi(closes, 14)
    ema_fast = ema(closes, 12)
    ema_slow = ema(closes, 26)
    price = closes[-1]
    intent = s.strat.on_tick(price, rsi_vals[-1], ema_fast[-1] > ema_slow[-1], ema_fast[-1] < ema_slow[-1])
    text = f"Tick {price}"
    if intent:
        tr = s.engine.on_intent(intent.side, intent.qty, price)
        if tr:
            sign = "✅" if tr.pnl_realized >= 0 else "❌"
            text += f"\n{sign} {tr.side.upper()} {s.pair} qty={tr.qty:.6f} price={tr.price:.2f} fee={tr.fee:.4f} pnl={tr.pnl_realized:.2f} bal={s.engine.usdt:.2f}"
    await m.answer(text)

