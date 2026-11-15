import pandas as pd
import numpy as np

df = pd.read_csv('export_check.csv')
check_trades = df[df['Comments'].astype(str).str.strip().str.lower() == 'check'].copy()

check_trades['run_up'] = check_trades['Max Run-Up %'].str.rstrip('%').astype(float)
check_trades['pnl'] = check_trades['PnL %'].str.rstrip('%').astype(float)
check_trades['delta'] = check_trades['Deltha between Max run up and and PnL in %'].str.rstrip('%').astype(float)
check_trades['holding_days'] = check_trades['Holding'].str.replace(' days', '').astype(float)

# Sort by delta (profit given back)
check_trades = check_trades.sort_values(by='delta', ascending=False)

print("=== TOP 10 WORST PROFIT GIVEBACKS AMONG CHECK TRADES ===")
top10 = check_trades[['Trade ID', 'Ticker', 'Entry Date', 'Exit Date', 'run_up', 'pnl', 'delta', 'holding_days', 'Exit Reason']].head(10)
print(top10.to_string(index=False))

print("\n=== DISTRIBUTION OF MAX RUN-UP FOR THE 45 CHECK TRADES ===")
print("10% - 12% Run-up:", len(check_trades[(check_trades['run_up'] >= 10.0) & (check_trades['run_up'] < 12.0)]))
print("12% - 15% Run-up:", len(check_trades[(check_trades['run_up'] >= 12.0) & (check_trades['run_up'] < 15.0)]))
print("15% - 20% Run-up:", len(check_trades[(check_trades['run_up'] >= 15.0) & (check_trades['run_up'] < 20.0)]))
print("20%+ Run-up:     ", len(check_trades[check_trades['run_up'] >= 20.0]))

print("\n=== DISTRIBUTION OF FINAL PNL FOR THE 45 CHECK TRADES ===")
print("Negative PnL (< 0%): ", len(check_trades[check_trades['pnl'] < 0]))
print("0% to 2% PnL:        ", len(check_trades[(check_trades['pnl'] >= 0) & (check_trades['pnl'] < 2.0)]))
print("2% to 4% PnL:        ", len(check_trades[(check_trades['pnl'] >= 2.0) & (check_trades['pnl'] < 4.0)]))
print("4%+ PnL:             ", len(check_trades[check_trades['pnl'] >= 4.0]))

print("\n=== AVERAGE HOLDING PERIOD ===")
print(f"Average holding time: {check_trades['holding_days'].mean():.1f} days")
print(f"Min holding: {check_trades['holding_days'].min():.1f} days | Max holding: {check_trades['holding_days'].max():.1f} days")

