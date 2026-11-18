"""
test_4h_delayed_execution.py
"""
import sys
sys.path.insert(0, r'c:\Users\Advait\Desktop\Auto Swing Trader')
import sqlite3
import pickle
import pandas as pd
import numpy as np
from backtester import EngineMechanics

# Load 4H Renko cache
with open("nifty500_renko_cache_2020.pkl", "rb") as f:
    renko_4h, ts_4h = pickle.load(f)

print(f"Loaded {len(renko_4h)} stocks across {len(ts_4h)} 4H timestamps.")

# In 4H data, each day has two bars: ~09:15-13:15 and ~13:15-15:30.
# If data is 15 min late:
# For bar ending at 13:15, data arrives at 13:30. Trader enters at 13:30 (approx bar close + slippage).
# For bar ending at 15:30, data arrives at 15:45 (market closed!). Trader enters at next day 09:15 open!

# Let's test the 4H Precision strategy with:
# 1. 0.3% entry slippage (modeling entering 15 min late / next open)
# 2. Real-time GTT stop loss (Zerodha triggers SL real-time)

def run_4h_delayed(slippage_pct=0.003, max_pos=8, alloc_pct=0.125, monthly_sip=5000.0, initial_cap=100000.0):
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_pos)
    cash = float(initial_cap)
    total_invested = float(initial_cap)
    positions = {}
    trades = []
    equity_curve = []
    last_month = None

    for ts in ts_4h:
        cur_m = (ts.year, ts.month)
        if last_month is not None and cur_m != last_month and monthly_sip > 0:
            cash += monthly_sip
            total_invested += monthly_sip
        last_month = cur_m

        curr_eq = cash + sum(
            p["qty"] * renko_4h[s][ts]["close"] if ts in renko_4h[s] else p["qty"] * p["entry_px"]
            for s, p in positions.items()
        )
        equity_curve.append(curr_eq)

        # Exits
        to_close = []
        for s, p in positions.items():
            if ts not in renko_4h[s]: continue
            b = renko_4h[s][ts]
            close = b["close"]
            high = b["high"]
            low = b["low"]
            entry = p["entry_px"]
            b_size = p["brick_size"]
            p["days_held"] += 1

            if not p["be_locked"] and high >= entry + (2.0 * b_size):
                p["be_locked"] = True
                p["sl"] = entry * 1.002

            exit_px = None
            reason = None

            # Zerodha GTT triggers real-time on exchange: ZERO delay on SL
            if low <= p["sl"]:
                exit_px = p["sl"]
                reason = "Break_Even_SL" if p["be_locked"] else "Initial_Brick_SL"
            elif b["signal"] == -1:
                # 15 min delay on reversal exit -> 0.3% exit slippage
                exit_px = close * (1.0 - slippage_pct)
                reason = "Supertrend_Reversal"

            if exit_px:
                gross_b = p["qty"] * entry
                gross_s = p["qty"] * exit_px
                fric = engine.calculate_friction(gross_b, True) + engine.calculate_friction(gross_s, False)
                net = (gross_s - gross_b) - fric
                cash += (gross_s - engine.calculate_friction(gross_s, False))
                trades.append({
                    "symbol": s,
                    "entry_date": p["entry_date"],
                    "exit_date": ts,
                    "days_held": p["days_held"],
                    "entry_px": entry,
                    "exit_px": exit_px,
                    "quantity": p["qty"],
                    "capital_invested": gross_b,
                    "net_pnl": net,
                    "pnl_pct": ((exit_px - entry) / entry) * 100.0,
                    "reason": reason,
                    "fric": fric
                })
                to_close.append(s)

        for s in to_close: del positions[s]

        # Entries
        can_open = len(positions) < max_pos and cash >= 5000.0
        if can_open:
            candidates = []
            for s, series in renko_4h.items():
                if len(positions) >= max_pos: break
                if s in positions or ts not in series: continue
                b = series[ts]
                if b["signal"] == 1:
                    candidates.append((s, b["close"], b["brick_size"]))

            if candidates:
                for s, c, b_sz in candidates:
                    if len(positions) >= max_pos or cash < 5000.0: break
                    target_alloc = min(curr_eq * alloc_pct, cash)
                    if target_alloc < 5000.0: continue

                    # 15-minute delay: Enter with extra slippage (e.g. 0.3% to 0.5%)
                    entry_px = c * (1.0 + slippage_pct)
                    qty = int(target_alloc // entry_px)
                    if qty <= 0: continue
                    gross_b = qty * entry_px
                    fric = engine.calculate_friction(gross_b, True)
                    if cash >= (gross_b + fric):
                        cash -= (gross_b + fric)
                        positions[s] = {
                            "entry_date": ts,
                            "entry_px": entry_px,
                            "qty": qty,
                            "brick_size": b_sz,
                            "sl": entry_px - b_sz,
                            "be_locked": False,
                            "days_held": 0
                        }

    final_eq = equity_curve[-1] if equity_curve else initial_cap
    tdf = pd.DataFrame(trades)
    wins = tdf[tdf["net_pnl"] > 0]
    gross_w = wins["net_pnl"].sum()
    gross_l = abs(tdf[tdf["net_pnl"] <= 0]["net_pnl"].sum())
    pf = (gross_w / gross_l) if gross_l > 0 else 999.0
    eq_s = pd.Series(equity_curve)
    peak = eq_s.cummax()
    dd_s = (eq_s - peak) / peak * 100.0
    max_dd = abs(float(dd_s.min()))
    years = 6.8
    trades_per_month = len(tdf) / (years * 12)

    return {
        "final_eq": final_eq,
        "net_profit": final_eq - total_invested,
        "win_rate": len(wins) / len(tdf) * 100 if len(tdf) > 0 else 0,
        "profit_factor": pf,
        "max_dd": max_dd,
        "trades": len(tdf),
        "trades_per_month": round(trades_per_month, 1),
        "tdf": tdf
    }

print("Running 4H Renko under varying delays / slippage...")
for slip in [0.002, 0.004, 0.006]:
    res = run_4h_delayed(slippage_pct=slip)
    print(f"Slippage {slip*100:.1f}%: Final Cap = ₹{res['final_eq']:>12,.0f} | Net = ₹{res['net_profit']:>12,.0f} | PF = {res['profit_factor']:.2f} | DD = {res['max_dd']:.1f}% | Trades/mo = {res['trades_per_month']}")
