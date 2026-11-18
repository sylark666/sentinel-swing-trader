import yfinance as yf
import pandas as pd

tickers = ['IRB.NS', 'HAVELLS.NS', 'ICICIAMC.NS']
print("=" * 80)
print("FETCHING ACTUAL MARKET DATA FOR ACTIVE TRADES (NOV 18, 2026)")
print("=" * 80)

for t in tickers:
    try:
        df = yf.download(t, period='5d', progress=False)
        if not df.empty:
            df.reset_index(inplace=True)
            df.columns = [col[0].lower() if isinstance(col, tuple) else col.lower() for col in df.columns]
            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else latest
            print(f"\nTicker: {t}")
            print(f"  Latest Date: {latest.get('date', latest.get('timestamp'))}")
            print(f"  Open: ₹{float(latest['open']):,.2f} | High: ₹{float(latest['high']):,.2f} | Low: ₹{float(latest['low']):,.2f} | Close: ₹{float(latest['close']):,.2f}")
    except Exception as e:
        print(f"Error fetching {t}: {e}")
