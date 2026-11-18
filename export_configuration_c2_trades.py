"""
export_configuration_c2_trades.py
=============================================================================
Exports full trade ledger for Configuration C2:
- Strategy 1 (AI Renko Supertrend)
- 10 Concurrent Slots (10% Dynamic Allocation per trade)
- ₹5,000/Month SIP Capital Injection
- Universe: All 498 Nifty 500 Stocks (2020-2026, 4H Timeframe)
- Zerodha Indian Equity Delivery Friction Model
=============================================================================
"""

import sys
import os
import pickle
import pandas as pd
import numpy as np

from backtester import EngineMechanics

CACHE_FILE = "nifty500_renko_cache_2020.pkl"

def export_c2_trades():
    if not os.path.exists(CACHE_FILE):
        print(f"Error: {CACHE_FILE} not found. Run simulate_strategy1_sip_and_unrestricted.py first.")
        return

    print(f"Loading cached Renko bricks from {CACHE_FILE}...")
    with open(CACHE_FILE, "rb") as f:
        stock_renko, all_ts = pickle.load(f)

    initial_cap = 100000.0
    monthly_sip = 5000.0
    max_positions = 10
    alloc_pct = 0.10

    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_positions)
    available_cash = float(initial_cap)
    total_invested = float(initial_cap)
    open_positions = {}
    closed_trades = []
    equity_curve = []
    last_month = None

    print(f"Simulating Configuration C2 across {len(all_ts)} timestamps...")

    for ts in all_ts:
        # 1. Process Monthly SIP injection
        cur_m = (ts.year, ts.month)
        if last_month is not None and cur_m != last_month and monthly_sip > 0:
            available_cash += monthly_sip
            total_invested += monthly_sip
        last_month = cur_m

        # 2. Mark-to-market Equity
        curr_eq = available_cash + sum(
            p["qty"] * stock_renko[s][ts]["close"] if ts in stock_renko[s] else p["qty"] * p["entry_px"]
            for s, p in open_positions.items()
        )
        equity_curve.append(curr_eq)

        # 3. Process EXITS
        to_close = []
        for s, p in open_positions.items():
            if ts not in stock_renko[s]:
                continue
            b = stock_renko[s][ts]
            close = b["close"]
            high = b["high"]
            low = b["low"]
            entry = p["entry_px"]
            b_size = p["brick_size"]

            # Increment holding bars
            p["bars_held"] += 1

            # 1:1 Break-Even Ratchet: at +1 brick profit, lock SL to cost
            if not p["be_locked"] and high >= entry + b_size:
                p["be_locked"] = True
                p["sl"] = entry * 1.002  # Covers friction

            exit_px = None
            reason = None

            if low <= p["sl"]:
                exit_px = p["sl"]
                reason = "Break_Even_SL" if p["be_locked"] else "Initial_Brick_SL"
            elif b["signal"] == -1:
                exit_px = close
                reason = "Supertrend_Reversal"

            if exit_px:
                gross_b = p["qty"] * entry
                gross_s = p["qty"] * exit_px
                fric = engine.calculate_friction(gross_b, True) + engine.calculate_friction(gross_s, False)
                net = (gross_s - gross_b) - fric
                available_cash += (gross_s - engine.calculate_friction(gross_s, False))
                
                # Determine category
                if reason == "Break_Even_SL":
                    category = "Break-Even (Protected)"
                elif net > 0:
                    category = "Win"
                else:
                    category = "Loss"

                holding_days = round(p["bars_held"] / 2.0, 1)  # 2 bars of 4H per trading day

                closed_trades.append({
                    "Trade_No": len(closed_trades) + 1,
                    "Ticker": s,
                    "Entry_Date": str(p["entry_time"]),
                    "Exit_Date": str(ts),
                    "Holding_Bars_4H": p["bars_held"],
                    "Holding_Days_Est": holding_days,
                    "Entry_Price": round(entry, 2),
                    "Exit_Price": round(exit_px, 2),
                    "Quantity": p["qty"],
                    "Capital_Invested": round(gross_b, 2),
                    "Gross_Exit_Value": round(gross_s, 2),
                    "Gross_PnL": round(gross_s - gross_b, 2),
                    "Zerodha_Friction_Taxes": round(fric, 2),
                    "Net_Realized_PnL": round(net, 2),
                    "Return_Pct": round(((exit_px - entry) / entry) * 100.0, 2),
                    "Exit_Reason": reason,
                    "Category": category,
                    "Portfolio_Equity_After_Trade": round(available_cash + sum(
                        op["qty"] * stock_renko[sym][ts]["close"] if ts in stock_renko[sym] else op["qty"] * op["entry_px"]
                        for sym, op in open_positions.items() if sym != s
                    ), 2),
                    "Year": ts.year,
                    "Month": f"{ts.year}-{ts.month:02d}"
                })
                to_close.append(s)

        for s in to_close:
            del open_positions[s]

        # 4. Process ENTRIES
        can_open = len(open_positions) < max_positions and available_cash >= 5000.0
        if can_open:
            for s, series in stock_renko.items():
                if len(open_positions) >= max_positions:
                    break
                if s in open_positions or ts not in series:
                    continue
                b = series[ts]
                if b["signal"] == 1:
                    target_alloc = min(curr_eq * alloc_pct, available_cash)
                    if target_alloc < 5000.0:
                        continue
                    entry_px = b["close"] * 1.002
                    qty = int(target_alloc // entry_px)
                    if qty <= 0:
                        continue
                    gross_b = qty * entry_px
                    fric = engine.calculate_friction(gross_b, True)
                    if available_cash >= (gross_b + fric):
                        available_cash -= (gross_b + fric)
                        b_sz = b["brick_size"]
                        open_positions[s] = {
                            "entry_time": ts,
                            "entry_px": entry_px,
                            "qty": qty,
                            "brick_size": b_sz,
                            "sl": entry_px - b_sz,
                            "be_locked": False,
                            "bars_held": 0
                        }

    df_trades = pd.DataFrame(closed_trades)
    csv_file = "trades_strategy1_configuration_c2.csv"
    df_trades.to_csv(csv_file, index=False)

    print("\n" + "=" * 80)
    print("CONFIGURATION C2 TRADE EXPORT COMPLETED SUCCESSFULLY")
    print("=" * 80)
    print(f"  • Total Trades Logged:       {len(df_trades):,}")
    print(f"  • File Saved:                {csv_file}")
    print(f"  • File Size:                 {os.path.getsize(csv_file) / 1024:.1f} KB")

    wins = df_trades[df_trades["Net_Realized_PnL"] > 0]
    losses = df_trades[df_trades["Net_Realized_PnL"] <= 0]
    be_saves = df_trades[df_trades["Exit_Reason"] == "Break_Even_SL"]
    
    print(f"\nBreakdown:")
    print(f"  • Total Wins:                {len(wins):,} ({len(wins)/len(df_trades)*100:.1f}%)")
    print(f"  • Total Losses:              {len(losses):,} ({len(losses)/len(df_trades)*100:.1f}%)")
    print(f"  • Break-Even Protected:      {len(be_saves):,} ({len(be_saves)/len(df_trades)*100:.1f}%) - ₹0 Loss Saves!")
    print(f"  • Gross Wins Total:          ₹{wins['Net_Realized_PnL'].sum():,.2f}")
    print(f"  • Gross Losses Total:        ₹{abs(losses['Net_Realized_PnL'].sum()):,.2f}")
    print(f"  • Profit Factor:             {wins['Net_Realized_PnL'].sum() / abs(losses['Net_Realized_PnL'].sum()):.2f}")
    print(f"  • Total Friction & Taxes:    ₹{df_trades['Zerodha_Friction_Taxes'].sum():,.2f}")
    print(f"  • Final Capital:             ₹{equity_curve[-1]:,.2f}")

    print("\nTop 10 Biggest Winner Trades:")
    top_winners = df_trades.sort_values(by="Net_Realized_PnL", ascending=False).head(10)
    for _, r in top_winners.iterrows():
        print(f"  #{r['Trade_No']:>4} | {r['Ticker']:<14} | {r['Entry_Date'][:10]} -> {r['Exit_Date'][:10]} | Held: {r['Holding_Days_Est']:>4.1f}d | Gain: {r['Return_Pct']:>+6.1f}% | Net PnL: ₹{r['Net_Realized_PnL']:>+12,.2f}")


if __name__ == "__main__":
    export_c2_trades()
