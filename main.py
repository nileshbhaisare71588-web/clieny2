# main.py - PREMIER FOREX AI QUANT V4.0 (Passive Server Edition)

import os
import yfinance as yf
import pandas as pd
import numpy as np
import requests
from flask import Flask, jsonify
from datetime import datetime

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# The pairs you want
PAIRS = {
    "GBP/JPY": "GBPJPY=X",
    "XAU/USD": "GC=F",
    "AUD/CAD": "AUDCAD=X"
}

app = Flask(__name__)

# --- TELEGRAM FUNCTION ---
def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=5)
    except Exception as e:
        print(f"Telegram Fail: {e}")

# --- ANALYSIS ENGINE ---
def run_analysis_for_pair(symbol, ticker):
    try:
        # 1. Fetch Data (Fast Mode)
        df = yf.download(ticker, period="5d", interval="1h", progress=False)
        if df.empty: return None

        # Fix Data Format
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        df = df.rename(columns={"Close": "close", "High": "high", "Low": "low", "Open": "open"})
        price = float(df['close'].iloc[-1])

        # 2. Strategy: Trend + ATR
        short_ma = df['close'].rolling(8).mean().iloc[-1]
        long_ma = df['close'].rolling(21).mean().iloc[-1]
        trend = "BULLISH" if short_ma > long_ma else "BEARISH"

        atr = (df['high'] - df['low']).rolling(14).mean().iloc[-1]
        if pd.isna(atr): atr = price * 0.005

        # 3. Signals
        signal = "NEUTRAL"
        emoji = "⚪️"
        
        # Simple Logic: Price > Long MA = Buy Bias
        if trend == "BULLISH" and price > long_ma:
            signal = "BUY BIAS"
            emoji = "🟢"
            tp = price + (2 * atr)
            sl = price - (1.5 * atr)
        elif trend == "BEARISH" and price < long_ma:
            signal = "SELL BIAS"
            emoji = "🔴"
            tp = price - (2 * atr)
            sl = price + (1.5 * atr)
        else:
            return None # Skip neutral signals to save noise

        # 4. Construct Message
        dec = 2 if "JPY" in symbol or "XAU" in symbol else 4
        return (
            f"<b>💎 V4.0 SIGNAL</b>\n"
            f"🪙 <b>{symbol}</b>: {price:.{dec}f}\n"
            f"👉 <b>{emoji} {signal}</b>\n"
            f"🎯 TP: {tp:.{dec}f}\n"
            f"🛑 SL: {sl:.{dec}f}"
        )

    except Exception as e:
        return f"⚠️ Error analyzing {symbol}: {str(e)}"

# --- WEB ENDPOINTS ---

@app.route('/')
def home():
    return "<h3>🤖 V4.0 is Online.</h3><p>Use /scan to trigger signals.</p>"

@app.route('/scan')
def scan_market():
    """Hitting this URL manually triggers the bot."""
    results = []
    send_telegram("🔍 <b>Manual Scan Started...</b>")
    
    for symbol, ticker in PAIRS.items():
        msg = run_analysis_for_pair(symbol, ticker)
        if msg:
            send_telegram(msg)
            results.append(f"Sent: {symbol}")
        else:
            results.append(f"Skipped: {symbol} (Neutral)")
            
    return jsonify({"status": "success", "details": results})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
