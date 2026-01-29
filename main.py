# main.py - PREMIER FOREX AI QUANT V5.0 (Bulletproof Edition)

import os
import threading
import time
import requests
import pandas as pd
import yfinance as yf
from flask import Flask
from datetime import datetime

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# PAIRS CONFIGURATION
# We use Yahoo Finance tickers which are reliable and free
PAIRS = {
    "GBP/JPY": "GBPJPY=X",
    "XAU/USD": "GC=F",      # Gold Futures
    "AUD/CAD": "AUDCAD=X"
}

app = Flask(__name__)

# --- TELEGRAM SENDER (Simple & Reliable) ---
def send_msg(text):
    """Sends a Telegram message without crashing."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"⚠️ Telegram Failed: {e}")

# --- TRADING LOGIC ---
def get_signal(symbol, ticker):
    try:
        # 1. Get Data (Fast Mode)
        df = yf.download(ticker, period="5d", interval="1h", progress=False)
        if df.empty: return None

        # Fix Data Structure
        if isinstance(df.columns, pd.MultiIndex): 
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns={"Close": "close", "High": "high", "Low": "low"})
        
        price = float(df['close'].iloc[-1])

        # 2. Calculate Indicators
        # Simple Trend: Short MA vs Long MA
        sma_short = df['close'].rolling(8).mean().iloc[-1]
        sma_long = df['close'].rolling(21).mean().iloc[-1]
        trend = "BULLISH" if sma_short > sma_long else "BEARISH"
        
        # Volatility (ATR)
        atr = (df['high'] - df['low']).rolling(14).mean().iloc[-1]
        if pd.isna(atr): atr = price * 0.005 # Safety fallback

        # 3. Generate Signal
        signal = "NEUTRAL"
        emoji = "⚪️"
        
        if trend == "BULLISH":
            signal = "BUY BIAS"
            emoji = "🟢"
            sl = price - (1.5 * atr)
            tp = price + (2.5 * atr)
        else:
            signal = "SELL BIAS"
            emoji = "🔴"
            sl = price + (1.5 * atr)
            tp = price - (2.5 * atr)

        # 4. Format Message
        dec = 2 if "JPY" in symbol or "XAU" in symbol else 4
        return (
            f"<b>💎 V5.0 SIGNAL</b>\n"
            f"🪙 <b>{symbol}</b>: {price:.{dec}f}\n"
            f"👉 <b>{emoji} {signal}</b>\n"
            f"🎯 TP: {tp:.{dec}f}\n"
            f"🛑 SL: {sl:.{dec}f}\n"
            f"<i>Trend: {trend}</i>"
        )
    except Exception as e:
        print(f"Error {symbol}: {e}")
        return None

# --- BACKGROUND WORKER ---
def worker_loop():
    """This runs forever in the background."""
    print("⏳ Worker waiting 10s for server boot...")
    time.sleep(10) # Wait for Flask to start
    
    send_msg("🟢 <b>BOT ONLINE V5.0</b>\nEngine Started.\nScanning in 10 seconds...")
    time.sleep(10)

    while True:
        print("🔍 Starting Scan...")
        for symbol, ticker in PAIRS.items():
            msg = get_signal(symbol, ticker)
            if msg:
                send_msg(msg)
                time.sleep(2) # Don't spam Telegram
        
        print("😴 Sleeping for 30 minutes...")
        time.sleep(1800) # Sleep 30 mins

# --- WEB SERVER ---
@app.route('/')
def index():
    return "<h3>Bot V5.0 is Running.</h3>"

# --- LAUNCHER ---
# We start the thread ONLY when the script runs directly
if __name__ == '__main__':
    # Start the background worker
    t = threading.Thread(target=worker_loop, daemon=True)
    t.start()
    
    # Start the Web Server (Render needs this)
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
