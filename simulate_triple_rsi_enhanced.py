"""
simulate_triple_rsi_enhanced.py
=============================================================================
ENGINEERING THE INSTITUTIONAL ENHANCED TRIPLE RSI ENGINE:
Implementing the 3 Vital Modifications:
1. Catastrophic Hard Stop Loss (Testing 5%, 6%, 7%, 8%, and 2.5x ATR)
2. Strict Time-Stop (Max 6 to 8 days holding)
3. Dynamic Profit Capture:
   - Standard RSI > 50 Exit
   - Connors 5-period SMA Target
   - Reversal Confirmation (Close > Open / Bullish Reversal)
   - Dynamic Trailing Stop
4. Realistic 15-Min Delayed Execution (Next-Day Open with 0.2% slippage)
5. Capital: ₹1,00,000 + ₹5,000/mo SIP across Nifty 500 (2020-2026)
=============================================================================
"""

import sys
sys.path.insert(0, r'c:\Users\Advait\Desktop\Auto Swing Trader')
import sqlite3
import pandas as pd
import numpy as np
from tqdm import tqdm
from backtester import EngineMechanics

def calculate_rsi(series: pd.Series, period: int = 5) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

def load_data():
    print("Loading Nifty 500 Daily data from market_data.db...")
    with sqlite3.connect("market_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT ticker FROM market_data WHERE timeframe='1D' AND ticker NOT IN ('NIFTY.NS', '^NSEI') ORDER BY ticker ASC")
        tickers = [r[0] for r in cursor.fetchall()]

    stock_data = {}
    all_timestamps = set()

    with sqlite3.connect("market_data.db") as conn:
        for t in tqdm(tickers, desc="Preparing Indicators"):
            q = "SELECT timestamp, open, high, low, close, volume FROM market_data WHERE ticker = ? AND timeframe = '1D' AND timestamp >= '2019-01-01' ORDER BY timestamp ASC"
            df = pd.read_sql_query(q, conn, params=(t,))
            if df.empty or len(df) < 220: continue
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.sort_values(by="timestamp").reset_index(drop=True)

            c = df["close"]
            h = df["high"]
            l = df["low"]

            df["sma200"] = c.rolling(200).mean()
            df["sma5"] = c.rolling(5).mean()
            df["rsi5"] = calculate_rsi(c, period=5)

            # ATR 14
            tr1 = h - l
            tr2 = (h - c.shift(1)).abs()
            tr3 = (l - c.shift(1)).abs()
            df["atr14"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean().fillna(c * 0.02)

            r = df["rsi5"]
            c1 = r < 30.0
            c2 = (r < r.shift(1)) & (r.shift(1) < r.shift(2)) & (r.shift(2) < r.shift(3))
            c3 = r.shift(3) < 60.0
            c4 = c > df["sma200"]

            df["raw_buy"] = c1 & c2 & c3 & c4
            df["green_candle"] = c > df["open"]
            df["next_open"] = df["open"].shift(-1)

            df_eval = df[df["timestamp"] >= "2020-01-01"].copy()
            if df_eval.empty: continue

            d_dict = {}
            for _, row in df_eval.iterrows():
                ts = row["timestamp"]
                all_timestamps.add(ts)
                d_dict[ts] = {
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "sma200": row["sma200"],
                    "sma5": row["sma5"],
                    "rsi5": row["rsi5"],
                    "atr14": row["atr14"],
                    "raw_buy": bool(row["raw_buy"]),
                    "green_candle": bool(row["green_candle"]),
                    "next_open": row["next_open"]
                }
            stock_data[t] = d_dict

    all_ts = sorted(list(all_timestamps))
    print(f"Loaded {len(stock_data)} stocks across {len(all_ts)} daily timestamps.")
    return stock_data, all_ts


def run_simulation(stock_data, all_ts, 
                   sl_type="fixed", # "fixed" or "atr"
                   sl_value=0.06,   # 6% or 2.5x ATR
                   max_days=7,      # Time-stop in days
                   target_type="rsi50", # "rsi50", "sma5", or "hybrid"
                   require_green=False,
                   execute_at="next_open", # "close" or "next_open"
                   max_pos=10, 
                   alloc_pct=0.10, 
                   label=""):
    engine = EngineMechanics(initial_capital=100000.0, max_positions=max_pos)
    cash = 100000.0
    total_invested = 100000.0
    positions = {}
    trades = []
    equity_curve = []
    last_month = None

    for ts in all_ts:
        cur_m = (ts.year, ts.month)
        if last_month is not None and cur_m != last_month:
            cash += 5000.0
            total_invested += 5000.0
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
            low = b["low"]
            entry = p["entry_px"]
            p["days_held"] += 1

            exit_px = None
            reason = None

            # A. Hard Stop Loss
            if sl_type == "fixed" and low <= entry * (1.0 - sl_value):
                exit_px = entry * (1.0 - sl_value)
                reason = "Hard_Stop_Loss"
            elif sl_type == "atr" and low <= p["atr_stop"]:
                exit_px = p["atr_stop"]
                reason = "ATR_Stop_Loss"

            # B. Target Exit
            elif p["days_held"] >= 1:
                target_hit = False
                if target_type == "rsi50" and b["rsi5"] >= 50.0:
                    target_hit = True
                elif target_type == "sma5" and close >= b["sma5"]:
                    target_hit = True
                elif target_type == "hybrid" and (b["rsi5"] >= 50.0 or close >= b["sma5"]):
                    target_hit = True

                if target_hit:
                    if execute_at == "close":
                        exit_px = close * 0.998
                    else:
                        nxt = b["next_open"]
                        exit_px = (nxt if pd.notnull(nxt) and nxt > 0 else close) * 0.998
                    reason = "Target_Exit"

            # C. Time-Stop
            if not exit_px and max_days > 0 and p["days_held"] >= max_days:
                if execute_at == "close":
                    exit_px = close * 0.998
                else:
                    nxt = b["next_open"]
                    exit_px = (nxt if pd.notnull(nxt) and nxt > 0 else close) * 0.998
                reason = f"Time_Stop_{max_days}d"

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
                if b["raw_buy"]:
                    if require_green and not b["green_candle"]:
                        continue
                    candidates.append((s, b["close"], b["rsi5"], b["atr14"], b["next_open"]))

            if candidates:
                # Rank by most oversold
                candidates.sort(key=lambda x: x[2])

                for s, c, _, atr_val, nxt_op in candidates:
                    if len(positions) >= max_pos or cash < 5000.0: break
                    target_alloc = min(curr_eq * alloc_pct, cash)
                    if target_alloc < 5000.0: continue

                    if execute_at == "close":
                        entry_px = c * 1.002
                    else:
                        op = nxt_op if pd.notnull(nxt_op) and nxt_op > 0 else c
                        entry_px = op * 1.002

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
                            "atr_stop": entry_px - (sl_value * atr_val) if sl_type == "atr" else 0,
                            "days_held": 0
                        }

    final_eq = equity_curve[-1] if equity_curve else 100000.0
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
        "label": label,
        "final_eq": final_eq,
        "net_profit": final_eq - total_invested,
        "win_rate": len(wins) / len(tdf) * 100 if len(tdf) > 0 else 0,
        "profit_factor": pf,
        "max_dd": max_dd,
        "trades": len(tdf),
        "trades_per_month": round(trades_per_month, 1),
        "avg_hold": round(tdf["days_held"].mean(), 1) if len(tdf) > 0 else 0,
        "worst_loss": round(tdf["pnl_pct"].min(), 2) if len(tdf) > 0 else 0,
        "tdf": tdf
    }

