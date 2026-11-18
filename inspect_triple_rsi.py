import pandas as pd
import numpy as np

df = pd.read_csv("trades_triple_rsi_close.csv")
print(f"Total Trades: {len(df)}")
wins = df[df["net_pnl"] > 0]
losses = df[df["net_pnl"] <= 0]

print(f"Wins: {len(wins)} ({len(wins)/len(df)*100:.1f}%)")
print(f"Losses: {len(losses)} ({len(losses)/len(df)*100:.1f}%)")
print(f"Avg Win PnL %: +{wins['pnl_pct'].mean():.2f}%")
print(f"Avg Loss PnL %: {losses['pnl_pct'].mean():.2f}%")
print(f"Max Win PnL %: +{wins['pnl_pct'].max():.2f}%")
print(f"Max Loss PnL %: {losses['pnl_pct'].min():.2f}%")
print(f"Total Friction Paid: ₹{df['fric'].sum():,.2f}")
print("\nWorst 5 Trades:")
print(losses.sort_values(by="net_pnl").head(5)[["symbol", "entry_date", "exit_date", "days_held", "pnl_pct", "net_pnl"]])
