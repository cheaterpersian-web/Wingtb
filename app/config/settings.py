from __future__ import annotations

import os
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class GridConfig(BaseModel):
    pair: str = "BTCUSDT"
    timeframe: str = "5m"
    lower_price: float = 50000.0
    upper_price: float = 70000.0
    grid_count: int = 20
    step_type: str = "percent"  # percent | fixed
    base_order_usdt: float = 100.0
    max_position_usdt: float = 100000.0
    fee_bps: float = 10.0
    slippage_bps: float = 2.0
    use_rsi_filter: bool = False
    use_ema_filter: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str | None = None
    coinex_access_id: str | None = None
    coinex_secret_key: str | None = None

    default_pair: str = os.getenv("DEFAULT_PAIR", "BTCUSDT")
    default_tf: str = os.getenv("DEFAULT_TF", "5m")
    start_balance_usdt: float = float(os.getenv("START_BALANCE_USDT", "200000"))
    fee_bps: float = float(os.getenv("FEE_BPS", "10"))
    slippage_bps: float = float(os.getenv("SLIPPAGE_BPS", "2"))

    @property
    def initial_grid(self) -> GridConfig:
        return GridConfig(
            pair=self.default_pair,
            timeframe=self.default_tf,
            fee_bps=self.fee_bps,
            slippage_bps=self.slippage_bps,
        )


def load_settings() -> Settings:
    return Settings()