def main():
    stock_data, all_ts = load_data()

    print("\nBENCHMARKING INSTITUTIONAL ENHANCEMENTS ON TRIPLE RSI:")
    tests = [
        # Baseline Raw (No Stop Loss, No Time Stop)
        ("1. Raw Baseline (No SL, No Time Stop, At Close)", "fixed", 0.0, 0, "rsi50", False, "close"),
        
        # Modification 1: Adding 6% Stop Loss
        ("2. Mod 1: Added 6% Hard Stop Loss", "fixed", 0.06, 0, "rsi50", False, "close"),

        # Modification 2: Adding 6% Stop + 7-Day Time Stop
        ("3. Mod 1+2: 6% Hard Stop + 7-Day Time Stop", "fixed", 0.06, 7, "rsi50", False, "close"),

        # Modification 3A: Mod 1+2 + Reversal Candle (Close > Open)
        ("4. Mod 1+2+3A: 6% SL + 7d Time + Green Candle", "fixed", 0.06, 7, "rsi50", True, "close"),

        # Modification 3B: Connors 5 SMA Target Exit + 6% SL + 7d Time
        ("5. Mod 1+2+3B: 6% SL + 7d Time + Connors 5 SMA Exit", "fixed", 0.06, 7, "sma5", False, "close"),

        # Modification 3C: ATR Volatility Stop (2.0x ATR) + 7d Time + Hybrid Exit
        ("6. Mod 1+2+3C: 2.0x ATR Stop + 7d Time + Hybrid Exit", "atr", 2.0, 7, "hybrid", False, "close"),

        # REAL-WORLD 15-MIN DELAY TEST: Next-Day Open Execution for Champion
        ("7. CHAMPION REALISTIC: 6% SL + 7d Time (Next-Day Open Entry)", "fixed", 0.06, 7, "rsi50", False, "next_open"),
        ("8. CHAMPION REALISTIC + Green Candle (Next-Day Open Entry)", "fixed", 0.06, 7, "rsi50", True, "next_open"),
    ]

    results = []
    for lbl, stype, sval, mdays, tgttype, reqgrn, exec_mode in tests:
        res = run_simulation(
            stock_data, all_ts,
            sl_type=stype, sl_value=sval, max_days=mdays,
            target_type=tgttype, require_green=reqgrn,
            execute_at=exec_mode, label=lbl
        )
        results.append(res)

    print("\n" + "=" * 140)
    print(f"{'Strategy Architecture':<58} | {'Trades/Mo':<9} | {'Win %':<6} | {'PF':<5} | {'Avg Hold':<8} | {'Worst Trd':<9} | {'Max DD%':<7} | {'Final Capital':<15} | {'Net Profit'}")
    print("=" * 140)
    for r in results:
        print(f"{r['label']:<58} | {r['trades_per_month']:>7.1f}/mo | {r['win_rate']:>5.1f}% | {r['profit_factor']:>4.2f} | {r['avg_hold']:>6.1f}d | {r['worst_loss']:>7.1f}% | {r['max_dd']:>6.1f}% | ₹{r['final_eq']:>13,.0f} | ₹{r['net_profit']:>13,.0f}")
    print("-" * 140)

    # Save champion ledger
    champ = results[2] # Mod 1+2: 6% SL + 7d Time Stop
    champ["tdf"].to_csv("trades_triple_rsi_enhanced_champion.csv", index=False)
    print("\nExported enhanced champion trade ledger to trades_triple_rsi_enhanced_champion.csv")

if __name__ == "__main__":
    main()
