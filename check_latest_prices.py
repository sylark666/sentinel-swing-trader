import sqlite3
import pandas as pd
import numpy as np

tickers = ['IRB.NS', 'HAVELLS.NS', 'ICICIAMC.NS']

print("=" * 80)
print("FETCHING LATEST MARKET DATA FOR ACTIVE TRADES")
print("=" * 80)

with sqlite3.connect("market_data.db") as conn:
    for t in tickers:
        print(f"\n--- {t} ---")
        # Check available timeframes and latest timestamps
        q = f"SELECT timeframe, max(timestamp), count(*) FROM market_data WHERE ticker = ? GROUP BY timeframe"
        df_summary = pd.read_sql_query(q, conn, params=(t,))
        print("Data Summary:")
        print(df_summary)

        # Get latest 5 daily bars
        q_daily = f"SELECT timestamp, open, high, low, close, volume FROM market_data WHERE ticker = ? AND timeframe = '1D' ORDER BY timestamp DESC LIMIT 5"
        df_d = pd.read_sql_query(q_daily, conn, params=(t,))
        print("\nLatest Daily Bars:")
        print(df_d)

        # Get latest 5 4H bars
        q_4h = f"SELECT timestamp, open, high, low, close, volume FROM market_data WHERE ticker = ? AND timeframe = '4H' ORDER BY timestamp DESC LIMIT 5"
        df_4h = pd.read_sql_query(q_4h, conn, params=(t,))
        print("\nLatest 4H Bars:")
        print(df_4h)
