"""
simulate_win_rate_optimizations.py
=============================================================================
Investigates the user's questions:
1. What can be done to improve win numbers / win rate and reduce losses / BE saves?
2. What happens if we optimize specifically for Win Rate?
   - Test 1: Macro Trend Filter (Price > 200 EMA & 50 > 200 EMA)
   - Test 2: Volume Surge Filter (Vol >= 1.0x 20-SMA)
   - Test 3: Wider BE Buffer (+2.0 Bricks before BE lock instead of +1.0)
   - Test 4: Fixed Take-Profit Targets (+3 Bricks / +6% or +5 Bricks / +10% target)
   - Test 5: Hybrid Champion (Macro Filter + Wider BE + 2-Stage Exit)
=============================================================================
"""

import sys
import os
import pickle
import sqlite3
import pandas as pd
import numpy as np

from backtester import EngineMechanics

CACHE_FILE = "nifty500_renko_cache_2020.pkl"

def load_data_and_indicators():
    with open(CACHE_FILE, "rb") as f:
        stock_renko, all_ts = pickle.load(f)

    # Let's load 4H candles to compute 50 EMA, 200 EMA and Volume Surge for each stock
    print("Loading 4H candles for macro indicators...")
    macro_info = {}
    with sqlite3.connect("market_data.db") as conn:
        for ticker in stock_renko.keys():
            q = f"SELECT timestamp, close, volume FROM market_data WHERE ticker = ? AND timeframe = '4H' AND timestamp >= '2020-01-01' ORDER BY timestamp ASC"
            df = pd.read_sql_query(q, conn, params=(ticker,))
            if df.empty: continue
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            c = df["close"]
            v = df["volume"]
            ema50 = c.ewm(span=50, adjust=False).mean()
            ema200 = c.ewm(span=200, adjust=False).mean()
            vol_sma20 = v.rolling(20).mean()
            vol_ratio = (v / vol_sma20.replace(0, np.nan)).fillna(1.0)

            m_dict = {}
            for idx, r in df.iterrows():
                m_dict[r["timestamp"]] = {
                    "close": r["close"],
                    "ema50": ema50.iloc[idx],
                    "ema200": ema200.iloc[idx],
                    "vol_ratio": vol_ratio.iloc[idx]
                }
            macro_info[ticker] = m_dict

    return stock_renko, all_ts, macro_info


