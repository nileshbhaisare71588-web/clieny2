# main.py - PREMIER FOREX AI QUANT V3.1 (Safe-Boot Edition)

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

# Pairs Map
PAIRS_CONFIG = {
    "GBP/JPY": "GBPJPY=X",
    "XAU/USD": "GC=F",      
    "AUD/CAD": "AUDCAD=X"
}
ACTIVE_PAIRS = list(PAIRS_CONFIG.keys())

bot_stats = {
    "status": "booting",
    "total_analyses": 0,
    "last_run": "Never",
    "version": "V3.1 Safe-Boot"
}

app = Flask(__name__)

# =========================================================================
# === TELEGRAM ENGINE ===
# =========================================================================

def send_telegram_message(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"⚠️ Telegram Error: {e}")

def send_error_alert(symbol, error):
    send_telegram_message(f"⚠️ <b>ERROR: {symbol}</b>\n{error}")

# =========================================================================
# === DATA & ANALYSIS ENGINE (Yahoo Finance) ===
# =========================================================================

def fetch_data(symbol):
    ticker = PAIRS_CONFIG.get(symbol)
    if not ticker: return pd.DataFrame()
    
    try:
        # Fast download, no progress bar
        df = yf.download(ticker, period="5d", interval="1h", progress=False)
        if df.empty: return pd.DataFrame()
        
        # Cleanup
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        df = df.rename(columns={"Date":"timestamp", "Open":"open", "High":"high", "Low":"low", "Close":"close"})
        
        # Force floats
        cols = ['open','high','low','close']
        for c in cols: 
            if c in df.columns: df[c] = df[c].astype(float)
            
        return df
    except Exception as e:
        print(f"Data error {symbol}: {e}")
        return pd.DataFrame()

def analyze_market(symbol, force=False):
    global bot_stats
    df = fetch_data(symbol)
    if df.empty:
        if force: send_error_alert(symbol, "No Data from Yahoo")
        return

    # Indicators
    price = float(df.iloc[-1]['close'])
    
    # Simple Trend
    sma_short = df['close'].rolling(8).mean().iloc[-1]
    sma_long = df['close'].rolling(21).mean().iloc[-1]
    trend = "BULLISH" if sma_short > sma_long else "BEARISH"
    
    # ATR
    high_low = df['high'] - df['low']
    atr = float(high_low.rolling(14).mean().iloc[-1])
    if pd.isna(atr) or atr == 0: atr = price * 0.005 # Fallback
    
    # FVG Scan
    fvg_type, fvg_zone = None, None
    recent = df.iloc[-5:-1]
    for i in range(len(recent)-2):
        if float(recent.iloc[i+2]['low']) > float(recent.iloc[i]['high']):
            fvg_type, fvg_zone = "BULLISH_FVG", (recent.iloc[i]['high'], recent.iloc[i+2]['low'])
        if float(recent.iloc[i+2]['high']) < float(recent.iloc[i]['low']):
            fvg_type, fvg_zone = "BEARISH_FVG", (recent.iloc[i+2]['high'], recent.iloc[i]['low'])

    # Signal Logic
    signal, color = "NEUTRAL", "⚪️"
    
    if trend == "BULLISH" and fvg_type == "BULLISH_FVG":
        signal, color = "STRONG BUY", "🟢"
        sl, tp1, tp2 = price-(1.5*atr), price+(2*atr), price+(3.5*atr)
    elif trend == "BEARISH" and fvg_type == "BEARISH_FVG":
        signal, color = "STRONG SELL", "🔴"
        sl, tp1, tp2 = price+(1.5*atr), price-(2*atr), price-(3.5*atr)
    else:
        if not force: return
        # Default levels for test/status
        sl = price-(2*atr) if trend == "BULLISH" else price+(2*atr)
        tp1, tp2 = (price+(2*atr), price+(3*atr)) if trend == "BULLISH" else (price-(2*atr), price-(3*atr))

    # Send Message
    dec = 2 if "JPY" in symbol or "XAU" in symbol else 5
    z_txt = f"{fvg_zone[0]:.{dec}f}-{fvg_zone[1]:.{dec}f}" if fvg_zone else "None"
    
    msg = (
        f"<b>💎 V3.1 SIGNAL</b>\n"
        f"────────────────\n"
        f"<b>🪙 {symbol}</b> | <b>💵 {price:.{dec}f}</b>\n"
        f"────────────────\n"
        f"<b>👉 {color} {signal}</b>\n"
        f"────────────────\n"
        f"<b>TP1:</b> {tp1:.{dec}f} | <b>TP2:</b> {tp2:.{dec}f}\n"
        f"<b>SL:</b>  {sl:.{dec}f}\n"
        f"────────────────\n"
        f"Trend: {trend} | Zone: {z_txt}"
    )
    send_telegram_message(msg)
    bot_stats['total_analyses'] += 1
    bot_stats['last_run'] = datetime.now().isoformat()

# =========================================================================
# === SCHEDULER & BOOTSTRAP ===
# =========================================================================

def run_scheduler():
    scheduler = BackgroundScheduler()
    # Run every 30 mins
    for s in ACTIVE_PAIRS:
        scheduler.add_job(analyze_market, 'cron', minute='0,30', args=[s, False])
    scheduler.start()
    print("⏰ Scheduler Started")

def boot_sequence():
    """Runs 10 seconds AFTER app start to prevent timeouts"""
    time.sleep(10)
    print("🚀 Running Boot Sequence...")
    run_scheduler()
    
    # Send Startup Msg
    send_telegram_message(f"🟢 <b>SYSTEM ONLINE: V3.1</b>\nYahoo Engine Ready.\nWaiting for signals...")
    
    # Force run once to verify data
    for s in ACTIVE_PAIRS:
        analyze_market(s, True)

# Start background thread immediately, but it waits 10s before doing work
threading.Thread(target=boot_sequence, daemon=True).start()

# =========================================================================
# === WEB INTERFACE (For Debugging) ===
# =========================================================================

@app.route('/')
def home():
    return render_template_string("""
    <html>
        <body style="background:#111; color:white; font-family:sans-serif; text-align:center; padding:50px;">
            <h1>🤖 V3.1 Dashboard</h1>
            <p>Status: <span style="color:#0f0;">Online</span></p>
            <p>Analyses Sent: {{ total }}</p>
            <hr>
            <h3>👇 Troubleshooting 👇</h3>
            <a href="/trigger" style="background:#f00; color:white; padding:15px; text-decoration:none; border-radius:5px;">
                CLICK TO FORCE TEST SIGNALS
            </a>
        </body>
    </html>
    """, total=bot_stats['total_analyses'])

@app.route('/trigger')
def trigger():
    # Manual Trigger Endpoint
    def run_manual():
        send_telegram_message("⚠️ <b>Manual Test Triggered...</b>")
        for s in ACTIVE_PAIRS:
            analyze_market(s, True)
    
    threading.Thread(target=run_manual).start()
    return "Signals Triggered! Check Telegram.", 200

@app.route('/health')
def health(): return "OK", 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
