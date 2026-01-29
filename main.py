# main.py — PREMIER MULTI-ASSET AI LEVELS BOT (FINAL)

import os
import ccxt
import pandas as pd
import numpy as np
import asyncio
import threading
import time
import traceback
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Bot
from flask import Flask, jsonify, render_template_string
from dotenv import load_dotenv

# ===================== CONFIG =====================
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PAIRS = [
    "EUR/USD",
    "GBP/JPY",
    "AUD/USD",
    "GBP/USD",
    "XAU/USD",
    "AUD/CAD",
    "AUD/JPY",
    "BTC/USD"
]

TF_TREND = "4h"
TF_ENTRY = "1h"

bot = Bot(token=TELEGRAM_BOT_TOKEN)

exchange = ccxt.kraken({
    "enableRateLimit": True,
    "rateLimit": 2000
})

# ===================== HELPERS =====================

def asset_type(symbol):
    if "BTC" in symbol:
        return "CRYPTO"
    if "XAU" in symbol:
        return "METAL"
    return "FOREX"

def pip_value(symbol):
    if "JPY" in symbol: return 0.01
    if "XAU" in symbol or "BTC" in symbol: return 1
    return 0.0001

def calculate_cpr(df):
    prev = df.iloc[-2]
    H, L, C = prev["high"], prev["low"], prev["close"]
    PP = (H + L + C) / 3
    BC = (H + L) / 2
    TC = 2 * PP - BC
    return {
        "PP": PP,
        "BC": BC,
        "TC": TC,
        "R1": 2*PP - L,
        "S1": 2*PP - H,
        "R2": PP + (H - L),
        "S2": PP - (H - L)
    }

def indicators(df):
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs()
    ], axis=1).max(axis=1)

    df["atr"] = tr.rolling(14).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    rs = gain.rolling(14).mean() / loss.rolling(14).mean()
    df["rsi"] = 100 - (100 / (1 + rs))

    ema12 = df["close"].ewm(span=12).mean()
    ema26 = df["close"].ewm(span=26).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    return df.dropna()

def fetch(symbol, tf, limit=200):
    market = exchange.market(symbol)["id"]
    data = exchange.fetch_ohlcv(market, tf, limit=limit)
    df = pd.DataFrame(data, columns=["ts","open","high","low","close","vol"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df.set_index("ts", inplace=True)
    return indicators(df.astype(float))

# ===================== CORE LOGIC =====================

def analyze(symbol):
    try:
        df_4h = fetch(symbol, TF_TREND)
        df_1h = fetch(symbol, TF_ENTRY)
        df_d = fetch(symbol, "1d", 10)

        if df_4h.empty or df_1h.empty:
            return

        cpr = calculate_cpr(df_d)
        last4 = df_4h.iloc[-1]
        last1 = df_1h.iloc[-1]

        price = last4["close"]
        pip = pip_value(symbol)

        trend4 = "BULLISH" if last4["ema20"] > last4["ema50"] else "BEARISH"
        trend1 = "BULLISH" if last1["ema20"] > last1["ema50"] else "BEARISH"

        atr = last4["atr"]
        vol_pips = atr / pip if pip else 0

        score = 0
        reasons = []

        if trend4 == trend1:
            score += 2 if trend4 == "BULLISH" else -2
            reasons.append("Trend aligned")

        if price > cpr["PP"]:
            score += 1
            reasons.append("Above PP")
        else:
            score -= 1
            reasons.append("Below PP")

        if last1["macd_hist"] > 0:
            score += 1
            reasons.append("MACD positive")

        if 35 < last1["rsi"] < 70:
            score += 0.5
            reasons.append("RSI healthy")

        if score >= 3:
            call = "STRONG BUY"
            emoji = "🚀"
        elif score >= 1.5:
            call = "BUY"
            emoji = "🟢"
        elif score <= -3:
            call = "STRONG SELL"
            emoji = "🔻"
        elif score <= -1.5:
            call = "SELL"
            emoji = "🔴"
        else:
            call = "NEUTRAL"
            emoji = "⚪️"

        atype = asset_type(symbol)
        atr_mult = 2.5 if atype=="CRYPTO" else 2.0 if atype=="METAL" else 1.5

        if "BUY" in call:
            sl = price - atr_mult * atr
            tp1, tp2 = cpr["R1"], cpr["R2"]
        elif "SELL" in call:
            sl = price + atr_mult * atr
            tp1, tp2 = cpr["S1"], cpr["S2"]
        else:
            sl = cpr["BC"]
            tp1, tp2 = cpr["R1"], cpr["S1"]

        d = 2 if atype!="FOREX" else (3 if "JPY" in symbol else 5)

        msg = f"""
<b>{emoji} {symbol}</b>

<b>Price:</b> <code>{price:.{d}f}</code>

<b>Final Call:</b> <b>{call}</b>

<b>Trend:</b>
• 4H: {trend4}
• 1H: {trend1}

<b>Volatility:</b>
• ATR: {atr:.{d}f}
• ~{vol_pips:.1f} pips

<b>Levels:</b>
PP: <code>{cpr["PP"]:.{d}f}</code>
R1: <code>{cpr["R1"]:.{d}f}</code> | R2: <code>{cpr["R2"]:.{d}f}</code>
S1: <code>{cpr["S1"]:.{d}f}</code> | S2: <code>{cpr["S2"]:.{d}f}</code>

<b>Risk:</b>
SL: <code>{sl:.{d}f}</code>
TP1: <code>{tp1:.{d}f}</code>
TP2: <code>{tp2:.{d}f}</code>

<b>Reason:</b> {" • ".join(reasons)}
"""

        asyncio.run(bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=msg,
            parse_mode="HTML"
        ))

    except Exception as e:
        print(symbol, e)
        traceback.print_exc()

# ===================== SCHEDULER =====================

def start():
    scheduler = BackgroundScheduler()
    for s in PAIRS:
        scheduler.add_job(analyze, "cron", minute="0,30", args=[s])
        threading.Thread(target=analyze, args=(s,)).start()
    scheduler.start()

start()

# ===================== FLASK =====================

app = Flask(__name__)

@app.route("/")
def home():
    return "AI Levels Bot Running"

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
