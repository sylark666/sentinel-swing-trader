"""
test_daily_renko_win_win.py
=============================================================================
THE ULTIMATE WIN-WIN:
"DAILY RENKO SUPERTREND (1D Bricks)" across Nifty 500:
- Renko Bricks built on DAILY (1D) Candles (evaluated strictly ONCE a day at 3:15 PM)
- 100% Zerodha GTT automated stop loss
- Holds for multi-week swings (15 - 45 days)
- No intraday screen-time
- Compounding with ₹5,000/mo SIP
=============================================================================
"""

import sys
sys.path.insert(0, r'c:\Users\Advait\Desktop\Auto Swing Trader')
import sqlite3
import pandas as pd
import numpy as np
from tqdm import tqdm
from backtester import EngineMechanics
from strategy_renko.renko_engine import RenkoEngine
from strategy_renko.supertrend import calculate_supertrend

def run_daily_renko():
    print("Loading 1D candles for Daily Renko from market_data.db...")
    with sqlite3.connect("market_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT ticker FROM market_data WHERE timeframe='1D' AND ticker NOT IN ('NIFTY.NS', '^NSEI') ORDER BY ticker ASC")
        all_tickers = [r[0] for r in cursor.fetchall()]

    stock_renko = {}
    all_timestamps = set()

    with sqlite3.connect("market_data.db") as conn:
        for ticker in tqdm(all_tickers, desc="Building Daily Renko"):
            q = "SELECT timestamp, open, high, low, close, volume FROM market_data WHERE ticker = ? AND timeframe = '1D' AND timestamp >= '2020-01-01' ORDER BY timestamp ASC"
            df = pd.read_sql_query(q, conn, params=(ticker,))
            if df.empty or len(df) < 100: continue
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.sort_values(by="timestamp").reset_index(drop=True)

            avg_p = df["close"].iloc[0]
            brick_size = max(1.0, round(avg_p * 0.025, 2))  # 2.5% brick size on Daily

            re = RenkoEngine(brick_size=brick_size)
            bricks = re.build_bricks(df)
            if len(bricks) < 20: continue

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

    all_ts = sorted(list(all_timestamps))
    print(f"Built Daily Renko for {len(stock_renko)} stocks across {len(all_ts)} daily dates.")

    # Test two configurations:
    # Config 1: 5 Slots (20% Alloc) + ₹5k SIP
    # Config 2: 10 Slots (10% Alloc) + ₹5k SIP
    for max_pos, alloc in [(5, 0.20), (8, 0.125), (10, 0.10)]:
        simulate_daily_renko_portfolio(stock_renko, all_ts, max_pos=max_pos, alloc_pct=alloc, monthly_sip=5000.0)


def simulate_daily_renko_portfolio(stock_renko, all_ts, max_pos=8, alloc_pct=0.125, monthly_sip=5000.0):
    initial_cap = 100000.0
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_pos)
    cash = float(initial_cap)
    total_invested = float(initial_cap)
    positions = {}
    trades = []
    equity_curve = []
    last_month = None

    for ts in all_ts:
        cur_m = (ts.year, ts.month)
        if last_month is not None and cur_m != last_month and monthly_sip > 0:
            cash += monthly_sip
            total_invested += monthly_sip
        last_month = cur_m

        curr_eq = cash + sum(
            p["qty"] * stock_renko[s][ts]["close"] if ts in stock_renko[s] else p["qty"] * p["entry_px"]
            for s, p in positions.items()
        )
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
            p["days_held"] += 1

            # Ratchet BE at +2 bricks
            if not p["be_locked"] and high >= entry + (2.0 * b_size):
                p["be_locked"] = True
                p["sl"] = entry * 1.002

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
                    "symbol": s, "entry_date": p["entry_date"], "exit_date": ts,
                    "days_held": p["days_held"], "net_pnl": net,
                    "pnl_pct": ((exit_px - entry) / entry) * 100.0, "reason": reason
                })
                to_close.append(s)

        for s in to_close: del positions[s]

        # Entries
        can_open = len(positions) < max_pos and cash >= 5000.0
        if can_open:
            for s, series in stock_renko.items():
                if len(positions) >= max_pos: break
                if s in positions or ts not in series: continue
                b = series[ts]
                if b["signal"] == 1:
                    target_alloc = min(curr_eq * alloc_pct, cash)
                    if target_alloc < 5000.0: continue
                    entry_px = b["close"] * 1.002
                    qty = int(target_alloc // entry_px)
                    if qty <= 0: continue
                    gross_b = qty * entry_px
                    fric = engine.calculate_friction(gross_b, True)
                    if cash >= (gross_b + fric):
                        cash -= (gross_b + fric)
                        b_sz = b["brick_size"]
                        positions[s] = {
                            "entry_date": ts, "entry_px": entry_px, "qty": qty,
                            "brick_size": b_sz, "sl": entry_px - b_sz,
                            "be_locked": False, "days_held": 0
                        }

    final_eq = equity_curve[-1] if equity_curve else initial_cap
    tdf = pd.DataFrame(trades)
    wins = tdf[tdf["net_pnl"] > 0]
    losses = tdf[tdf["net_pnl"] <= 0]
    win_rate = len(wins) / len(tdf) * 100 if len(tdf) > 0 else 0
    gross_w = wins["net_pnl"].sum()
    gross_l = abs(losses["net_pnl"].sum())
    pf = (gross_w / gross_l) if gross_l > 0 else 999.0
    eq_s = pd.Series(equity_curve)
    peak = eq_s.cummax()
    dd_s = (eq_s - peak) / peak * 100.0
    max_dd = abs(float(dd_s.min()))
    years = 6.8

    print("\n" + "=" * 80)
    print(f"DAILY RENKO ({max_pos} SLOTS, {alloc_pct*100:.1f}% ALLOC) + ₹5K SIP")
    print("=" * 80)
    print(f"  • Final Capital:             ₹{final_eq:,.2f}")
    print(f"  • Net Realized Profit:       ₹{final_eq - total_invested:,.2f}")
    print(f"  • Win Rate:                  {win_rate:.1f}% ({len(wins)} Wins / {len(losses)} Losses)")
    print(f"  • Profit Factor:             {pf:.2f}")
    print(f"  • Max Drawdown:              {max_dd:.1f}%")
    print(f"  • Total Trades Taken:        {len(tdf)} ({len(tdf)/years:.1f} trades/yr = ~{len(tdf)/(years*12):.1f} trades/month!)")
    print(f"  • Average Holding Days:      {tdf['days_held'].mean():.1f} days")


if __name__ == "__main__":
    run_daily_renko()
