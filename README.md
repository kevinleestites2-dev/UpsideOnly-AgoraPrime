# AgoraPrime — UpsideOnly Bot

Automated trading bot for UpsideOnly.com.
Target: Beat JimmieShortsWorld (+108.5%) and dominate the leaderboard.

## Architecture

signal_engine.py  — RSI + Bollinger + VWAP signals across 25 markets
market_feed.py    — Real-time OHLCV data (yfinance + Finnhub + CoinGecko)
executor.py       — Playwright browser automation, places/manages trades

## Edge vs JimmieShortsWorld

- He shorts RSI overbought — we do the same PLUS long oversold
- He has no TP/SL precision — we set strict 1.5:1 R:R on every trade
- He covers maybe 1-2 assets — we scan all 25 simultaneously
- We report every trade to Telegram for full visibility

## Setup (Termux)

```
pip install -r requirements.txt
playwright install chromium
python executor.py
```

First run requires Google SSO login — will open browser window.
After first auth, session is saved.

## Config (executor.py top section)

POSITION_SIZE   = 0.01   # 1% of balance per trade
MAX_OPEN_TRADES = 5      # max concurrent positions
SCAN_INTERVAL   = 60     # seconds between scans

## Flow

1. Login via Google SSO (kevinleestites2@gmail.com)
2. Every 60 seconds: scan all 25 markets
3. Top signals by confidence get executed as limit orders with TP+SL
4. Telegram reports every trade + status every 10 cycles
5. Ride to #1 on the leaderboard

## Phase 2 (after first week live)

- Map exact UpsideOnly DOM selectors after login
- Increase scan frequency to 30s
- Add confirmation: 2 strategies must agree before firing
- Auto-reset balance if drawdown >20% (reset = fresh start, still +EV)
