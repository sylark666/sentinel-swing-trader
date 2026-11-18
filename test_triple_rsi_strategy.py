"""
test_triple_rsi_strategy.py
=============================================================================
BACKTESTING QUANTIFIED STRATEGIES: TRIPLE RSI (90% WIN RATE ENGINE)
Rules:
1. 5-day RSI < 30
2. 5-day RSI fallen 3 days in a row (RSI[t] < RSI[t-1] < RSI[t-2])
3. 5-day RSI was below 60 three days ago (RSI[t-3] < 60)
4. Close > 200-day moving average (SMA200)
5. Buy at Close (or Next Open for manual traders)
6. Sell when 5-day RSI crosses above 50
Universe: Nifty 500 Stocks (2020-2026)
Capital: ₹1,00,000 + ₹5,000/mo SIP (and fixed slots)
Friction: Full Indian delivery friction (0.35%)
=============================================================================
"""

import sqlite3
import pandas as pd
import numpy as np
from tqdm import tqdm
from backtester import EngineMechanics

def calculate_rsi(series: pd.Series, period: int = 5) -> pd.Series:
    delta = series.diff()
    gain = (delta.where(delta > 0, 0.0))
    loss = (-delta.where(delta < 0, 0.0))
    # Wilder's Smoothing
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)

def load_and_prep_triple_rsi():
    print("Loading Daily data from market_data.db...")
    with sqlite3.connect("market_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT ticker FROM market_data WHERE timeframe='1D' AND ticker NOT IN ('NIFTY.NS', '^NSEI') ORDER BY ticker ASC")
        tickers = [r[0] for r in cursor.fetchall()]

    all_stock_data = {}
    all_timestamps = set()

    with sqlite3.connect("market_data.db") as conn:
        for t in tqdm(tickers, desc="Computing Triple RSI"):
            q = "SELECT timestamp, open, high, low, close, volume FROM market_data WHERE ticker = ? AND timeframe = '1D' AND timestamp >= '2019-01-01' ORDER BY timestamp ASC"
            df = pd.read_sql_query(q, conn, params=(t,))
            if df.empty or len(df) < 220: continue
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.sort_values(by="timestamp").reset_index(drop=True)

            c = df["close"]
            df["sma200"] = c.rolling(window=200).mean()
            df["rsi5"] = calculate_rsi(c, period=5)

            # Rule conditions
            # 1. RSI < 30
            c1 = df["rsi5"] < 30.0
            # 2. RSI down 3 days in a row: rsi5[t] < rsi5[t-1] and rsi5[t-1] < rsi5[t-2] and rsi5[t-2] < rsi5[t-3]
            r = df["rsi5"]
            c2 = (r < r.shift(1)) & (r.shift(1) < r.shift(2)) & (r.shift(2) < r.shift(3))
            # 3. RSI < 60 three days ago
            c3 = r.shift(3) < 60.0
            # 4. Close > SMA200
            c4 = c > df["sma200"]

            df["buy_signal"] = c1 & c2 & c3 & c4
            # Exit condition: RSI5 > 50
            df["exit_signal"] = df["rsi5"] > 50.0

            # Next open for realistic execution
            df["next_open"] = df["open"].shift(-1)

            # Filter for evaluation period (from 2020)
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
                    "rsi5": row["rsi5"],
                    "buy_signal": bool(row["buy_signal"]),
                    "exit_signal": bool(row["exit_signal"]),
                    "next_open": row["next_open"]
                }
            all_stock_data[t] = d_dict

    all_ts = sorted(list(all_timestamps))
    print(f"Prepped {len(all_stock_data)} stocks across {len(all_ts)} daily timestamps.")
    return all_stock_data, all_ts


def backtest_triple_rsi(stock_data, all_ts, max_pos=10, alloc_pct=0.10, monthly_sip=5000.0, initial_cap=100000.0, execute_at="close"):
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
            entry = p["entry_px"]
            p["days_held"] += 1

            exit_px = None
            reason = None

            # Exit rule: RSI5 crosses above 50
            if b["exit_signal"] and p["days_held"] >= 1:
                if execute_at == "close":
                    exit_px = close * 0.998
                else:
                    nxt = b["next_open"]
                    exit_px = (nxt if pd.notnull(nxt) and nxt > 0 else close) * 0.998
                reason = "RSI5_Cross_50"
            # Emergency Time Stop / Deep Stop if held > 20 days
            elif p["days_held"] >= 25:
                exit_px = close * 0.998
                reason = "Max_Days_Timeout"

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
                if b["buy_signal"]:
                    candidates.append((s, b["close"], b["rsi5"], b["next_open"]))

            if candidates:
                # Prioritize most oversold (lowest RSI5)
                candidates.sort(key=lambda x: x[2])

                for s, c, rsi_val, nxt_op in candidates:
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
        "avg_hold": round(tdf["days_held"].mean(), 1) if len(tdf) > 0 else 0,
        "tdf": tdf
    }

def main():
    stock_data, all_ts = load_and_prep_triple_rsi()
    
    print("\nRunning Backtests for Triple RSI Strategy...")
    # Test 1: Ideal Close
    res_close = backtest_triple_rsi(stock_data, all_ts, max_pos=10, alloc_pct=0.10, execute_at="close")
    
    # Test 2: Next Open (15-min delay realistic execution)
    res_open = backtest_triple_rsi(stock_data, all_ts, max_pos=10, alloc_pct=0.10, execute_at="next_open")

    print("\n" + "=" * 125)
    print(f"{'Execution Mode':<28} | {'Trades/Mo':<10} | {'Win Rate %':<11} | {'PF':<6} | {'Avg Hold':<9} | {'Max DD%':<8} | {'Final Capital':<15} | {'Net Profit'}")
    print("=" * 125)
    for name, r in [("Ideal (Buy/Sell @ Close)", res_close), ("Realistic (Next-Day Open)", res_open)]:
        print(f"{name:<28} | {r['trades_per_month']:>8.1f}/mo | {r['win_rate']:>9.1f}% | {r['profit_factor']:>4.2f} | {r['avg_hold']:>6.1f} d | {r['max_dd']:>6.1f}% | ₹{r['final_eq']:>13,.0f} | ₹{r['net_profit']:>13,.0f}")
    print("-" * 125)

    # Save ledger
    res_close["tdf"].to_csv("trades_triple_rsi_close.csv", index=False)
    res_open["tdf"].to_csv("trades_triple_rsi_next_open.csv", index=False)
    print("Exported trade ledgers to trades_triple_rsi_close.csv and trades_triple_rsi_next_open.csv")

if __name__ == "__main__":
    main()
