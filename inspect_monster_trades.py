import pandas as pd
import numpy as np

# Let's inspect which specific trades gained vs lost between BASELINE and BREAK_EVEN_AT_10
from test_exit_improvements import dfs

b_df = dfs['BASELINE'].copy()
be_df = dfs['BREAK_EVEN_AT_10'].copy()

# Look at monster winners in BASELINE (net_pnl > 100,000)
monster_baseline = b_df[b_df['pnl_pct'] >= 30.0].sort_values(by='net_pnl', ascending=False)
print("--- TOP 15 MONSTER WINNERS IN BASELINE ---")
print(monster_baseline[['ticker', 'entry_date', 'max_runup_pct', 'pnl_pct', 'net_pnl', 'days_held']].head(15).to_string(index=False))

# Now let's see what happened to those exact same entries in BREAK_EVEN_AT_10
print("\n--- WHAT HAPPENED TO THOSE MONSTER WINNERS IN BREAK_EVEN_AT_10? ---")
for idx, row in monster_baseline.head(10).iterrows():
    ticker = row['ticker']
    entry_date = row['entry_date']
    matched = be_df[(be_df['ticker'] == ticker) & (be_df['entry_date'] == entry_date)]
    if not matched.empty:
        m = matched.iloc[0]
        print(f"{ticker} ({entry_date[:10]}): Baseline PnL = +{row['pnl_pct']:.1f}% (₹{row['net_pnl']:,.0f}) | BE PnL = {m['pnl_pct']:.1f}% (₹{m['net_pnl']:,.0f}) | Exit: {m['exit_reason']}")
    else:
        print(f"{ticker} ({entry_date[:10]}): Not matched or entry changed due to portfolio cash state")

