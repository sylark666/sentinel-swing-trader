"""
test_kinetic_renko_alpha.py
=============================================================================
Evaluates our newly engineered proprietary strategy:
"KINETIC-RENKO ALPHA" (Macro EMA Filter + Volume Confirmation + Multi-Tier Ratchet)
Benchmarked against:
1. Baseline (Golden Cross 4H on Streamlit)
2. Strategy 1 (Standard Renko Supertrend)
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

UNIVERSE = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "BHARTIARTL.NS", "SBIN.NS", "LTIM.NS", "BAJFINANCE.NS", "HINDUNILVR.NS",
    "ITC.NS", "LT.NS", "KOTAKBANK.NS", "AXISBANK.NS", "TITAN.NS",
    "TATAMOTORS.NS", "MARUTI.NS", "SUNPHARMA.NS", "TRENT.NS", "HAL.NS",
    "DIXON.NS", "ZOMATO.NS", "ADANIENT.NS", "ADANIPORTS.NS", "COALINDIA.NS",
    "NTPC.NS", "POWERGRID.NS", "ONGC.NS", "JSWSTEEL.NS", "TATASTEEL.NS",
    "HINDALCO.NS", "VEDL.NS", "BHEL.NS", "RVNL.NS", "IRFC.NS",
    "PFC.NS", "RECLTD.NS", "SUZLON.NS", "POLYCAB.NS", "CUMMINSIND.NS",
    "SIEMENS.NS", "ABB.NS", "BEL.NS", "PERSISTENT.NS", "COFORGE.NS",
    "TVSMOTOR.NS", "EICHERMOT.NS", "CHOLAFIN.NS", "SHRIRAMFIN.NS", "MCX.NS",
    "KALYANKJIL.NS", "CIPLA.NS", "DRREDDY.NS", "LUPIN.NS", "APOLLOHOSP.NS",
    "MOTHERSON.NS", "HEROMOTOCO.NS", "TORNTPOWER.NS", "AUROPHARMA.NS", "CANBK.NS"
]


def run_kinetic_renko_tournament():
    print("Loading 4H historical data from market_data.db...")
    stock_raw = {}
    with sqlite3.connect("market_data.db") as conn:
        for sym in tqdm(UNIVERSE, desc="Loading data"):
            q = """
                SELECT timestamp, open, high, low, close, volume 
                FROM market_data 
                WHERE ticker = ? AND timeframe = '4H' AND timestamp >= '2020-01-01'
                ORDER BY timestamp ASC
            """
            df = pd.read_sql_query(q, conn, params=(sym,))
            if df.empty or len(df) < 220:
                continue
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.sort_values(by="timestamp").reset_index(drop=True)

            # Precalculate base EMAs and Volume Average on standard candles
            c = df["close"]
            df["ema50"] = c.ewm(span=50, adjust=False).mean()
            df["ema200"] = c.ewm(span=200, adjust=False).mean()
            df["vol_sma20"] = df["volume"].rolling(20).mean()
            df["vol_ratio"] = (df["volume"] / df["vol_sma20"].replace(0, np.nan)).fillna(1.0)
            stock_raw[sym] = df

    print(f"Loaded {len(stock_raw)} stocks.")

    # 1. Build Renko Series for each stock
    stock_renko_std = {}
    stock_renko_filtered = {}
    all_timestamps = set()

    for sym, df in stock_raw.items():
        avg_p = df["close"].iloc[0]
        b_size = max(1.0, round(avg_p * 0.015, 2))  # 1.5% brick size

        re = RenkoEngine(brick_size=b_size)
        bricks = re.build_bricks(df)
        if len(bricks) < 30: continue

        st = calculate_supertrend(bricks, period=10, multiplier=2.0)

        # Merge candle indicators to brick timestamps
        candle_map = df.set_index("timestamp")[["ema50", "ema200", "vol_ratio"]].to_dict("index")

        records_std = {}
        records_flt = {}

        for _, r in st.iterrows():
            ts = r["timestamp"]
            all_timestamps.add(ts)
            c_info = candle_map.get(ts, {"ema50": 0, "ema200": 0, "vol_ratio": 1.0})
            is_macro_bull = (r["close"] > c_info["ema200"]) and (c_info["ema50"] > c_info["ema200"])
            has_vol = c_info["vol_ratio"] >= 0.9

            b_data = {
                "close": r["close"],
                "high": r["high"],
                "low": r["low"],
                "signal": r["signal"],
                "trend": r["trend"],
                "brick_size": r["brick_size"]
            }
            records_std[ts] = b_data

            # Filtered: only take buy signal if macro bull + volume
            b_data_flt = dict(b_data)
            if r["signal"] == 1 and not (is_macro_bull and has_vol):
                b_data_flt["signal"] = 0  # Reject signal
            records_flt[ts] = b_data_flt

        stock_renko_std[sym] = records_std
        stock_renko_filtered[sym] = records_flt

    all_ts = sorted(list(all_timestamps))

    # Run Simulations
    print("\nSimulating Strategy 1 (Standard Renko Supertrend)...")
    res_strat1 = simulate_renko_engine("Strategy 1: Standard Renko Supertrend", stock_renko_std, all_ts, be_target_mult=1.0, lock_gain=0.0)

    print("Simulating Proprietary Strategy: KINETIC-RENKO ALPHA (Macro EMA + Vol + Asymmetric Ratchet)...")
    res_kinetic = simulate_renko_engine("Proprietary: Kinetic-Renko Alpha", stock_renko_filtered, all_ts, be_target_mult=1.5, lock_gain=0.5)

    print("\n" + "=" * 105)
    print(f"{'Strategy Architecture':<38} | {'Final Equity':<12} | {'CAGR %':<7} | {'Win Rate':<8} | {'PF':<5} | {'Max DD%':<8} | {'Calmar':<6} | {'Sharpe'}")
    print("=" * 105)
    for r in [res_strat1, res_kinetic]:
        print(f"{r['name']:<38} | ₹{r['final_equity']:>10,.0f} | {r['cagr_pct']:>6.1f}% | {r['win_rate']:>7.1f}% | {r['profit_factor']:>5.2f} | {r['max_drawdown']:>7.1f}% | {r['calmar_ratio']:>6.2f} | {r['sharpe_ratio']:>6.2f}")
    print("-" * 105)

    print("\nExit Breakdown Comparison:")
    for r in [res_strat1, res_kinetic]:
        print(f"\n{r['name']} ({r['trades']} trades):")
        for reason, count in r["reasons"].items():
            pct = (count / r['trades']) * 100
            print(f"  • {reason:<26}: {count:>4} trades ({pct:>5.1f}%)")


def simulate_renko_engine(name, stock_data, all_ts, initial_cap=100000.0, max_positions=3, alloc_pct=0.30, be_target_mult=1.0, lock_gain=0.0):
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_positions)
    cash = initial_cap
    positions = {}
    trades = []
    equity_curve = []

    for ts in all_ts:
        curr_eq = cash + sum(p["qty"] * stock_data[s][ts]["close"] if ts in stock_data[s] else p["qty"] * p["entry_px"] for s, p in positions.items())
        equity_curve.append(curr_eq)

        to_close = []
        for s, p in positions.items():
            if ts not in stock_data[s]: continue
            b = stock_data[s][ts]
            close = b["close"]
            high = b["high"]
            low = b["low"]
            entry = p["entry_px"]
            b_size = p["brick_size"]

            # Ratchet SL
            target_be = entry + (be_target_mult * b_size)
            if not p["be_locked"] and high >= target_be:
                p["be_locked"] = True
                p["sl"] = entry + (lock_gain * b_size)  # Lock gain or cost

            exit_px = None
            reason = None

            if low <= p["sl"]:
                exit_px = p["sl"]
                reason = "Break_Even_Locked_SL" if p["be_locked"] else "Initial_Brick_SL"
            elif b["signal"] == -1:
                exit_px = close
                reason = "Supertrend_Reversal_Exit"

            if exit_px:
                gross_b = p["qty"] * entry
                gross_s = p["qty"] * exit_px
                fric = engine.calculate_friction(gross_b, True) + engine.calculate_friction(gross_s, False)
                net = (gross_s - gross_b) - fric
                cash += (gross_s - engine.calculate_friction(gross_s, False))
                trades.append({"symbol": s, "entry_time": p["entry_time"], "exit_time": ts, "net_pnl": net, "pnl_pct": ((exit_px - entry)/entry)*100, "reason": reason})
                to_close.append(s)

        for s in to_close: del positions[s]

        # Entries
        if len(positions) < max_positions and cash > 5000:
            for s, series in stock_data.items():
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

    return {
        "name": name,
        "final_equity": final_eq,
        "cagr_pct": cagr,
        "trades": n,
        "win_rate": win_rate,
        "profit_factor": pf,
        "max_drawdown": max_dd,
        "calmar_ratio": calmar,
        "sharpe_ratio": sharpe,
        "reasons": reasons,
        "trades_df": tdf
    }


if __name__ == "__main__":
    run_kinetic_renko_tournament()
