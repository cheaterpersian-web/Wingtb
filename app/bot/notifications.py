from __future__ import annotations

from aiogram import Bot

from app.execution.paper_exec import OrderResult


class Notifier:
    def __init__(self, bot: Bot, chat_id: int | str):
        self.bot = bot
        self.chat_id = str(chat_id)

    async def on_trade(self, res: OrderResult, pair: str, equity: float, win_rate: float):
        emoji = "✅" if res.pnl_realized >= 0 else "❌"
        text = (
            f"{emoji} {res.side} {pair} | qty={res.qty:.6f} | price={res.price:.2f} | "
            f"fee={res.fee:.4f} | pnl={res.pnl_realized:.2f} | equity={equity:.2f} | win_rate={win_rate:.2f}%"
        )
        await self.bot.send_message(self.chat_id, text)

