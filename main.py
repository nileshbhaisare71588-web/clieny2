import os
import ccxt
import pandas as pd
import numpy as np
import asyncio
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Bot
from flask import Flask, jsonify, render_template_string
import threading
import time
import traceback

# --- CONFIGURATION ---
from dotenv import load_dotenv
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# UPDATED: Assets to monitor (Forex + Crypto + Gold)
# Defaults to your specific list if not found in .env
DEFAULT_PAIRS = "EUR/USD,GBP/JPY,AUD/USD,GBP/USD,XAU/USD,AUD/CAD,AUD/JPY,BTC/USD"
CRYPTOS = [s.strip() for s in os.getenv("CRYPTOS", DEFAULT_PAIRS).split(',')]

TIMEFRAME_MAIN = "4h"  # Major Trend
TIMEFRAME_ENTRY = "1h" # Entry Precision

# Initialize Bot and Exchange (Kraken Public)
# Note: Ensure your Kraken account/tier supports these Forex pairs via API
bot = Bot(token=TELEGRAM_BOT_TOKEN)
exchange = ccxt.kraken({'enableRateLimit': True, 'rateLimit': 2000})

bot_stats = {
    "status": "initializing",
    "total_analyses": 0,
    "last_analysis": None,
    "monitored_assets": CRYPTOS,
    "uptime_start": datetime.now().isoformat(),
    "version": "V3.0 Nilesh Pro Quant"
}

# =========================================================================
# === ADVANCED QUANT LOGIC & INDICATORS ===
# =========================================================================

def calculate_cpr_levels(df_daily):
    """Calculates Daily Pivot Points for Professional Target Setting."""
    if df_daily.empty or len(df_daily) < 2: return None
    prev_day = df_daily.iloc[-2]
    H, L, C = prev_day['high'], prev_day['low'], prev_day['close']
    PP = (H + L + C) / 3.0
    BC = (H + L) / 2.0
    TC = PP - BC + PP
    return {
        'PP': PP, 'TC': TC, 'BC': BC,
        'R1': 2*PP - L, 'S1': 2*PP - H,
        'R2': PP + (H - L), 'S2': PP - (H - L)
    }

def add_technical_indicators(df):
    """Adds RSI, MACD, Bollinger Bands, EMA, and ATR to the dataframe."""
    if df.empty: return df
    
    # 1. EMAs for Trend
    df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()

    # 2. RSI (Relative Strength Index) - 14 periods
    delta = df['close'].diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=13, adjust=False).mean()
    ema_down = down.ewm(com=13, adjust=False).mean()
    rs = ema_up / ema_down
    df['rsi'] = 100 - (100 / (1 + rs))

    # 3. MACD (12, 26, 9)
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['signal_line'] = df['macd'].ewm(span=9, adjust=False).mean()

    # 4. Bollinger Bands (20, 2)
    df['bb_middle'] = df['close'].rolling(window=20).mean()
    df['bb_std'] = df['close'].rolling(window=20).std()
    df['bb_upper'] = df['bb_middle'] + (2 * df['bb_std'])
    df['bb_lower'] = df['bb_middle'] - (2 * df['bb_std'])

    # 5. ATR (Average True Range) for Volatility
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    df['atr'] = true_range.rolling(14).mean()

    return df

