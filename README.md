# Binance RSI Divergence Cloud Scanner

Educational scanner for Binance USDT spot markets.

## Features

- Binance Spot USDT pairs
- Bullish + Bearish RSI divergence
- 1H + 1D + 1W
- RSI 14
- Pivot Right = 0
- Pivot Left = 5 by default
- Duplicate-alert protection
- Telegram alerts
- GitHub Actions cloud scheduling
- No Binance trading/API key required because this scanner only reads public market data

## Important divergence definition

Bullish:
- price makes a lower low
- RSI makes a higher low

Bearish:
- price makes a higher high
- RSI makes a lower high

The scanner uses only closed candles. Pivot Right remains 0, so the latest pivot candidate does not require future candles for confirmation.

## Cloud setup

1. Create a GitHub repository.
2. Upload these files.
3. In GitHub:
   Settings -> Secrets and variables -> Actions
4. Add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. Open Actions and manually run `Binance RSI Divergence Scanner`.
6. After the test succeeds, the scheduled scanner runs automatically.

GitHub scheduled workflows use UTC and can occasionally be delayed during high-load periods. The workflow therefore runs every 5 minutes rather than exactly on each candle close.

## Safety

This is an alert/scanning tool only. It does not place trades and contains no Binance trading credentials.
