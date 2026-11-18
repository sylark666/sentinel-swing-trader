import sqlite3
import pandas as pd
import numpy as np

tickers = ['IRB.NS', 'HAVELLS.NS', 'ICICIAMC.NS']

print("=" * 85)
print("TECHNICAL INDICATOR & EXIT EVALUATION FOR OPEN TRADES")
print("=" * 85)

with sqlite3.connect("market_data.db") as conn:
    for t in tickers:
        print(f"\n==================== {t} ====================")
        # Load 4H data
        q = "SELECT timestamp, open, high, low, close, volume FROM market_data WHERE ticker = ? AND timeframe = '4H' ORDER BY timestamp ASC"
        df = pd.read_sql_query(q, conn, params=(t,))
        if df.empty:
            print("No 4H data")
            continue
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        c = df['close']
        h = df['high']
        l = df['low']
        v = df['volume']
        
        # EMAs
        ema20 = c.ewm(span=20, adjust=False).mean()
        ema50 = c.ewm(span=50, adjust=False).mean()
        ema200 = c.ewm(span=200, adjust=False).mean()
        
        # ATR 14
        tr1 = h - l
        tr2 = (h - c.shift(1)).abs()
        tr3 = (l - c.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()
        
        # Last 5 bars
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        cmp = latest['close']
        last_high = latest['high']
        last_low = latest['low']
        last_ts = latest['timestamp']
        
        cur_ema20 = ema20.iloc[-1]
        cur_ema50 = ema50.iloc[-1]
        cur_ema200 = ema200.iloc[-1]
        cur_atr = atr14.iloc[-1]
        
        # Stage 1 Chandelier Stop (3.0x ATR from entry/peak)
        peak_high = h.iloc[-10:].max()
        chandelier_3x = peak_high - (3.0 * cur_atr)
        chandelier_2_3x = peak_high - (2.3 * cur_atr)
        
        print(f"Timestamp: {last_ts}")
        print(f"CMP: ₹{cmp:,.2f} | Bar Range: High ₹{last_high:,.2f}, Low ₹{last_low:,.2f}")
        print(f"EMAs: 20 EMA: ₹{cur_ema20:,.2f} | 50 EMA: ₹{cur_ema50:,.2f} | 200 EMA: ₹{cur_ema200:,.2f}")
        print(f"Trend Alignment: {'BULLISH (Above 200 EMA)' if cmp > cur_ema200 else 'BEARISH (Below 200 EMA)'}")
        print(f"14-bar ATR: ₹{cur_atr:,.2f}")
        print(f"Recent Peak High (last 10 bars): ₹{peak_high:,.2f}")
        print(f"Theoretical Chandelier Stops: 3.0x = ₹{chandelier_3x:,.2f} | 2.3x = ₹{chandelier_2_3x:,.2f}")
