## CoinEx Telegram Trader Bot (Demo)

Phase 1: Telegram bot to fetch market info from CoinEx v2 and show basic data.
Phase 2: Run a simple demo backtest (paper trading) with real data from CoinEx inside the bot.

### Requirements
- Python 3.10+
- Telegram bot token in env var `TELEGRAM_BOT_TOKEN`

### Install
```bash
pip install -r requirements.txt
```

### Run
```bash
export TELEGRAM_BOT_TOKEN=your_token_here
python -m bot.main
```

### Commands
- `/start` or `/help`: Show help
- `/search <query>`: Search markets by substring (e.g. `BTC`)
- `/ticker <market>`: Show compact ticker (e.g. `BTCUSDT`)
- `/klines <market> [period] [limit]`: Fetch klines (default `1hour` `100`)
- `/demo <market> [period] [limit]`: Run demo strategy and show win rate and PnL

### Notes
- Public endpoints used:
  - `GET /v2/spot/ticker` for all spot tickers
  - `GET /v2/spot/kline?market=...&period=...&limit=...` for klines
- Valid periods observed include: `1min`, `5min`, `15min`, `30min`, `1hour`, `4hour`, `1day`.

This is a demo; not financial advice.

## CoinEx Telegram Trader Bot (Demo)

Phase 1: Telegram bot to fetch market info from CoinEx v2 and show basic data.
Phase 2: Run a simple demo backtest (paper trading) with real data from CoinEx inside the bot.

### Requirements
- Python 3.10+
- Telegram bot token in env var `TELEGRAM_BOT_TOKEN`

### Install
```bash
pip install -r requirements.txt
```

### Run
```bash
export TELEGRAM_BOT_TOKEN=your_token_here
python -m bot.main
```

### Commands
- `/start` or `/help`: Show help
- `/search <query>`: Search markets by substring (e.g. `BTC`)
- `/ticker <market>`: Show compact ticker (e.g. `BTCUSDT`)
- `/klines <market> [period] [limit]`: Fetch klines (default `1hour` `100`)
- `/demo <market> [period] [limit]`: Run demo strategy and show win rate and PnL

### Notes
- Public endpoints used:
  - `GET /v2/spot/ticker` for all spot tickers
  - `GET /v2/spot/kline?market=...&period=...&limit=...` for klines
- Valid periods observed include: `1min`, `5min`, `15min`, `30min`, `1hour`, `4hour`, `1day`.

This is a demo; not financial advice.