def simulate_variation(stock_renko, all_ts, macro_info,
                       filter_macro=False,
                       filter_vol=False,
                       be_trigger_bricks=1.0,
                       take_profit_bricks=None,
                       partial_tp=False,
                       label=""):
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

    for ts in all_ts:
        cur_m = (ts.year, ts.month)
        if last_month is not None and cur_m != last_month and monthly_sip > 0:
            available_cash += monthly_sip
            total_invested += monthly_sip
        last_month = cur_m

        curr_eq = available_cash + sum(
            p["qty"] * stock_renko[s][ts]["close"] if ts in stock_renko[s] else p["qty"] * p["entry_px"]
            for s, p in open_positions.items()
        )
        equity_curve.append(curr_eq)

        to_close = []
        for s, p in open_positions.items():
            if ts not in stock_renko[s]: continue
            b = stock_renko[s][ts]
            close = b["close"]
            high = b["high"]
            low = b["low"]
            entry = p["entry_px"]
            b_size = p["brick_size"]

            # 1. Break-Even Ratchet
            if not p["be_locked"] and high >= entry + (be_trigger_bricks * b_size):
                p["be_locked"] = True
                p["sl"] = entry * 1.002

            # 2. Check Partial or Full Take Profit (if configured)
            tp_hit = False
            if take_profit_bricks is not None:
                tp_px = entry + (take_profit_bricks * b_size)
                if high >= tp_px:
                    tp_hit = True

            exit_px = None
            reason = None

            if tp_hit:
                exit_px = entry + (take_profit_bricks * b_size)
                reason = f"Take_Profit_{take_profit_bricks}B"
            elif low <= p["sl"]:
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
                    "entry_px": entry,
                    "exit_px": exit_px,
                    "net_pnl": net,
                    "pnl_pct": ((exit_px - entry) / entry) * 100.0,
                    "reason": reason
                })
                to_close.append(s)

        for s in to_close: del open_positions[s]

        # ENTRIES
        can_open = len(open_positions) < max_positions and available_cash >= 5000.0
        if can_open:
            for s, series in stock_renko.items():
                if len(open_positions) >= max_positions: break
                if s in open_positions or ts not in series: continue
                b = series[ts]
                if b["signal"] == 1:
                    # Apply Macro / Volume filters if enabled
                    if filter_macro and s in macro_info and ts in macro_info[s]:
                        mi = macro_info[s][ts]
                        # Must be above 200 EMA and 50 > 200 EMA
                        if mi["close"] < mi["ema200"] or mi["ema50"] < mi["ema200"]:
                            continue

                    if filter_vol and s in macro_info and ts in macro_info[s]:
                        mi = macro_info[s][ts]
                        if mi["vol_ratio"] < 1.0:
                            continue

                    target_alloc = min(curr_eq * alloc_pct, available_cash)
                    if target_alloc < 5000.0: continue
                    entry_px = b["close"] * 1.002
                    qty = int(target_alloc // entry_px)
                    if qty <= 0: continue
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
        return {"label": label, "trades": 0, "final_eq": final_eq, "net_profit": 0, "win_rate": 0, "pf": 0, "max_dd": 0, "be_saves": 0, "losses": 0, "wins": 0}

    wins = tdf[tdf["net_pnl"] > 0]
    losses = tdf[tdf["net_pnl"] <= 0]
    be_saves = tdf[tdf["reason"] == "Break_Even_SL"]
    initial_sl = tdf[tdf["reason"] == "Initial_Brick_SL"]
    win_rate = (len(wins) / n) * 100.0
    gross_w = wins["net_pnl"].sum()
    gross_l = abs(losses["net_pnl"].sum())
    pf = (gross_w / gross_l) if gross_l > 0 else 999.0

    return {
        "label": label,
        "trades": n,
        "wins": len(wins),
        "losses": len(initial_sl),
        "be_saves": len(be_saves),
        "final_eq": final_eq,
        "net_profit": net_profit,
        "win_rate": win_rate,
        "profit_factor": pf,
        "max_dd": max_dd
    }


def main():
    stock_renko, all_ts, macro_info = load_data_and_indicators()

    variations = [
        # Baseline C2: Raw Signals, +1.0 Brick BE, No Target
        {"label": "1. Current C2 Baseline (Raw Signals, BE @ +1B)", "filter_macro": False, "filter_vol": False, "be_trigger_bricks": 1.0, "take_profit_bricks": None},
        
        # Variation A: Wider BE Ratchet (+2.0 Bricks before BE lock)
        {"label": "2. Wider BE Buffer (Ratchet @ +2B instead of +1B)", "filter_macro": False, "filter_vol": False, "be_trigger_bricks": 2.0, "take_profit_bricks": None},

        # Variation B: Macro Bull Trend Filter (Close > 200 EMA & 50 > 200)
        {"label": "3. Macro Trend Filter (Price > 200 EMA & 50 > 200)", "filter_macro": True, "filter_vol": False, "be_trigger_bricks": 1.0, "take_profit_bricks": None},

        # Variation C: Volume Surge Filter (Vol >= 1.0x 20-SMA)
        {"label": "4. Volume Confirmation (Vol >= 1.0x SMA20)", "filter_macro": False, "filter_vol": True, "be_trigger_bricks": 1.0, "take_profit_bricks": None},

        # Variation D: Macro Filter + Volume Surge Combined
        {"label": "5. Macro Filter + Volume Surge Combined", "filter_macro": True, "filter_vol": True, "be_trigger_bricks": 1.0, "take_profit_bricks": None},

        # Variation E: What if we focus on Win Rate by taking Quick Targets? (+3 Bricks / ~6% Target)
        {"label": "6. Win-Rate Focus: Fixed Target @ +3 Bricks (~6%)", "filter_macro": False, "filter_vol": False, "be_trigger_bricks": 1.0, "take_profit_bricks": 3.0},

        # Variation F: Win-Rate Focus: Fixed Target @ +5 Bricks (~10% Target)
        {"label": "7. Win-Rate Focus: Fixed Target @ +5 Bricks (~10%)", "filter_macro": False, "filter_vol": False, "be_trigger_bricks": 1.0, "take_profit_bricks": 5.0},

        # Variation G: The Ideal Hybrid (Macro Filter + Wider BE @ +2B)
        {"label": "8. Hybrid Champion (Macro Filter + BE @ +2B)", "filter_macro": True, "filter_vol": False, "be_trigger_bricks": 2.0, "take_profit_bricks": None},
    ]

    print("\nRunning Win-Rate Optimization Matrix across 498 stocks...")
    results = []
    for var in variations:
        res = simulate_variation(
            stock_renko, all_ts, macro_info,
            filter_macro=var["filter_macro"],
            filter_vol=var["filter_vol"],
            be_trigger_bricks=var["be_trigger_bricks"],
            take_profit_bricks=var["take_profit_bricks"],
            label=var["label"]
        )
        results.append(res)

    print("\n" + "=" * 135)
    print(f"{'Strategy Variation':<52} | {'Win %':<6} | {'Wins':<5} | {'Losses':<6} | {'BE Saves':<8} | {'Trades':<6} | {'PF':<5} | {'Final Capital':<15} | {'Max DD%'}")
    print("=" * 135)
    for r in results:
        cap_str = f"₹{r['final_eq']:>13,.0f}"
        print(f"{r['label']:<52} | {r['win_rate']:>5.1f}% | {r['wins']:>5} | {r['losses']:>6} | {r['be_saves']:>8} | {r['trades']:>6} | {r['profit_factor']:>4.2f} | {cap_str} | {r['max_dd']:>6.1f}%")
    print("-" * 135)


if __name__ == "__main__":
    main()
