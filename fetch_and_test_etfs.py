"""
fetch_and_test_etfs.py
=============================================================================
Fetches historical daily data for the top Indian ETFs and benchmarks:
1. Raw Triple RSI Strategy (QuantifiedStrategies rules)
2. Triple RSI with 6% Stop Loss & 7-Day Time Stop
=============================================================================
"""

import os
import sys
import sqlite3
import pandas as pd
import numpy as np
import yfinance as yf

ETF_TICKERS = [
    "NIFTYBEES.NS",   # Nifty 50
    "BANKBEES.NS",    # Nifty Bank
    "JUNIORBEES.NS",  # Nifty Next 50
    "ITBEES.NS",      # Nifty IT
    "GOLDBEES.NS",    # Gold BeES
    "SILVERBEES.NS",  # Silver BeES
    "CPSEETF.NS",     # CPSE PSU ETF
    "MON100.NS",      # Motilal Oswal Nasdaq 100
    "PHARMABEES.NS",  # Pharma BeES
    "AUTOBEES.NS",    # Auto BeES
]

DB_NAME = "etf_market_data.db"

def calculate_rsi(series: pd.Series, period: int = 5) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

def fetch_etf_data():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS etf_daily (
            ticker TEXT,
            timestamp TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            PRIMARY KEY (ticker, timestamp)
        )
    """)
    conn.commit()

    print(f"Downloading historical data for {len(ETF_TICKERS)} Indian ETFs...")
    for ticker in ETF_TICKERS:
        try:
            print(f"Fetching {ticker}...")
            df = yf.download(ticker, start="2010-01-01", progress=False)
            if df.empty:
                print(f"  Warning: No data for {ticker}")
                continue
            df.reset_index(inplace=True)
            df.columns = [col[0].lower() if isinstance(col, tuple) else col.lower() for col in df.columns]
            df["timestamp"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            
            rows = []
            for _, r in df.iterrows():
                rows.append((ticker, r["timestamp"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["volume"])))
            
            c.executemany("INSERT OR REPLACE INTO etf_daily VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            conn.commit()
            print(f"  Saved {len(rows)} bars for {ticker}")
        except Exception as e:
            print(f"  Error fetching {ticker}: {e}")
    conn.close()

def backtest_etfs(sl_pct=0.0, max_days=0):
    conn = sqlite3.connect(DB_NAME)
    all_trades = []

    for ticker in ETF_TICKERS:
        q = f"SELECT timestamp, open, high, low, close, volume FROM etf_daily WHERE ticker = ? ORDER BY timestamp ASC"
        df = pd.read_sql_query(q, conn, params=(ticker,))
        if df.empty or len(df) < 220: continue

        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values(by="timestamp").reset_index(drop=True)

        c = df["close"]
        df["sma200"] = c.rolling(200).mean()
        df["rsi5"] = calculate_rsi(c, period=5)

        r = df["rsi5"]
        c1 = r < 30.0
        c2 = (r < r.shift(1)) & (r.shift(1) < r.shift(2)) & (r.shift(2) < r.shift(3))
        c3 = r.shift(3) < 60.0
        c4 = c > df["sma200"]
        df["buy_signal"] = c1 & c2 & c3 & c4
        df["exit_signal"] = r > 50.0

        in_pos = False
        entry_px = 0
        entry_date = None
        days = 0

        for idx, row in df.iterrows():
            if in_pos:
                days += 1
                close_px = row["close"]
                low_px = row["low"]
                exit_px = None
                reason = None

                if sl_pct > 0 and low_px <= entry_px * (1.0 - sl_pct):
                    exit_px = entry_px * (1.0 - sl_pct)
                    reason = "Stop_Loss"
                elif row["exit_signal"] and days >= 1:
                    exit_px = close_px
                    reason = "RSI5_Cross_50"
                elif max_days > 0 and days >= max_days:
                    exit_px = close_px
                    reason = "Time_Stop"

                if exit_px:
                    pnl_pct = (exit_px - entry_px) / entry_px * 100.0
                    all_trades.append({
                        "ticker": ticker,
                        "entry_date": entry_date,
                        "exit_date": row["timestamp"],
                        "days": days,
                        "entry_px": entry_px,
                        "exit_px": exit_px,
                        "pnl_pct": pnl_pct,
                        "reason": reason
                    })
                    in_pos = False
            else:
                if row["buy_signal"]:
                    in_pos = True
                    entry_px = row["close"]
                    entry_date = row["timestamp"]
                    days = 0

    conn.close()
    tdf = pd.DataFrame(all_trades)
    if tdf.empty: return None

    wins = tdf[tdf["pnl_pct"] > 0]
    losses = tdf[tdf["pnl_pct"] <= 0]
    pf = (wins["pnl_pct"].sum() / abs(losses["pnl_pct"].sum())) if len(losses) > 0 and abs(losses["pnl_pct"].sum()) > 0 else 999.0

    return {
        "trades": len(tdf),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(tdf) * 100.0,
        "pf": pf,
        "avg_win": wins["pnl_pct"].mean() if len(wins) > 0 else 0,
        "avg_loss": losses["pnl_pct"].mean() if len(losses) > 0 else 0,
        "worst_loss": tdf["pnl_pct"].min(),
        "avg_days": tdf["days"].mean(),
        "tdf": tdf
    }

if __name__ == "__main__":
    if not os.path.exists(DB_NAME):
        fetch_etf_data()
    else:
        # Check if DB has rows
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT count(*) FROM etf_daily")
        cnt = c.fetchone()[0]
        conn.close()
        if cnt < 100:
            fetch_etf_data()

    print("\n" + "=" * 115)
    print("BACKTESTING TRIPLE RSI ACROSS INDIAN ETFs (NiftyBeES, BankBeES, JuniorBeES, GoldBeES, etc.)")
    print("=" * 115)

    r_raw = backtest_etfs(sl_pct=0.0, max_days=0)
    r_sl = backtest_etfs(sl_pct=0.05, max_days=7)
    r_sl8 = backtest_etfs(sl_pct=0.08, max_days=10)

    print(f"{'Configuration':<40} | {'Trades':<7} | {'Win Rate %':<11} | {'PF':<6} | {'Avg Win':<8} | {'Avg Loss':<9} | {'Worst Trade':<11} | {'Avg Hold'}")
    print("-" * 115)
    for name, r in [("1. Raw Triple RSI (No Stop Loss)", r_raw),
                    ("2. With 5% Stop Loss + 7-Day Time Stop", r_sl),
                    ("3. With 8% Stop Loss + 10-Day Time Stop", r_sl8)]:
        if r:
            print(f"{name:<40} | {r['trades']:>6} | {r['win_rate']:>9.1f}% | {r['pf']:>4.2f} | +{r['avg_win']:>5.2f}% | {r['avg_loss']:>7.2f}% | {r['worst_loss']:>9.2f}% | {r['avg_days']:>5.1f} d")
    print("=" * 115)

    if r_raw:
        r_raw["tdf"].to_csv("trades_indian_etfs_triple_rsi.csv", index=False)
        print("\nSaved trade ledger to trades_indian_etfs_triple_rsi.csv")
