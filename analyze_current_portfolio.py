import sqlite3
import pandas as pd
import numpy as np
import portfolio_manager as pm

pm.init_portfolio_db()
active = pm.get_active_trades()
ledger = pm.get_portfolio_summary()

print("=" * 80)
print("PORTFOLIO LEDGER SUMMARY:")
print("=" * 80)
print(ledger)

print("\n" + "=" * 80)
print(f"ACTIVE SWING POSITIONS ({len(active)} open slots):")
print("=" * 80)

with sqlite3.connect("market_data.db") as conn:
    for p in active:
        ticker = p['ticker']
        # Fetch latest 4H bar
        q = "SELECT timestamp, open, high, low, close, volume FROM market_data WHERE ticker = ? AND timeframe = '4H' ORDER BY timestamp DESC LIMIT 1"
        df = pd.read_sql_query(q, conn, params=(ticker,))
        
        # Fetch 1D bar
        q_d = "SELECT timestamp, open, high, low, close, volume FROM market_data WHERE ticker = ? AND timeframe = '1D' ORDER BY timestamp DESC LIMIT 1"
        df_d = pd.read_sql_query(q_d, conn, params=(ticker,))
        
        cmp_4h = df['close'].iloc[0] if not df.empty else p['entry_price']
        cmp_date = df['timestamp'].iloc[0] if not df.empty else "N/A"
        
        entry = p['entry_price']
        qty = p['qty']
        initial_stop = p['initial_stop']
        curr_stop = p['current_trailing_stop']
        invested = qty * entry
        curr_val = qty * cmp_4h
        unrealized_pnl = curr_val - invested
        unrealized_pct = (cmp_4h - entry) / entry * 100
        stop_dist_pct = (cmp_4h - curr_stop) / cmp_4h * 100
        
        print(f"\nTicker: {ticker}")
        print(f"  Entry Date: {p['entry_date']} | Entry CMP: ₹{entry:,.2f}")
        print(f"  Latest Data As Of: {cmp_date}")
        print(f"  Current CMP: ₹{cmp_4h:,.2f} | Quantity: {qty} shares")
        print(f"  Capital Invested: ₹{invested:,.2f} | Current Value: ₹{curr_val:,.2f}")
        print(f"  Unrealized PnL: {'+' if unrealized_pnl >= 0 else ''}₹{unrealized_pnl:,.2f} ({'+' if unrealized_pct >= 0 else ''}{unrealized_pct:.2f}%)")
        print(f"  Initial Hard Stop: ₹{initial_stop:,.2f} (-{((entry - initial_stop)/entry*100):.2f}% risk)")
        print(f"  Current Trailing Stop: ₹{curr_stop:,.2f} (Buffer to Stop: {stop_dist_pct:.2f}%)")
        print(f"  Chandelier Stage: {p['chandelier_stage']} | Alpha Score: {p['alpha_score']}")
        print(f"  Status: {p['status']}")
