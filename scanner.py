import os
import json
import time
from datetime import datetime, timezone

import requests
import pandas as pd

BASE_URL = "https://data-api.binance.vision"
TELEGRAM_URL = "https://api.telegram.org/bot{}/sendMessage"

TIMEFRAMES = {
    "1H": "1h",
    "1D": "1d",
    "1W": "1w",
}

RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
PIVOT_LEFT = int(os.getenv("PIVOT_LEFT", "5"))
LOOKBACK = int(os.getenv("LOOKBACK", "220"))
MIN_QUOTE_VOLUME = float(os.getenv("MIN_QUOTE_VOLUME", "1000000"))
MAX_SYMBOLS = int(os.getenv("MAX_SYMBOLS", "250"))

STATE_FILE = "state.json"


def get_json(path, params=None):
    r = requests.get(BASE_URL + path, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def get_usdt_symbols():
    info = get_json("/api/v3/exchangeInfo")
    tickers = get_json("/api/v3/ticker/24hr")

    volume = {
        x["symbol"]: float(x.get("quoteVolume", 0))
        for x in tickers
        if x.get("symbol")
    }

    symbols = []
    for s in info["symbols"]:
        if (
            s.get("status") == "TRADING"
            and s.get("quoteAsset") == "USDT"
            and s.get("isSpotTradingAllowed", False)
            and s.get("baseAsset") not in {"USDC", "FDUSD", "TUSD", "USDP", "DAI"}
        ):
            sym = s["symbol"]
            if volume.get(sym, 0) >= MIN_QUOTE_VOLUME:
                symbols.append((sym, volume.get(sym, 0)))

    symbols.sort(key=lambda x: x[1], reverse=True)
    return [x[0] for x in symbols[:MAX_SYMBOLS]]


def get_klines(symbol, interval):
    data = get_json(
        "/api/v3/klines",
        {"symbol": symbol, "interval": interval, "limit": LOOKBACK},
    )

    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_base",
        "taker_quote", "ignore"
    ]
    df = pd.DataFrame(data, columns=cols)

    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)

    # Only closed candles. This keeps the alert stable while still using
    # pivot_right=0 for the divergence structure.
    now = pd.Timestamp.now(tz="UTC")
    df = df[df["close_time"] <= now].copy()
    return df.reset_index(drop=True)


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)
    out = 100 - (100 / (1 + rs))
    return out.astype(float)


def pivot_low(series, left):
    vals = series.to_numpy()
    out = [False] * len(vals)
    for i in range(left, len(vals)):
        # Right = 0: current bar only needs to be lower/equal than its left bars.
        out[i] = vals[i] <= vals[i-left:i].min()
    return out


def pivot_high(series, left):
    vals = series.to_numpy()
    out = [False] * len(vals)
    for i in range(left, len(vals)):
        out[i] = vals[i] >= vals[i-left:i].max()
    return out


def find_latest_divergence(df):
    if len(df) < max(60, PIVOT_LEFT * 3):
        return None

    df = df.copy()
    df["rsi"] = rsi(df["close"], RSI_PERIOD)

    lows = pivot_low(df["low"], PIVOT_LEFT)
    highs = pivot_high(df["high"], PIVOT_LEFT)

    low_idx = [i for i, x in enumerate(lows) if x and pd.notna(df.loc[i, "rsi"])]
    high_idx = [i for i, x in enumerate(highs) if x and pd.notna(df.loc[i, "rsi"])]

    # Need two pivot points. Compare the newest qualifying pivot with the
    # immediately previous pivot of the same type.
    if len(low_idx) >= 2:
        a, b = low_idx[-2], low_idx[-1]
        if df.loc[b, "low"] < df.loc[a, "low"] and df.loc[b, "rsi"] > df.loc[a, "rsi"]:
            return {
                "type": "BULLISH",
                "index": b,
                "signal_time": df.loc[b, "close_time"].isoformat(),
                "price": float(df.loc[b, "close"]),
                "rsi": float(df.loc[b, "rsi"]),
                "pivot_price_1": float(df.loc[a, "low"]),
                "pivot_price_2": float(df.loc[b, "low"]),
                "pivot_rsi_1": float(df.loc[a, "rsi"]),
                "pivot_rsi_2": float(df.loc[b, "rsi"]),
            }

    if len(high_idx) >= 2:
        a, b = high_idx[-2], high_idx[-1]
        if df.loc[b, "high"] > df.loc[a, "high"] and df.loc[b, "rsi"] < df.loc[a, "rsi"]:
            return {
                "type": "BEARISH",
                "index": b,
                "signal_time": df.loc[b, "close_time"].isoformat(),
                "price": float(df.loc[b, "close"]),
                "rsi": float(df.loc[b, "rsi"]),
                "pivot_price_1": float(df.loc[a, "high"]),
                "pivot_price_2": float(df.loc[b, "high"]),
                "pivot_rsi_1": float(df.loc[a, "rsi"]),
                "pivot_rsi_2": float(df.loc[b, "rsi"]),
            }

    return None


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def send_telegram(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    r = requests.post(
        TELEGRAM_URL.format(token),
        data={"chat_id": chat_id, "text": text},
        timeout=20,
    )
    r.raise_for_status()


def fmt_price(x):
    if x >= 1000:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:.4f}"
    return f"{x:.8f}"


def make_message(symbol, tf, div):
    emoji = "🟢" if div["type"] == "BULLISH" else "🔴"
    return (
        f"{emoji} {div['type']} RSI DIVERGENCE\n\n"
        f"Coin: {symbol}\n"
        f"Timeframe: {tf}\n"
        f"Price: {fmt_price(div['price'])}\n"
        f"RSI({RSI_PERIOD}): {div['rsi']:.2f}\n"
        f"Pivot Right: 0\n"
        f"Pivot Left: {PIVOT_LEFT}\n"
        f"Signal Candle: {div['signal_time']}\n\n"
        f"Binance Spot | Educational scanner alert"
    )


def main():
    symbols = get_usdt_symbols()
    state = load_state()

    print(f"Scanning {len(symbols)} Binance USDT spot pairs...")

    sent = 0
    errors = 0

    for symbol in symbols:
        for tf_name, interval in TIMEFRAMES.items():
            try:
                df = get_klines(symbol, interval)
                div = find_latest_divergence(df)

                if not div:
                    continue

                # Deduplicate by symbol + timeframe + direction + signal candle.
                key = f"{symbol}|{tf_name}|{div['type']}"
                signal_id = div["signal_time"]

                if state.get(key) == signal_id:
                    continue

                send_telegram(make_message(symbol, tf_name, div))
                state[key] = signal_id
                save_state(state)
                sent += 1

                # Keep requests polite.
                time.sleep(0.05)

            except Exception as e:
                errors += 1
                print(f"ERROR {symbol} {tf_name}: {e}")

    save_state(state)
    print(f"Done. Alerts sent: {sent}; errors: {errors}")


if __name__ == "__main__":
    main()