def fetch_data_safe(symbol, timeframe):
    """Robust fetcher with retries."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            if not exchange.markets: exchange.load_markets()
            # Handle potential symbol mapping issues
            try:
                market = exchange.market(symbol)
            except:
                # Fallback: try to find the symbol in the list keys
                found = [k for k in exchange.markets.keys() if symbol in k]
                if found:
                    market = exchange.markets[found[0]]
                else:
                    print(f"⚠️ Symbol {symbol} not found on Kraken.")
                    return pd.DataFrame()

            # Increased limit to 300 to ensure EMA 200 can be calculated
            ohlcv = exchange.fetch_ohlcv(market['symbol'], timeframe, limit=300, params={'timeout': 20000})
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            
            # Add Indicators immediately
            df = add_technical_indicators(df)
            
            return df.dropna()
        except Exception as e:
            # print(f"Retry {attempt} for {symbol}: {e}") # debug
            if attempt < max_retries - 1: time.sleep(5)
    return pd.DataFrame()

# =========================================================================
# === MULTI-TIMEFRAME CONFLUENCE ENGINE ===
# =========================================================================

def determine_signal_strength(row_4h, row_1h, cpr_data):
    """
    Analyzes all indicators to produce a weighted signal score.
    Returns: Signal String, Emoji, Score, Volatility Status
    """
    score = 0
    price = row_4h['close']
    
    # --- TREND ANALYSIS (4H) ---
    # EMA 200 is the "King" of trend
    if price > row_4h['ema_200']: score += 1
    else: score -= 1
    
    # EMA 50 crossing (Mid-term trend)
    if row_4h['ema_50'] > row_4h['ema_200']: score += 1
    else: score -= 1

    # --- MOMENTUM ANALYSIS (1H & 4H) ---
    # RSI Logic
    if 50 < row_4h['rsi'] < 70: score += 0.5 # Healthy Bullish
    elif row_4h['rsi'] > 70: score -= 0.5 # Overbought risk
    elif 30 < row_4h['rsi'] < 50: score -= 0.5 # Bearish
    elif row_4h['rsi'] < 30: score += 0.5 # Oversold bounce potential

    # MACD Logic
    if row_4h['macd'] > row_4h['signal_line']: score += 1
    else: score -= 1

    # --- PRICE ACTION / CPR ---
    if price > cpr_data['PP']: score += 0.5
    else: score -= 0.5

    # --- VOLATILITY CHECK ---
    volatility_state = "Normal"
    # If price is outside Bollinger Bands, volatility is extreme
    if price > row_4h['bb_upper']: volatility_state = "High (Overextended Up)"
    elif price < row_4h['bb_lower']: volatility_state = "High (Overextended Down)"
    
    # --- FINAL VERDICT ---
    if score >= 3:
        return "STRONG BUY", "🚀", score, volatility_state
    elif 1 <= score < 3:
        return "BUY", "🟢", score, volatility_state
    elif -3 < score <= -1:
        return "SELL", "🔴", score, volatility_state
    elif score <= -3:
        return "STRONG SELL", "🔻", score, volatility_state
    else:
        return "NEUTRAL / WAIT", "⚖️", score, volatility_state

def generate_and_send_signal(symbol):
    global bot_stats
    try:
        # 1. Fetch Multi-Timeframe Data
        df_4h = fetch_data_safe(symbol, TIMEFRAME_MAIN)
        df_1h = fetch_data_safe(symbol, TIMEFRAME_ENTRY)
        
        # Fetch Daily for CPR Targets
        if not exchange.markets: exchange.load_markets()
        
        # Safe symbol handling for Daily fetch
        try:
             # Try direct access or search
            market_id = exchange.market(symbol)['id']
        except:
             # Fallback if symbol naming is tricky
            keys = [k for k in exchange.markets.keys() if symbol in k]
            if not keys: return
            market_id = exchange.markets[keys[0]]['id']

        ohlcv_d = exchange.fetch_ohlcv(market_id, '1d', limit=5)
        df_d = pd.DataFrame(ohlcv_d, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        cpr = calculate_cpr_levels(df_d)

        if df_4h.empty or df_1h.empty or cpr is None: return

        # 2. Extract Key Values (Latest Candle)
        last_4h = df_4h.iloc[-1]
        last_1h = df_1h.iloc[-1]
        price = last_4h['close']
        
        # 3. Get Advanced Signal
        signal, emoji, score, vol_status = determine_signal_strength(last_4h, last_1h, cpr)
        
        # 4. Calculate Dynamic Targets
        is_buy = score > 0
        
        # Targets based on CPR
        tp1 = cpr['R1'] if is_buy else cpr['S1']
        tp2 = cpr['R2'] if is_buy else cpr['S2']
        
        # Stop Loss: Use ATR for dynamic stop loss (1.5x ATR) or CPR
        atr_sl_buffer = last_4h['atr'] * 1.5
        sl_atr = price - atr_sl_buffer if is_buy else price + atr_sl_buffer
        
        # We start with CPR SL, but if ATR SL is tighter/safer, use that logic visually
        sl_cpr = min(cpr['BC'], cpr['TC']) if is_buy else max(cpr['BC'], cpr['TC'])
        
        # Format numbers for Forex (4 decimals) vs Crypto (2 decimals)
        fmt = ",.2f" if "JPY" in symbol or "XAU" in symbol or "BTC" in symbol else ",.4f"

        # --- PREMIUM HTML TEMPLATE ---
        message = (
            f"╔════════════════════════════════╗\n"
            f"  🏆 <b>NILESH PREMIUM AI SIGNAL</b>\n"
            f"╚════════════════════════════════╝\n\n"
            f"<b>Asset:</b> {symbol}\n"
            f"<b>Price:</b> <code>{price:{fmt}}</code>\n"
            f"<b>Volatility:</b> {vol_status}\n\n"
            f"--- 🚨 {emoji} <b>{signal}</b> 🚨 ---\n\n"
            f"<b>📊 INDICATOR DASHBOARD:</b>\n"
            f"• <b>Trend (EMA200):</b> {'BULLISH' if price > last_4h['ema_200'] else 'BEARISH'}\n"
            f"• <b>Momentum (RSI):</b> <code>{last_4h['rsi']:.1f}</code>\n"
            f"• <b>MACD:</b> {'Bullish' if last_4h['macd'] > last_4h['signal_line'] else 'Bearish'}\n"
            f"• <b>Pivot:</b> {'Above' if price > cpr['PP'] else 'Below'} PP\n\n"
            f"<b>🎯 TRADE PLAN:</b>\n"
            f"✅ <b>TP 1 (Conservative):</b> <code>{tp1:{fmt}}</code>\n"
            f"🔥 <b>TP 2 (Aggressive):</b> <code>{tp2:{fmt}}</code>\n"
            f"🛑 <b>Stop Loss (ATR/CPR):</b> <code>{sl_atr:{fmt}}</code>\n\n"
            f"----------------------------------------\n"
            f"<i>Powered by Advanced CPR By Nilesh</i>"
        )

        asyncio.run(bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='HTML'))
        
        bot_stats['total_analyses'] += 1
        bot_stats['last_analysis'] = datetime.now().isoformat()
        bot_stats['status'] = "operational"

    except Exception as e:
        print(f"❌ Analysis failed for {symbol}: {e}")
        traceback.print_exc()

# =========================================================================
# === GUNICORN-SAFE INITIALIZATION ===
# =========================================================================

def start_bot():
    print(f"🚀 Initializing {bot_stats['version']}...")
    scheduler = BackgroundScheduler()
    
    # Stagger jobs slightly to avoid hitting API limits all at once
    for idx, s in enumerate(CRYPTOS):
        # Schedule for every hour and half-hour + small offset based on index
        scheduler.add_job(generate_and_send_signal, 'cron', minute='0,30', second=idx*2, args=[s.strip()])
    
    scheduler.start()
    
    # Run first analysis immediately
    for s in CRYPTOS:
        threading.Thread(target=generate_and_send_signal, args=(s.strip(),)).start()

start_bot()

app = Flask(__name__)

@app.route('/')
def home():
    return render_template_string("""
        <body style="font-family:sans-serif; background:#0f172a; color:#f8fafc; text-align:center; padding-top:100px;">
            <div style="background:#1e293b; display:inline-block; padding:40px; border-radius:15px; border: 1px solid #334155;">
                <h1 style="color:#22d3ee;">Nilesh AI Quant Dashboard</h1>
                <p style="font-size:1.2em;">Status: <span style="color:#4ade80;">Active</span></p>
                <p>Analyses Streamed: <b>{{a}}</b></p>
                <p>Assets: <span style="font-size:0.8em; color:#cbd5e1;">{{m}}</span></p>
                <p>Version: <i>{{v}}</i></p>
                <hr style="border-color:#334155;">
                <p style="font-size:0.8em; color:#94a3b8;">Last Update: {{t}}</p>
            </div>
        </body>
    """, a=bot_stats['total_analyses'], v=bot_stats['version'], t=bot_stats['last_analysis'], m=", ".join(bot_stats['monitored_assets']))

@app.route('/health')
def health(): return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
