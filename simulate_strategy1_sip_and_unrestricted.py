"""
simulate_strategy1_sip_and_unrestricted.py
=============================================================================
Simulates Strategy 1 (AI Renko Supertrend) under:
1. ₹5,000/Month SIP Injection vs Lump Sum Only (₹0 SIP)
2. Open Trade Restriction Sensitivity:
   - Max 3 Positions (30% allocation)
   - Max 5 Positions (20% allocation)
   - Max 10 Positions (10% allocation)
   - Max 20 Positions (5% allocation)
   - Truly Unrestricted (No slot limit, dynamic 10% sizing while cash >= ₹5,000)

Evaluated across:
- Full Nifty 500 Universe (498 stocks, 2020-2026)
- Initial Capital: ₹100,000
- Full Zerodha Delivery Friction Model
=============================================================================
"""

import sys
import os
import sqlite3
import pickle
import pandas as pd
import numpy as np
from datetime import datetime
from tqdm import tqdm

from backtester import EngineMechanics
from strategy_renko.renko_engine import RenkoEngine
from strategy_renko.supertrend import calculate_supertrend

CACHE_FILE = "nifty500_renko_cache.pkl"

def get_or_build_renko_data(start_year="2020"):
    cache_path = f"nifty500_renko_cache_{start_year}.pkl"
    if os.path.exists(cache_path):
        print(f"Loading cached Renko bricks from {cache_path}...")
        with open(cache_path, "rb") as f:
            return pickle.load(f)

    print(f"Building Renko bricks for full Nifty 500 universe from {start_year}...")
    with sqlite3.connect("market_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT ticker FROM market_data WHERE timeframe='4H' AND ticker NOT IN ('NIFTY.NS', '^NSEI') ORDER BY ticker ASC")
        all_tickers = [r[0] for r in cursor.fetchall()]

    stock_renko = {}
    all_timestamps = set()

    with sqlite3.connect("market_data.db") as conn:
        for ticker in tqdm(all_tickers, desc="Building Renko"):
            q = f"""
                SELECT timestamp, open, high, low, close, volume 
                FROM market_data 
                WHERE ticker = ? AND timeframe = '4H' AND timestamp >= '{start_year}-01-01'
                ORDER BY timestamp ASC
            """
            df = pd.read_sql_query(q, conn, params=(ticker,))
            if df.empty or len(df) < 120:
                continue

            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.sort_values(by="timestamp").reset_index(drop=True)

            avg_price = df["close"].iloc[0]
            brick_size = max(1.0, round(avg_price * 0.02, 2))  # 2.0% brick size

            re = RenkoEngine(brick_size=brick_size)
            bricks = re.build_bricks(df)
            if len(bricks) < 30:
                continue

            st = calculate_supertrend(bricks, period=10, multiplier=2.0)

            st_dict = {}
            for _, r in st.iterrows():
                all_timestamps.add(r["timestamp"])
                st_dict[r["timestamp"]] = {
                    "close": r["close"],
                    "high": r["high"],
                    "low": r["low"],
                    "signal": r["signal"],
                    "trend": r["trend"],
                    "brick_size": r["brick_size"]
                }
            stock_renko[ticker] = st_dict

    all_ts_sorted = sorted(list(all_timestamps))
    print(f"Built Renko bricks for {len(stock_renko)} stocks across {len(all_ts_sorted)} timestamps.")

    with open(cache_path, "wb") as f:
        pickle.dump((stock_renko, all_ts_sorted), f)

    return stock_renko, all_ts_sorted


def run_portfolio_simulation(stock_renko, all_ts, initial_cap=100000.0, monthly_sip=0.0, max_positions=3, alloc_pct=0.30, label=""):
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_positions)
    available_cash = float(initial_cap)
    total_invested = float(initial_cap)
    open_positions = {}
    closed_trades = []
    equity_curve = []
    monthly_injected_count = 0
    last_month = None

    for ts in all_ts:
        # 1. Process Monthly SIP injection on first bar of new month
        cur_m = (ts.year, ts.month)
        if last_month is not None and cur_m != last_month and monthly_sip > 0:
            available_cash += monthly_sip
            total_invested += monthly_sip
            monthly_injected_count += 1
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

            # 1:1 Break-Even Ratchet: at +1 brick profit, lock SL to cost
            if not p["be_locked"] and high >= entry + b_size:
                p["be_locked"] = True
                p["sl"] = entry * 1.002  # Cover friction

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
                closed_trades.append({
                    "symbol": s,
                    "entry_time": p["entry_time"],
                    "exit_time": ts,
                    "entry_px": entry,
                    "exit_px": exit_px,
                    "net_pnl": net,
                    "pnl_pct": ((exit_px - entry) / entry) * 100.0,
                    "reason": reason
                })
                to_close.append(s)

        for s in to_close:
            del open_positions[s]

        # 4. Process ENTRIES
        # If max_positions is high or 999 (unrestricted), allocate alloc_pct of portfolio equity
        # Minimum trade size = ₹5,000
        can_open = (max_positions == 999 or len(open_positions) < max_positions) and available_cash >= 5000.0
        if can_open:
            for s, series in stock_renko.items():
                if max_positions != 999 and len(open_positions) >= max_positions:
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
                            "be_locked": False
                        }

    # Final Metrics
    final_eq = equity_curve[-1] if equity_curve else available_cash
    net_profit = final_eq - total_invested
    roi_pct = (net_profit / total_invested) * 100.0

    eq_s = pd.Series(equity_curve)
    peak = eq_s.cummax()
    dd_s = (eq_s - peak) / peak * 100.0
    max_dd = abs(float(dd_s.min())) if len(dd_s) > 0 else 0.0

    tdf = pd.DataFrame(closed_trades)
    n = len(tdf)
    if n == 0:
        return {
            "label": label, "max_pos": max_positions, "sip": monthly_sip,
            "invested": total_invested, "final_eq": final_eq, "net_profit": 0,
            "roi_pct": 0, "cagr": 0, "win_rate": 0, "pf": 0, "max_dd": 0,
            "trades": 0, "reasons": {}
        }

    wins = tdf[tdf["net_pnl"] > 0]
    losses = tdf[tdf["net_pnl"] <= 0]
    win_rate = (len(wins) / n) * 100.0
    gross_w = wins["net_pnl"].sum()
    gross_l = abs(losses["net_pnl"].sum())
    pf = (gross_w / gross_l) if gross_l > 0 else 999.0

    years = 6.8  # 2020 to late 2026
    cagr = ((final_eq / total_invested) ** (1.0 / years) - 1.0) * 100.0 if final_eq > total_invested else 0.0
    reasons = tdf["reason"].value_counts().to_dict()

    return {
        "label": label,
        "max_pos": max_positions,
        "sip": monthly_sip,
        "invested": total_invested,
        "final_eq": final_eq,
        "net_profit": net_profit,
        "roi_pct": roi_pct,
        "cagr": cagr,
        "win_rate": win_rate,
        "profit_factor": pf,
        "max_dd": max_dd,
        "trades": n,
        "reasons": reasons
    }


