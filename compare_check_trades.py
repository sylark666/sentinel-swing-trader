import pandas as pd
import numpy as np

# Load user export (3.0 ATR baseline)
exp = pd.read_csv('export_check.csv')
exp['entry_dt'] = pd.to_datetime(exp['Entry Date'], format='%d-%m-%Y %H:%M')
exp_check = exp[exp['Comments'].astype(str).str.strip().str.lower() == 'check'].copy()
exp_check['run_up'] = exp_check['Max Run-Up %'].str.rstrip('%').astype(float)
exp_check['pnl_30'] = exp_check['PnL %'].str.rstrip('%').astype(float)

# Load 2.3 ATR recalculation
t23 = pd.read_csv('trades_chandelier_2_3_recalc.csv')
t23['entry_dt'] = pd.to_datetime(t23['entry_date'])

matches = []
for idx, r in exp_check.iterrows():
    sym = r['Ticker']
    dt = r['entry_dt']
    
    # Exact or near match (+- 3 days)
    m = t23[(t23['ticker'] == sym) & (abs((t23['entry_dt'] - dt).dt.total_seconds()) <= 86400 * 3)]
    if not m.empty:
        best_m = m.iloc[0]
        matches.append({
            'Ticker': sym.replace('.NS', ''),
            'Entry Date': str(dt)[:10],
            'RunUp %': r['run_up'],
            '3.0 ATR PnL %': r['pnl_30'],
            '2.3 ATR PnL %': round(best_m['pnl_pct'], 2),
            'Delta %': round(best_m['pnl_pct'] - r['pnl_30'], 2),
            '2.3 Exit Reason': best_m['reason'],
            'Status': 'MATCHED'
        })
    else:
        matches.append({
            'Ticker': sym.replace('.NS', ''),
            'Entry Date': str(dt)[:10],
            'RunUp %': r['run_up'],
            '3.0 ATR PnL %': r['pnl_30'],
            '2.3 ATR PnL %': np.nan,
            'Delta %': np.nan,
            '2.3 Exit Reason': 'Different cash/slot timing',
            'Status': 'SKIPPED'
        })

df_match = pd.DataFrame(matches)
print("=" * 85)
print("DETAILED TRADE-BY-TRADE COMPARISON (45 'CHECK' TRADES)")
print("=" * 85)
print(df_match.to_string(index=False))

matched_sub = df_match[df_match['Status'] == 'MATCHED']
print("\n" + "=" * 85)
print("SUMMARY OF MATCHED TRADES")
print("=" * 85)
print(f"Total 'check' trades in user export: {len(df_match)}")
print(f"Matched directly in 2.3 ATR simulation: {len(matched_sub)}")
print(f"Skipped/Replaced by portfolio slot dynamics: {len(df_match) - len(matched_sub)}")
print(f"Average PnL under 3.0 ATR: {matched_sub['3.0 ATR PnL %'].mean():.2f}%")
print(f"Average PnL under 2.3 ATR: {matched_sub['2.3 ATR PnL %'].mean():.2f}%")
delta_avg = matched_sub['Delta %'].mean()
print(f"Average Net Improvement per trade: {delta_avg:+.2f}%")
print(f"Trades that Improved: {len(matched_sub[matched_sub['Delta %'] > 0])}")
print(f"Trades that Worsened: {len(matched_sub[matched_sub['Delta %'] < 0])}")
print(f"Negative Trades under 3.0 ATR: {len(matched_sub[matched_sub['3.0 ATR PnL %'] < 0])}")
print(f"Negative Trades under 2.3 ATR: {len(matched_sub[matched_sub['2.3 ATR PnL %'] < 0])}")

# Let's also inspect all trades in 2.3 ATR that had >= 10% run-up
r10_23 = t23[t23['max_runup_pct'] >= 10.0]
print("\n" + "=" * 85)
print("ALL RUNNERS (>=10% RUN-UP) IN 2.3 ATR SIMULATION")
print("=" * 85)
print(f"Count: {len(r10_23)}")
print(f"Average Max Run-Up: {r10_23['max_runup_pct'].mean():.2f}%")
print(f"Average Realized PnL: {r10_23['pnl_pct'].mean():.2f}%")
print(f"Profitable runners: {len(r10_23[r10_23['pnl_pct'] > 0])} ({len(r10_23[r10_23['pnl_pct'] > 0])/len(r10_23)*100:.1f}%)")
print(f"Negative runners (given back all gains): {len(r10_23[r10_23['pnl_pct'] <= 0])} ({len(r10_23[r10_23['pnl_pct'] <= 0])/len(r10_23)*100:.1f}%)")

