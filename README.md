# Telegram Grid-Trading Bot (Paper Trading V1)

Modular Telegram bot that performs grid trading on CoinEx market data. V1 runs in paper trading mode using live data. Architecture allows swapping execution module later for real trading.

## Features
- Grid strategy on USDT pairs (default `BTCUSDT`)
- Live data via CoinEx REST/WS (public)
- Paper execution with fills, fees, slippage
- SQLite persistence (trades, balances, settings)
- Telegram bot (aiogram v3): status, control commands, history, CSV export
- APScheduler for periodic tasks
- Extensible interfaces: IDataFeed, IExecutionGateway, IStrategy

## Quickstart
1. Create `.env` from `.env.example` and fill `BOT_TOKEN`.
2. Install deps:
```bash
pip install -e .
```
3. Run:
```bash
python -m scripts.run_bot
```

Optional (Docker):
```bash
docker compose up -d
```

## Config
Environment vars (.env):
```
BOT_TOKEN=
COINEX_ACCESS_ID=
COINEX_SECRET_KEY=
DEFAULT_PAIR=BTCUSDT
DEFAULT_TF=5m
START_BALANCE_USDT=200000
FEE_BPS=10
SLIPPAGE_BPS=2
```

## Project Structure
```
app/
  config/
  bot/
  core/
    indicators/
    strategy/
    paper_engine/
    risk/
    reporting/
    storage/
  datafeed/
  execution/
  services/
  infra/
scripts/
```

## Upgrade Path
- Implement `CoinExExecutionGateway` with v2 auth headers and signatures
- Add risk controls and kill switch
- Keep interfaces stable to swap modules via config

## License
MIT