def main():
    stock_renko, all_ts = get_or_build_renko_data(start_year="2020")

    experiments = [
        # (Label, max_positions, alloc_pct, monthly_sip)
        ("A1: 3 Slots (30% alloc) - NO SIP (Current Baseline)", 3, 0.30, 0.0),
        ("A2: 3 Slots (30% alloc) + ₹5,000/mo SIP Injection", 3, 0.30, 5000.0),

        ("B1: 5 Slots (20% alloc) - NO SIP", 5, 0.20, 0.0),
        ("B2: 5 Slots (20% alloc) + ₹5,000/mo SIP Injection", 5, 0.20, 5000.0),

        ("C1: 10 Slots (10% alloc) - NO SIP", 10, 0.10, 0.0),
        ("C2: 10 Slots (10% alloc) + ₹5,000/mo SIP Injection", 10, 0.10, 5000.0),

        ("D1: 20 Slots (5% alloc) - NO SIP", 20, 0.05, 0.0),
        ("D2: 20 Slots (5% alloc) + ₹5,000/mo SIP Injection", 20, 0.05, 5000.0),

        ("E1: Unrestricted Slots (Dynamic 10% alloc) - NO SIP", 999, 0.10, 0.0),
        ("E2: Unrestricted Slots (Dynamic 10% alloc) + ₹5,000/mo SIP", 999, 0.10, 5000.0),
        ("E3: Unrestricted Slots (Dynamic 5% alloc) + ₹5,000/mo SIP", 999, 0.05, 5000.0),
    ]

    print(f"\nRunning {len(experiments)} Portfolio Architectures across {len(stock_renko)} stocks...")
    results = []

    for label, max_pos, alloc, sip in experiments:
        res = run_portfolio_simulation(stock_renko, all_ts, initial_cap=100000.0, monthly_sip=sip, max_positions=max_pos, alloc_pct=alloc, label=label)
        results.append(res)

    print("\n" + "=" * 135)
    print(f"{'Portfolio Setup':<54} | {'Total Inv.':<11} | {'Final Capital':<15} | {'Net Profit':<15} | {'ROI %':<8} | {'Win%':<6} | {'PF':<5} | {'Max DD%':<7} | {'Trades'}")
    print("=" * 135)

    for r in results:
        inv_str = f"₹{r['invested']:>9,.0f}"
        fin_str = f"₹{r['final_eq']:>13,.0f}"
        pnl_str = f"₹{r['net_profit']:>13,.0f}"
        pos_label = "Unrestricted" if r['max_pos'] == 999 else f"{r['max_pos']} Slots"
        print(f"{r['label']:<54} | {inv_str} | {fin_str} | {pnl_str} | {r['roi_pct']:>7.0f}% | {r['win_rate']:>5.1f}% | {r['profit_factor']:>4.2f} | {r['max_dd']:>6.1f}% | {r['trades']:>6}")
    print("-" * 135)

    # Save results to a summary file
    df_res = pd.DataFrame([{
        "Setup": r['label'],
        "Max_Positions": r['max_pos'],
        "Monthly_SIP": r['sip'],
        "Total_Invested": r['invested'],
        "Final_Capital": r['final_eq'],
        "Net_Profit": r['net_profit'],
        "ROI_Pct": r['roi_pct'],
        "Win_Rate": r['win_rate'],
        "Profit_Factor": r['profit_factor'],
        "Max_Drawdown": r['max_dd'],
        "Total_Trades": r['trades'],
        "Initial_SL_Trades": r['reasons'].get('Initial_Brick_SL', 0),
        "Break_Even_Trades": r['reasons'].get('Break_Even_SL', 0),
        "Supertrend_Reversal_Trades": r['reasons'].get('Supertrend_Reversal', 0)
    } for r in results])
    df_res.to_csv("strategy1_sip_and_unrestricted_results.csv", index=False)
    print("\nSaved detailed results to strategy1_sip_and_unrestricted_results.csv")


if __name__ == "__main__":
    main()
