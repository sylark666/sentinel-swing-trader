import sqlite3
import pandas as pd
from golden_cross_4h_optimizer import load_4h_universe, simulate_4h_golden_cross

with sqlite3.connect('market_data.db') as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT ticker FROM market_data WHERE ticker != 'NIFTY' ORDER BY ticker ASC")
    all_syms = [r[0] for r in cursor.fetchall()]

stock_dfs, all_ts = load_4h_universe(start_date="2018-01-01", symbols=all_syms, timeframe="4H")

exits = ["CHANDELIER", "HYBRID_TRAIL", "EMA50_CROSS", "SUPERTREND"]
results = []

for ex in exits:
    res = simulate_4h_golden_cross(
        stock_dfs=stock_dfs,
        all_timestamps=all_ts,
        initial_cap=100000.0,
        max_alloc_pct=0.30,
        min_alloc_pct=0.15,
        max_positions=3,
        entry_trigger="SWING_GC",
        exit_model=ex,
        initial_sl_atr=2.3,
        entry_filter_gate="ACTIVE_VOLUME",
        monthly_sip=5000.0,
        timeframe="4H"
    )
    tdf = res['trades_df']
    # Filter for trades that had >= 10% run-up
    r10 = tdf[tdf['max_runup_pct'] >= 10.0]
    
    results.append({
        "Exit Model": ex,
        "Final Equity": f"₹{res['final_equity']:,.2f}",
        "Net Profit": f"₹{res['total_net_pnl']:,.2f}",
        "Win Rate": f"{res['win_rate']:.1f}%",
        "PF": f"{res['profit_factor']:.2f}",
        "Max DD": f"{res['max_drawdown_pct']:.1f}%",
        "Trades": len(tdf),
        "Avg PnL on +10% Runners": f"{r10['pnl_pct'].mean():.2f}%",
        "+10% Ending Negative": len(r10[r10['pnl_pct'] < 0])
    })

print(pd.DataFrame(results).to_string(index=False))

