# main.py - PREMIER FOREX AI QUANT V2.14 (Targeted Edition)

import os
import ccxt
import pandas as pd
import numpy as np
import requests
import threading
import time
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, render_template_string
from dotenv import load_dotenv 

# --- CONFIGURATION ---
load_dotenv() 
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
APP_URL = os.getenv("RENDER_EXTERNAL_URL") 

# 🔥 ONLY YOUR 3 REQUESTED PAIRS
TARGET_PAIRS = ["GBP/JPY", "XAU/USD", "AUD/CAD"]

TIMEFRAME_HTF = "4h"
TIMEFRAME_LTF = "1h"

# Initialize Kraken
exchange = ccxt.kraken({
    'enableRateLimit': True, 
    'rateLimit': 2000,
    'params': {'timeout': 20000}
})

bot_stats = {
    "status": "initializing",
    "total_analyses": 0,
    "last_analysis": None,
    "version": "V2.14 Targeted"
}

# =========================================================================
# === TELEGRAM ENGINE (Direct HTTP) ===
# =========================================================================

def send_telegram_message(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"⚠️ Telegram Send Error: {e}")

def send_error_alert(symbol, error):
    msg = (f"⚠️ <b>PAIR ERROR: {symbol}</b>\n"
           f"Reason: {error}\n"
           f"<i>Kraken may not support this specific pair directly.</i>")
    send_telegram_message(msg)

# =========================================================================
# === SMART PAIR FINDER ===
# =========================================================================

def find_kraken_symbol(user_symbol):
    """Finds the correct Kraken ID for your specific pairs."""
    if not exchange.markets:
        try: exchange.load_markets()
        except: return None

    # 1. Check exact match
    if user_symbol in exchange.markets: return user_symbol

    # 2. Hardcoded fixes for your specific pairs
    if user_symbol == "XAU/USD": return "XAU/USD" # Often maps to XXAUZUSD automatically
    if user_symbol == "GBP/JPY": return "GBP/JPY" # Often maps to ZGBPZJPY automatically
    
    # 3. Deep Search (The fix for weird names)
    # Removes slash: AUD/CAD -> AUDCAD
    clean = user_symbol.replace("/", "") 
    for market_id in exchange.markets.keys():
        if clean in market_id:
            return market_id
            
    return None

def fetch_data_safe(user_symbol, timeframe):
    max_retries = 2
    for attempt in range(max_retries):
        try:
            kraken_id = find_kraken_symbol(user_symbol)
            if not kraken_id:
                raise ValueError("Pair not found on Kraken.")

            ohlcv = exchange.fetch_ohlcv(kraken_id, timeframe, limit=100)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df.dropna()
        except Exception as e:
            if attempt == max_retries - 1: raise e
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
    df['is_high'] = df['high'][(df['high'].shift(1) < df['high']) & (df['high'].shift(-1) < df['high'])]
    df['is_low'] = df['low'][(df['low'].shift(1) > df['low']) & (df['low'].shift(-1) > df['low'])]
    last_highs = df['is_high'].dropna().tail(2)
    last_lows = df['is_low'].dropna().tail(2)
    
    if len(last_highs) < 2 or len(last_lows) < 2: return "NEUTRAL"
    if last_highs.iloc[-1] > last_highs.iloc[-2] and last_lows.iloc[-1] > last_lows.iloc[-2]: return "BULLISH"
    elif last_highs.iloc[-1] < last_highs.iloc[-2] and last_lows.iloc[-1] < last_lows.iloc[-2]: return "BEARISH"
    return "NEUTRAL"

def detect_fvg(df):
    recent = df.iloc[-6:-1] 
    fvg_zone, fvg_type = None, None
    for i in range(len(recent) - 2):
        curr_high = float(recent.iloc[i]['high'])
        next_low = float(recent.iloc[i+2]['low'])
        if next_low > curr_high:
            fvg_zone, fvg_type = (curr_high, next_low), "BULLISH_FVG"
            
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
        df_htf = fetch_data_safe(symbol, TIMEFRAME_HTF)
        df_ltf = fetch_data_safe(symbol, TIMEFRAME_LTF)
        if df_htf.empty or df_ltf.empty: return

        price = float(df_ltf.iloc[-1]['close'])
        structure = detect_structure(df_htf)
        atr = float(calculate_atr(df_ltf).iloc[-1])
        fvg_type, fvg_zone = detect_fvg(df_ltf)

        signal, color = "NEUTRAL (WAIT)", "⚪️"
        
        # --- LOGIC ---
        if structure == "BULLISH" and fvg_type == "BULLISH_FVG":
            signal, color = "STRONG BUY", "🟢"
            sl, tp1, tp2 = price - (1.5*atr), price + (2.0*atr), price + (3.5*atr)
        elif structure == "BEARISH" and fvg_type == "BEARISH_FVG":
            signal, color = "STRONG SELL", "🔴"
            sl, tp1, tp2 = price + (1.5*atr), price - (2.0*atr), price - (3.5*atr)
        else:
            if not force_send: return
            # Default levels for status report
            sl = price - (2.0*atr) if structure == "BULLISH" else price + (2.0*atr)
            tp1 = price + (2.0*atr) if structure == "BULLISH" else price - (2.0*atr)
            tp2 = price + (3.0*atr) if structure == "BULLISH" else price - (3.0*atr)

        # Formatting
        dec = 2 if "XAU" in symbol or "JPY" in symbol else 5
        zone_txt = f"{fvg_zone[0]:.{dec}f} - {fvg_zone[1]:.{dec}f}" if fvg_zone else "None"

        msg = (
            f"<b>💎 PREMIUM QUANT SIGNAL</b>\n"
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
    print(f"🚀 Initializing V2.14 Targeted...")
    threading.Thread(target=send_telegram_message, args=(
        f"🟢 <b>SYSTEM ONLINE: V2.14</b>\n"
        f"Targeting: GBP/JPY, XAU/USD, AUD/CAD\n"
        f"<i>Starting scan...</i>",
    )).start()

    scheduler = BackgroundScheduler()
    for s in TARGET_PAIRS:
        scheduler.add_job(generate_and_send_signal, 'cron', minute='0,30', args=[s, False])
    
    scheduler.add_job(keep_alive, 'interval', minutes=10)
    scheduler.start()
    
    # FORCE RUN NOW
    for s in TARGET_PAIRS:
        threading.Thread(target=generate_and_send_signal, args=(s, True)).start()

start_bot()

app = Flask(__name__)
@app.route('/')
def home(): return render_template_string("<h3>Targeted Bot Running V2.14</h3>")
@app.route('/health')
def health(): return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
