import pandas as pd
import numpy as np

df = pd.read_csv('export_check.csv')
print(f"Total trades in export: {len(df)}")

# Find trades with 'check' in Comments
check_trades = df[df['Comments'].astype(str).str.strip().str.lower() == 'check'].copy()
print(f"Total trades marked 'check': {len(check_trades)}")

# Clean numeric columns
check_trades['run_up'] = check_trades['Max Run-Up %'].str.rstrip('%').astype(float)
check_trades['pnl'] = check_trades['PnL %'].str.rstrip('%').astype(float)
check_trades['delta'] = check_trades['Deltha between Max run up and and PnL in %'].str.rstrip('%').astype(float)

print(f"\n--- SUMMARY OF CHECK TRADES ---")
print(f"Average Max Run-Up % : {check_trades['run_up'].mean():.2f}%")
print(f"Average Final PnL %  : {check_trades['pnl'].mean():.2f}%")
print(f"Average Profit Lost  : {check_trades['delta'].mean():.2f}%")

print(f"\nBreakdown by Exit Reason:")
print(check_trades.groupby('Exit Reason').agg(
    count=('Trade ID', 'count'),
    avg_run_up=('run_up', 'mean'),
    avg_pnl=('pnl', 'mean'),
    avg_delta=('delta', 'mean')
))

cols = ['Trade ID', 'Ticker', 'Entry Date', 'Exit Date', 'run_up', 'pnl', 'delta', 'Holding', 'Exit Reason']
print("\n--- ALL CHECK TRADES ---")
print(check_trades[cols].to_string(index=False))

# Let's also check all trades in the entire dataset that had run_up >= 10%
df['run_up'] = df['Max Run-Up %'].str.rstrip('%').astype(float)
df['pnl'] = df['PnL %'].str.rstrip('%').astype(float)
df['delta'] = df['Deltha between Max run up and and PnL in %'].str.rstrip('%').astype(float)

high_runup = df[df['run_up'] >= 10.0].copy()
print(f"\n--- OVERALL DATASET TRADES WITH RUN-UP >= 10% ---")
print(f"Count: {len(high_runup)} out of {len(df)} total trades ({len(high_runup)/len(df)*100:.1f}%)")
print(f"Average Run-Up: {high_runup['run_up'].mean():.2f}%")
print(f"Average Final PnL: {high_runup['pnl'].mean():.2f}%")
print(f"Count where final PnL was <= 5%: {len(high_runup[high_runup['pnl'] <= 5.0])}")
print(f"Count where final PnL was negative: {len(high_runup[high_runup['pnl'] < 0.0])}")

