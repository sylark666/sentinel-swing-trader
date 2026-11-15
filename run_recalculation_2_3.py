import sqlite3
import pandas as pd
import numpy as np
from golden_cross_4h_optimizer import load_4h_universe, simulate_4h_golden_cross

# Load all symbols
with sqlite3.connect('market_data.db') as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT ticker FROM market_data WHERE ticker != 'NIFTY' ORDER BY ticker ASC")
    all_syms = [r[0] for r in cursor.fetchall()]

# 1. Run simulation with Chandelier 2.3 ATR
stock_dfs_23, all_ts_23 = load_4h_universe(start_date="2018-01-01", symbols=all_syms, timeframe="4H", chandelier_mult=2.3)

res_23 = simulate_4h_golden_cross(
    stock_dfs=stock_dfs_23,
    all_timestamps=all_ts_23,
    initial_cap=100000.0,
    max_alloc_pct=0.30,
    min_alloc_pct=0.15,
    max_positions=3,
    entry_trigger="SWING_GC",
    exit_model="CHANDELIER",
    initial_sl_atr=2.3,
    entry_filter_gate="ACTIVE_VOLUME",
    monthly_sip=5000.0,
    timeframe="4H"
)

# 2. Run simulation with Chandelier 3.0 ATR (Baseline)
stock_dfs_30, all_ts_30 = load_4h_universe(start_date="2018-01-01", symbols=all_syms, timeframe="4H", chandelier_mult=3.0)

res_30 = simulate_4h_golden_cross(
    stock_dfs=stock_dfs_30,
    all_timestamps=all_ts_30,
    initial_cap=100000.0,
    max_alloc_pct=0.30,
    min_alloc_pct=0.15,
    max_positions=3,
    entry_trigger="SWING_GC",
    exit_model="CHANDELIER",
    initial_sl_atr=2.3,
    entry_filter_gate="ACTIVE_VOLUME",
    monthly_sip=5000.0,
    timeframe="4H"
)

# Print high-level comparison
print("\n" + "="*85)
print("🏆 MACRO COMPARISON: 2.3 ATR CHANDELIER vs 3.0 ATR CHANDELIER")
print("="*85)
comp_df = pd.DataFrame([
    {
        "Chandelier Multiplier": "3.0 ATR (Baseline)",
        "Final Portfolio": f"₹{res_30['final_equity']:,.2f}",
        "Net Profit": f"₹{res_30['total_net_pnl']:,.2f}",
        "Win Rate": f"{res_30['win_rate']:.1f}%",
        "Profit Factor": f"{res_30['profit_factor']:.2f}",
        "Max Drawdown": f"{res_30['max_drawdown_pct']:.1f}%",
        "Total Trades": len(res_30['trades_df'])
    },
    {
        "Chandelier Multiplier": "2.3 ATR (Tighter Exit)",
        "Final Portfolio": f"₹{res_23['final_equity']:,.2f}",
        "Net Profit": f"₹{res_23['total_net_pnl']:,.2f}",
        "Win Rate": f"{res_23['win_rate']:.1f}%",
        "Profit Factor": f"{res_23['profit_factor']:.2f}",
        "Max Drawdown": f"{res_23['max_drawdown_pct']:.1f}%",
        "Total Trades": len(res_23['trades_df'])
    }
])
print(comp_df.to_string(index=False))

# Now let's analyze the 45 check trades in both versions
tdf_23 = res_23['trades_df'].copy()
tdf_30 = res_30['trades_df'].copy()

# Load the user's export_check.csv to get the exact 45 check trades
export_df = pd.read_csv('export_check.csv')
check_trades = export_df[export_df['Comments'].astype(str).str.strip().str.lower() == 'check'].copy()
check_trades['run_up'] = check_trades['Max Run-Up %'].str.rstrip('%').astype(float)
check_trades['pnl'] = check_trades['PnL %'].str.rstrip('%').astype(float)

print(f"\nTotal trades marked 'check': {len(check_trades)}")
print(f"Original Average Run-Up: {check_trades['run_up'].mean():.2f}%")
print(f"Original Average Final PnL in Export: {check_trades['pnl'].mean():.2f}%")

# Let's inspect how trades in 2.3 ATR compare on trades that reached >= 10% run-up
r10_30 = tdf_30[tdf_30['max_runup_pct'] >= 10.0]
r10_23 = tdf_23[tdf_23['max_runup_pct'] >= 10.0]

