import sqlite3
import pandas as pd
import numpy as np

def calculate_rsi(series: pd.Series, period: int = 5) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))

with sqlite3.connect("market_data.db") as conn:
    for sym in ["NIFTY", "NIFTY.NS", "^NSEI"]:
        q = "SELECT timestamp, open, high, low, close FROM market_data WHERE ticker = ? AND timeframe = '1D' ORDER BY timestamp ASC"
        df = pd.read_sql_query(q, conn, params=(sym,))
        if not df.empty:
            print(f"Found {sym}: {len(df)} daily bars")
            break

if df.empty:
    print("No Nifty 1D data in market_data.db, fetching from yfinance...")
    import yfinance as yf
    df = yf.download("^NSEI", start="2005-01-01")
    df.reset_index(inplace=True)
    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]

df["timestamp"] = pd.to_datetime(df["timestamp"] if "timestamp" in df else df["date"])
df = df.sort_values(by="timestamp").reset_index(drop=True)
c = df["close"]
df["sma200"] = c.rolling(200).mean()
df["rsi5"] = calculate_rsi(c, 5)

r = df["rsi5"]
c1 = r < 30.0
c2 = (r < r.shift(1)) & (r.shift(1) < r.shift(2)) & (r.shift(2) < r.shift(3))
c3 = r.shift(3) < 60.0
c4 = c > df["sma200"]
df["buy_signal"] = c1 & c2 & c3 & c4
df["exit_signal"] = r > 50.0

trades = []
in_pos = False
entry_px = 0
entry_date = None
days = 0

for idx, row in df.iterrows():
    if in_pos:
        days += 1
        if row["exit_signal"] and days >= 1:
            exit_px = row["close"]
            pnl_pct = (exit_px - entry_px) / entry_px * 100.0
            trades.append({
                "entry_date": entry_date,
                "exit_date": row["timestamp"],
                "days": days,
                "entry_px": entry_px,
                "exit_px": exit_px,
                "pnl_pct": pnl_pct
            })
            in_pos = False
    else:
        if row["buy_signal"]:
            in_pos = True
            entry_px = row["close"]
            entry_date = row["timestamp"]
            days = 0

tdf = pd.DataFrame(trades)
wins = tdf[tdf["pnl_pct"] > 0]
losses = tdf[tdf["pnl_pct"] <= 0]
pf = (wins["pnl_pct"].sum() / abs(losses["pnl_pct"].sum())) if len(losses) > 0 else 999.0

print("\n" + "=" * 75)
print("TRIPLE RSI ON NIFTY INDEX (ETF / INDEX FUTURES)")
print("=" * 75)
print(f"Total Trades: {len(tdf)}")
print(f"Wins: {len(wins)} ({len(wins)/len(tdf)*100:.1f}%)")
print(f"Losses: {len(losses)} ({len(losses)/len(tdf)*100:.1f}%)")
print(f"Win Rate: {len(wins)/len(tdf)*100:.1f}%")
print(f"Profit Factor: {pf:.2f}")
print(f"Average Win: +{wins['pnl_pct'].mean():.2f}%")
print(f"Average Loss: {losses['pnl_pct'].mean():.2f}%")
print(f"Max Loss: {losses['pnl_pct'].min():.2f}%")
print(f"Avg Holding Days: {tdf['days'].mean():.1f} days")
print("=" * 75)
