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
WS_PING_SECONDS=25
WS_READ_TIMEOUT_SECONDS=30
WS_BACKOFF_MAX_SECONDS=30
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

## WebSocket Stability (CoinEx v2 Spot)
- App-level heartbeat: sends `{"method":"server.ping","params":{},"id":999}` every `WS_PING_SECONDS` with jitter.
- Read timeout: if no frames for `WS_READ_TIMEOUT_SECONDS`, sends a ping; if no message for > 60s total, reconnects.
- Compression: permessage-deflate enabled; binary frames are decompressed via zlib.
- Reconnect: exponential backoff (1s→2s→4s... capped by `WS_BACKOFF_MAX_SECONDS`) with small jitter, and deterministic resubscribe.

## License
MIT