print("\n" + "="*85)
print("📊 IMPACT ON ALL TRADES WITH >= 10% RUN-UP")
print("="*85)
print(f"Baseline (3.0 ATR): {len(r10_30)} trades | Avg PnL = {r10_30['pnl_pct'].mean():.2f}% | Negative Trades = {len(r10_30[r10_30['pnl_pct'] < 0])}")
print(f"2.3 ATR Chandelier: {len(r10_23)} trades | Avg PnL = {r10_23['pnl_pct'].mean():.2f}% | Negative Trades = {len(r10_23[r10_23['pnl_pct'] < 0])}")

# Let's also check the top 10 monster winners in both
top_30 = tdf_30.sort_values(by='net_pnl', ascending=False).head(10)
top_23 = tdf_23.sort_values(by='net_pnl', ascending=False).head(10)

print("\n--- TOP 5 WINNERS IN 3.0 ATR CHANDELIER ---")
print(top_30[['ticker', 'entry_date', 'pnl_pct', 'net_pnl', 'days_held', 'reason']].head(5).to_string(index=False))

print("\n--- TOP 5 WINNERS IN 2.3 ATR CHANDELIER ---")
print(top_23[['ticker', 'entry_date', 'pnl_pct', 'net_pnl', 'days_held', 'reason']].head(5).to_string(index=False))

# Now let's do a deep dive comparison of the 45 check trades:
print("\n" + "="*85)
print("🔍 EXACT OUTCOME OF THE 45 'CHECK' TRADES: 2.3 ATR vs 3.0 ATR CHANDELIER")
print("="*85)

check_results = []
for idx, row in check_trades.iterrows():
    ticker = row['Ticker'].replace(".NS", "")
    full_ticker = f"{ticker}.NS"
    entry_d = row['Entry Date']
    
    # Match in 3.0 ATR trades_df
    # In trades_df, ticker is clean or has .NS, let's match both
    match_30 = tdf_30[(tdf_30['ticker'].isin([ticker, full_ticker])) & (tdf_30['entry_date'].astype(str).str.startswith(entry_d[:10]))]
    match_23 = tdf_23[(tdf_23['ticker'].isin([ticker, full_ticker])) & (tdf_23['entry_date'].astype(str).str.startswith(entry_d[:10]))]

    pnl_orig = row['pnl']
    runup_orig = row['run_up']
    
    pnl_30 = match_30.iloc[0]['pnl_pct'] if not match_30.empty else pnl_orig
    pnl_23 = match_23.iloc[0]['pnl_pct'] if not match_23.empty else None
    reason_23 = match_23.iloc[0]['reason'] if not match_23.empty else "No entry/different timing"

    check_results.append({
        "Ticker": ticker,
        "Entry Date": entry_d[:10],
        "Max Run-Up %": runup_orig,
        "Original PnL % (3.0 ATR)": pnl_orig,
        "Recalc PnL % (2.3 ATR)": f"{pnl_23:.2f}%" if pnl_23 is not None else "N/A",
        "2.3 Exit Reason": reason_23
    })

res_check_df = pd.DataFrame(check_results)
print(res_check_df.to_string(index=False))

# Calculate summary stats for the matched check trades
valid_23 = [float(x.replace('%', '')) for x in res_check_df['Recalc PnL % (2.3 ATR)'] if x != 'N/A']
print(f"\nMatched {len(valid_23)} of 45 check trades under 2.3 ATR Chandelier:")
print(f"Average PnL under 3.0 ATR Chandelier (for these matched trades): {np.mean([r['Original PnL % (3.0 ATR)'] for i, r in res_check_df.iterrows() if r['Recalc PnL % (2.3 ATR)'] != 'N/A']):.2f}%")
print(f"Average PnL under 2.3 ATR Chandelier (for these matched trades): {np.mean(valid_23):.2f}%")
print(f"Trades improved: {sum(1 for i, r in res_check_df.iterrows() if r['Recalc PnL % (2.3 ATR)'] != 'N/A' and float(r['Recalc PnL % (2.3 ATR)'].replace('%','')) > r['Original PnL % (3.0 ATR)'])}")
print(f"Trades worsened: {sum(1 for i, r in res_check_df.iterrows() if r['Recalc PnL % (2.3 ATR)'] != 'N/A' and float(r['Recalc PnL % (2.3 ATR)'].replace('%','')) < r['Original PnL % (3.0 ATR)'])}")

# Save full results for user review
tdf_23.to_csv("trades_chandelier_2_3_recalc.csv", index=False)
