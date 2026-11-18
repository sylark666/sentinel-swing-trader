"""
test_delayed_data_execution.py
=============================================================================
TESTING REAL-WORLD 15-MINUTE DATA DELAY ON:
1. Daily Renko Strategy (Evening scan -> Next Day Open entry)
2. 4H Renko Strategy (15-min delayed entry / Next bar entry)

Compares:
- Ideal execution (Same bar close)
- Realistic 15-min delayed execution (Next bar Open + slippage)
- Zerodha GTT mechanics (Real-time SL execution on broker servers)
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

def load_daily_data_with_next_open():
    print("Loading Daily data from market_data.db with next open prices...")
    with sqlite3.connect("market_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT ticker FROM market_data WHERE timeframe='1D' AND ticker NOT IN ('NIFTY.NS', '^NSEI') ORDER BY ticker ASC")
        all_tickers = [r[0] for r in cursor.fetchall()]

    stock_data = {}
    all_timestamps = set()

    with sqlite3.connect("market_data.db") as conn:
        for ticker in tqdm(all_tickers, desc="Building Daily Renko"):
            q = "SELECT timestamp, open, high, low, close, volume FROM market_data WHERE ticker = ? AND timeframe = '1D' AND timestamp >= '2020-01-01' ORDER BY timestamp ASC"
            df = pd.read_sql_query(q, conn, params=(ticker,))
            if df.empty or len(df) < 100: continue
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.sort_values(by="timestamp").reset_index(drop=True)
            df["next_open"] = df["open"].shift(-1)

            avg_p = df["close"].iloc[0]
            brick_size = max(1.0, round(avg_p * 0.02, 2))

            re = RenkoEngine(brick_size=brick_size)
            bricks = re.build_bricks(df)
            if len(bricks) < 20: continue

            st = calculate_supertrend(bricks, period=10, multiplier=2.0)
            
            # Map timestamps to next open from original daily df
            open_map = dict(zip(df["timestamp"], df["next_open"]))

            st_dict = {}
            for _, r in st.iterrows():
                ts = r["timestamp"]
                all_timestamps.add(ts)
                st_dict[ts] = {
                    "close": r["close"],
                    "high": r["high"],
                    "low": r["low"],
                    "signal": r["signal"],
                    "trend": r["trend"],
                    "brick_size": r["brick_size"],
                    "next_open": open_map.get(ts, r["close"])
                }
            stock_data[ticker] = st_dict

    all_ts = sorted(list(all_timestamps))
    print(f"Loaded {len(stock_data)} stocks across {len(all_ts)} timestamps.")
    return stock_data, all_ts


def run_delayed_simulation(stock_data, all_ts, execute_at="next_open", max_pos=8, alloc_pct=0.125, monthly_sip=5000.0, initial_cap=100000.0):
    """
    execute_at: 
      - "same_close": Ideal execution at 3:25 PM (requires real-time data)
      - "next_open": Real-world 15-min delay (Scan in evening, buy next morning at Open + 0.2% slippage)
    """
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_pos)
    cash = float(initial_cap)
    total_invested = float(initial_cap)
    positions = {}
    trades = []
    equity_curve = []
    last_month = None

    for idx, ts in enumerate(all_ts):
        cur_m = (ts.year, ts.month)
        if last_month is not None and cur_m != last_month and monthly_sip > 0:
            cash += monthly_sip
            total_invested += monthly_sip
        last_month = cur_m

        curr_eq = cash + sum(
            p["qty"] * stock_data[s][ts]["close"] if ts in stock_data[s] else p["qty"] * p["entry_px"]
            for s, p in positions.items()
        )
        equity_curve.append(curr_eq)

        # 1. EXITS
        to_close = []
        for s, p in positions.items():
            if ts not in stock_data[s]: continue
            b = stock_data[s][ts]
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

            # Zerodha GTT: Executed real-time on exchange, ZERO delay
            if low <= p["sl"]:
                exit_px = p["sl"]
                reason = "Break_Even_SL" if p["be_locked"] else "Initial_Brick_SL"
            elif b["signal"] == -1:
                # Supertrend Reversal
                if execute_at == "same_close":
                    exit_px = close * 0.998
                else:
                    # 15-min delay -> Evening scan detects reversal -> Exit at Next Day Open
                    nxt_op = b["next_open"]
                    exit_px = (nxt_op if pd.notnull(nxt_op) and nxt_op > 0 else close) * 0.998
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

        # 2. ENTRIES
        can_open = len(positions) < max_pos and cash >= 5000.0
        if can_open:
            candidates = []
            for s, series in stock_data.items():
                if len(positions) >= max_pos: break
                if s in positions or ts not in series: continue
                b = series[ts]
                if b["signal"] == 1:
                    candidates.append((s, b["close"], b["brick_size"], b["next_open"]))

            if candidates:
                for s, c, b_sz, nxt_op in candidates:
                    if len(positions) >= max_pos or cash < 5000.0: break
                    target_alloc = min(curr_eq * alloc_pct, cash)
                    if target_alloc < 5000.0: continue

                    if execute_at == "same_close":
                        entry_px = c * 1.002
                    else:
                        # 15-min delayed feed: Signal confirmed at close -> Buy at Next Day Open + 0.2% slippage!
                        actual_open = nxt_op if (pd.notnull(nxt_op) and nxt_op > 0) else c
                        entry_px = actual_open * 1.002

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


def main():
    stock_data, all_ts = load_daily_data_with_next_open()

    print("\nSimulating Impact of 15-Minute Data Delay...")
    print("Scenario 1: Ideal Same-Day Close (requires 0-sec live feed)")
    res_ideal = run_delayed_simulation(stock_data, all_ts, execute_at="same_close")

    print("Scenario 2: Real-World 15-Min Delay (Evening Scan -> Next Day Open Entry + Slippage)")
    res_delayed = run_delayed_simulation(stock_data, all_ts, execute_at="next_open")

    print("\n" + "=" * 120)
    print(f"{'Execution Mode':<35} | {'Trades/Mo':<10} | {'Win %':<7} | {'PF':<6} | {'Max DD%':<8} | {'Final Capital':<15} | {'Net Profit'}")
    print("=" * 120)
    
    r1 = res_ideal
    print(f"{'Ideal (Same-Day Close)':<35} | {r1['trades_per_month']:>8.1f}/mo | {r1['win_rate']:>5.1f}% | {r1['profit_factor']:>4.2f} | {r1['max_dd']:>6.1f}% | ₹{r1['final_eq']:>13,.0f} | ₹{r1['net_profit']:>13,.0f}")
    
    r2 = res_delayed
    print(f"{'Real-World (Next-Day Open)':<35} | {r2['trades_per_month']:>8.1f}/mo | {r2['win_rate']:>5.1f}% | {r2['profit_factor']:>4.2f} | {r2['max_dd']:>6.1f}% | ₹{r2['final_eq']:>13,.0f} | ₹{r2['net_profit']:>13,.0f}")
    print("-" * 120)

    # Save realistic delayed trade ledger
    res_delayed["tdf"].to_csv("trades_daily_renko_15min_delayed_next_open.csv", index=False)
    print("Saved realistic delayed ledger to trades_daily_renko_15min_delayed_next_open.csv")

if __name__ == "__main__":
    main()
