# main.py - PREMIER FOREX AI QUANT V3.0 (Yahoo Finance Engine)

import os
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import threading
import time
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, render_template_string

# --- CONFIGURATION ---
from dotenv import load_dotenv 
load_dotenv() 

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
APP_URL = os.getenv("RENDER_EXTERNAL_URL") 

# YAHOO FINANCE TICKER MAPPING
# We map your requested pairs to Yahoo's specific format
PAIRS_CONFIG = {
    "GBP/JPY": "GBPJPY=X",
    "XAU/USD": "GC=F",      # Gold Futures (Standard for XAU trading)
    "AUD/CAD": "AUDCAD=X",
    "EUR/USD": "EURUSD=X",  
    "BTC/USD": "BTC-USD"    
}

# The pairs you specifically asked for
ACTIVE_PAIRS = ["GBP/JPY", "XAU/USD", "AUD/CAD"]

bot_stats = {
    "status": "initializing",
    "total_analyses": 0,
    "last_analysis": None,
    "version": "V3.0 Yahoo Data"
}

# =========================================================================
# === TELEGRAM ENGINE (Direct HTTP - Crash Proof) ===
# =========================================================================

def send_telegram_message(message):
    """Sends message via HTTP to avoid asyncio event loop crashes."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"⚠️ Telegram Send Error: {e}")

def send_error_alert(symbol, error):
    msg = (f"⚠️ <b>DATA ERROR: {symbol}</b>\nReason: {error}")
    send_telegram_message(msg)

# =========================================================================
# === DATA ENGINE (Yahoo Finance) ===
# =========================================================================

def fetch_data_safe(user_symbol):
    """Fetches data from Yahoo Finance."""
    max_retries = 2
    ticker = PAIRS_CONFIG.get(user_symbol)
    
    if not ticker: 
        print(f"❌ Unknown ticker for {user_symbol}")
        return pd.DataFrame()

    for attempt in range(max_retries):
        try:
            # Fetch 5 days of 1h data
            df = yf.download(tickers=ticker, period="5d", interval="1h", progress=False)
            
            if df.empty: raise ValueError("No data returned")

            # Clean up Yahoo's multi-index columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # Rename columns to standard lowercase
            df = df.rename(columns={
                "Date": "timestamp", "Datetime": "timestamp", 
                "Open": "open", "High": "high", "Low": "low", "Close": "close"
            })
            
            # Ensure strictly float type (Fixes the np.float64 print error)
            cols = ['open', 'high', 'low', 'close']
            for c in cols:
                if c in df.columns:
                    df[c] = df[c].astype(float)
            
            return df
        except Exception as e:
            time.sleep(2)
            
    return pd.DataFrame()

# =========================================================================
# === ANALYTICAL ENGINES ===
# =========================================================================

def calculate_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    return np.max(ranges, axis=1).rolling(period).mean()

def detect_structure(df):
    if len(df) < 20: return "NEUTRAL"
    
    # Simple Moving Average Trend Logic for stability
    short_ma = df['close'].rolling(8).mean().iloc[-1]
    long_ma = df['close'].rolling(21).mean().iloc[-1]

    if short_ma > long_ma: return "BULLISH"
    if short_ma < long_ma: return "BEARISH"
    return "NEUTRAL"

def detect_fvg(df):
    # Scan last 5 candles
    recent = df.iloc[-5:-1] 
    fvg_zone, fvg_type = None, None
    
    for i in range(len(recent) - 2):
        curr_high = float(recent.iloc[i]['high'])
        next_low = float(recent.iloc[i+2]['low'])
        
        # Bullish FVG
        if next_low > curr_high:
            fvg_zone, fvg_type = (curr_high, next_low), "BULLISH_FVG"
            
        # Bearish FVG
        curr_low = float(recent.iloc[i]['low'])
        next_high = float(recent.iloc[i+2]['high'])
        if next_high < curr_low:
            fvg_zone, fvg_type = (next_high, curr_low), "BEARISH_FVG"
            
    return fvg_type, fvg_zone

# =========================================================================
# === SIGNAL GENERATOR ===
# =========================================================================

def generate_and_send_signal(symbol, force_send=False):
    global bot_stats
    try:
        df = fetch_data_safe(symbol)
        if df.empty: 
            if force_send: send_error_alert(symbol, "Yahoo returned empty data")
            return

        price = float(df.iloc[-1]['close'])
        structure = detect_structure(df)
        
        df['atr'] = calculate_atr(df)
        # Fix: Handle case where ATR might be NaN at start
        if pd.isna(df.iloc[-1]['atr']):
            atr = price * 0.005 # Fallback to 0.5% if ATR fails
        else:
            atr = float(df.iloc[-1]['atr'])

        fvg_type, fvg_zone = detect_fvg(df)

        signal, color = "NEUTRAL (WAIT)", "⚪️"
        
        # --- STRATEGY LOGIC ---
        if structure == "BULLISH" and fvg_type == "BULLISH_FVG":
            signal, color = "STRONG BUY", "🟢"
            sl = price - (1.5 * atr)
            tp1 = price + (2.0 * atr)
            tp2 = price + (3.5 * atr)
            
        elif structure == "BEARISH" and fvg_type == "BEARISH_FVG":
            signal, color = "STRONG SELL", "🔴"
            sl = price + (1.5 * atr)
            tp1 = price - (2.0 * atr)
            tp2 = price - (3.5 * atr)
            
        else:
            if not force_send: return
            # Default levels for status report
            if structure == "BULLISH":
                sl, tp1, tp2 = price-(2*atr), price+(2*atr), price+(3*atr)
            else:
                sl, tp1, tp2 = price+(2*atr), price-(2*atr), price-(3*atr)

        # Formatting
        dec = 2 # Gold/Yen usually 2 decimals
        if "AUD" in symbol or "EUR" in symbol: dec = 5
        
        zone_txt = f"{fvg_zone[0]:.{dec}f} - {fvg_zone[1]:.{dec}f}" if fvg_zone else "None"

        msg = (
            f"<b>💎 V3.0 YAHOO SIGNAL</b>\n"
            f"──────────────────────\n"
            f"<b>🪙 ASSET:</b> #{symbol.replace('/','')}\n"
            f"<b>💵 PRICE:</b> <code>{price:.{dec}f}</code>\n"
            f"──────────────────────\n"
            f"<b>👉 DIRECTION: {color} {signal}</b>\n"
            f"──────────────────────\n"
            f"<b>🎯 TP 1:</b> <code>{tp1:.{dec}f}</code>\n"
            f"<b>🚀 TP 2:</b> <code>{tp2:.{dec}f}</code>\n"
            f"<b>🛑 SL:</b>  <code>{sl:.{dec}f}</code>\n"
            f"──────────────────────\n"
            f"<b>📊 ANALYSIS:</b>\n"
            f"• <b>Trend:</b> {structure}\n"
            f"• <b>Zone:</b> {zone_txt}\n"
        )
        send_telegram_message(msg)
        bot_stats['total_analyses'] += 1
        bot_stats['last_analysis'] = datetime.now().isoformat()

    except Exception as e:
        if force_send: send_error_alert(symbol, str(e))
        print(f"❌ Failed {symbol}: {e}")

# =========================================================================
# === RUNNER ===
# =========================================================================

def keep_alive():
    if APP_URL:
        try: requests.get(f"{APP_URL}/health", timeout=5)
        except: pass

def start_bot():
    print(f"🚀 Initializing V3.0 Yahoo Engine...")
    
    # Startup Msg
    pairs_str = ", ".join(ACTIVE_PAIRS)
    threading.Thread(target=send_telegram_message, args=(
        f"🟢 <b>SYSTEM ONLINE: V3.0</b>\n"
        f"Sources: {pairs_str}\n"
        f"<i>Starting Analysis...</i>",
    )).start()

    scheduler = BackgroundScheduler()
    # Runs every 30 mins
    for s in ACTIVE_PAIRS:
        scheduler.add_job(generate_and_send_signal, 'cron', minute='0,30', args=[s, False])
    
    scheduler.add_job(keep_alive, 'interval', minutes=10)
    scheduler.start()
    
    # FORCE RUN NOW (With 10s delay to let server boot)
    def delayed_start():
        time.sleep(10) 
        for s in ACTIVE_PAIRS:
            generate_and_send_signal(s, True)
            
    threading.Thread(target=delayed_start).start()

# Start the bot logic
start_bot()

# Flask App for Render
app = Flask(__name__)

@app.route('/')
def home(): return render_template_string("<h3>V3.0 Yahoo Bot Running</h3>")

@app.route('/health')
def health(): return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
