import pandas as pd

df = pd.read_csv("trades_indian_etfs_triple_rsi.csv")
print("=" * 80)
print("TRIPLE RSI BREAKDOWN BY INDIAN ETF")
print("=" * 80)

grouped = df.groupby("ticker")
summary = []

for ticker, group in grouped:
    wins = group[group["pnl_pct"] > 0]
    losses = group[group["pnl_pct"] <= 0]
    total = len(group)
    wr = len(wins) / total * 100
    gross_w = wins["pnl_pct"].sum()
    gross_l = abs(losses["pnl_pct"].sum())
    pf = (gross_w / gross_l) if gross_l > 0 else 999.0
    cum_ret = group["pnl_pct"].sum()
    
    summary.append({
        "Ticker": ticker,
        "Trades": total,
        "Win Rate %": round(wr, 1),
        "Profit Factor": round(pf, 2),
        "Avg Win %": round(wins["pnl_pct"].mean(), 2) if len(wins) > 0 else 0,
        "Avg Loss %": round(losses["pnl_pct"].mean(), 2) if len(losses) > 0 else 0,
        "Max Loss %": round(group["pnl_pct"].min(), 2),
        "Total Return %": round(cum_ret, 1)
    })

sdf = pd.DataFrame(summary).sort_values(by="Profit Factor", ascending=False)
print(sdf.to_string(index=False))
