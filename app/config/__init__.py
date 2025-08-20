from pydantic import BaseModel
import os


class Settings(BaseModel):
    bot_token: str
    default_pair: str = os.getenv("DEFAULT_PAIR", "BTCUSDT")
    default_tf: str = os.getenv("DEFAULT_TF", "1h")
    start_balance_usdt: float = float(os.getenv("START_BALANCE_USDT", "200000"))
    fee_bps: float = float(os.getenv("FEE_BPS", "10"))
    slippage_bps: float = float(os.getenv("SLIPPAGE_BPS", "2"))

    @staticmethod
    def load() -> "Settings":
        token = os.getenv("BOT_TOKEN", "")
        if not token:
            raise RuntimeError("BOT_TOKEN is required in environment.")
        return Settings(bot_token=token)

