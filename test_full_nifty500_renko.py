"""
test_full_nifty500_renko.py
=============================================================================
Tests Strategy 1 (Adaptive Renko Bricks + Supertrend + 1:1 Break-Even Ratchet)
Across the ENTIRE NIFTY 500 UNIVERSE (498 Stocks, 2020-2026):
- Starting Capital: ₹100,000
- Max Concurrent Positions: 3
- Allocation: 30% per trade (~₹30,000)
- Friction: Full Zerodha Indian Equity Delivery Friction
=============================================================================
"""

import sys
import os
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime
from tqdm import tqdm

from backtester import EngineMechanics
from strategy_renko.renko_engine import RenkoEngine
from strategy_renko.supertrend import calculate_supertrend


def load_nifty500_renko():
    print("Loading 4H historical data for all Nifty 500 stocks...")
    with sqlite3.connect("market_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT ticker FROM market_data WHERE timeframe='4H' AND ticker NOT IN ('NIFTY.NS', '^NSEI') ORDER BY ticker ASC")
        all_tickers = [r[0] for r in cursor.fetchall()]

    stock_renko = {}
    all_timestamps = set()

    with sqlite3.connect("market_data.db") as conn:
        for ticker in tqdm(all_tickers, desc="Building Renko Bricks"):
            q = """
                SELECT timestamp, open, high, low, close, volume 
                FROM market_data 
                WHERE ticker = ? AND timeframe = '4H' AND timestamp >= '2020-01-01'
                ORDER BY timestamp ASC
            """
            df = pd.read_sql_query(q, conn, params=(ticker,))
            if df.empty or len(df) < 150:
                continue

            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.sort_values(by="timestamp").reset_index(drop=True)

            avg_price = df["close"].iloc[0]
            brick_size = max(1.0, round(avg_price * 0.02, 2))  # 2.0% brick size for broad 500 universe

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

    print(f"Built Renko bricks for {len(stock_renko)} Nifty 500 stocks across {len(all_timestamps)} timestamps.")
    return stock_renko, sorted(list(all_timestamps))


def run_full_renko_simulation(stock_renko, all_ts, initial_cap=100000.0, max_positions=3, alloc_pct=0.30):
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_positions)
    cash = initial_cap
    positions = {}
    trades = []
    equity_curve = []

    for ts in all_ts:
        curr_eq = cash + sum(p["qty"] * stock_renko[s][ts]["close"] if ts in stock_renko[s] else p["qty"] * p["entry_px"] for s, p in positions.items())
        equity_curve.append(curr_eq)

        to_close = []
        for s, p in positions.items():
            if ts not in stock_renko[s]: continue
            b = stock_renko[s][ts]
            close = b["close"]
            high = b["high"]
            low = b["low"]
            entry = p["entry_px"]
            b_size = p["brick_size"]

            # Ratchet SL to Break-Even at +1.0 Brick
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
                cash += (gross_s - engine.calculate_friction(gross_s, False))
                trades.append({
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

        for s in to_close: del positions[s]

        # Entries
        if len(positions) < max_positions and cash > 5000:
            for s, series in stock_renko.items():
                if len(positions) >= max_positions: break
                if s in positions or ts not in series: continue
                b = series[ts]
                if b["signal"] == 1:
                    alloc = min(curr_eq * alloc_pct, cash)
                    if alloc < 5000: continue
                    entry_px = b["close"] * 1.002
                    qty = int(alloc // entry_px)
                    if qty <= 0: continue
                    gross_b = qty * entry_px
                    fric = engine.calculate_friction(gross_b, True)
                    if cash >= (gross_b + fric):
                        cash -= (gross_b + fric)
                        b_sz = b["brick_size"]
                        positions[s] = {
                            "entry_time": ts,
                            "entry_px": entry_px,
                            "qty": qty,
                            "brick_size": b_sz,
                            "sl": entry_px - b_sz,
                            "be_locked": False
                        }

    final_eq = equity_curve[-1] if equity_curve else initial_cap
    total_net = final_eq - initial_cap
    roi_pct = (total_net / initial_cap) * 100.0

    eq_s = pd.Series(equity_curve)
    peak = eq_s.cummax()
    dd_s = (eq_s - peak) / peak * 100.0
    max_dd = abs(float(dd_s.min())) if len(dd_s) > 0 else 0.0

    tdf = pd.DataFrame(trades)
    n = len(tdf)
    wins = tdf[tdf["net_pnl"] > 0]
    losses = tdf[tdf["net_pnl"] <= 0]
    win_rate = (len(wins) / n) * 100.0 if n > 0 else 0
    gross_w = wins["net_pnl"].sum()
    gross_l = abs(losses["net_pnl"].sum())
    pf = (gross_w / gross_l) if gross_l > 0 else 999.0

    years = 6.8
    cagr = ((final_eq / initial_cap) ** (1.0 / years) - 1.0) * 100.0
    calmar = (cagr / max_dd) if max_dd > 0 else 0.0

    pnl_s = tdf["net_pnl"]
    sharpe = (pnl_s.mean() / pnl_s.std()) * np.sqrt(n / years) if pnl_s.std() > 0 else 0.0
    reasons = tdf["reason"].value_counts().to_dict()

    print("\n" + "=" * 100)
    print("FULL NIFTY 500 TOURNAMENT: STRATEGY 1 (RENKO SUPERTREND) RESULTS")
    print("=" * 100)
    print(f"  • Starting Capital:          ₹{initial_cap:,.2f}")
    print(f"  • Final Capital:             ₹{final_eq:,.2f}")
    print(f"  • Net Realized Profit:       ₹{total_net:,.2f} (+{roi_pct:,.1f}% ROI)")
    print(f"  • Annualized CAGR:           {cagr:.1f}%")
    print(f"  • Total Trades Taken:        {n}")
    print(f"  • Win Rate:                  {win_rate:.1f}% ({len(wins)} Wins / {len(losses)} Losses)")
    print(f"  • Profit Factor:             {pf:.2f}")
    print(f"  • Max Portfolio Drawdown:    {max_dd:.1f}%")
    print(f"  • Calmar Ratio:              {calmar:.2f}")
    print(f"  • Annualized Sharpe:         {sharpe:.2f}")

    print("\nExit Breakdown:")
    for r, cnt in reasons.items():
        print(f"  • {r:<24}: {cnt:>4} trades ({(cnt/n)*100:>5.1f}%)")

    return final_eq, cagr, win_rate, pf, max_dd, calmar, sharpe


if __name__ == "__main__":
    stock_renko, all_ts = load_nifty500_renko()
    run_full_renko_simulation(stock_renko, all_ts)
