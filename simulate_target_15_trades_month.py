"""
simulate_target_15_trades_month.py
=============================================================================
ENGINEERING THE EXACT "12 TO 15 TRADES / MONTH" SWEET SPOT:
Target:
- Trade Frequency: Exactly 12 to 15 completed trades per month
- Wealth Generation: Multi-Crore Compounding (₹1.5 Cr to ₹2.8 Cr+)
- Manual Execution: 100% Zerodha GTT automated stop loss & targets
- High Win Rate & Asymmetric Edge: Profit Factor > 3.5 - 4.5
- Tested across Full Nifty 500 (2020-2026) with ₹5,000/mo SIP + ₹100,000 initial capital
=============================================================================
"""

import sys
sys.path.insert(0, r'c:\Users\Advait\Desktop\Auto Swing Trader')
import sqlite3
import pickle
import pandas as pd
import numpy as np
from tqdm import tqdm
from backtester import EngineMechanics
from strategy_renko.renko_engine import RenkoEngine
from strategy_renko.supertrend import calculate_supertrend

def load_or_build_renko(timeframe="1D", brick_pct=0.02):
    cache_file = f"renko_cache_{timeframe}_{int(brick_pct*1000)}.pkl"
    if False and os.path.exists(cache_file):
        with open(cache_file, "rb") as f:
            return pickle.load(f)

    print(f"Building {timeframe} Renko with {brick_pct*100:.1f}% bricks from market_data.db...")
    with sqlite3.connect("market_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT DISTINCT ticker FROM market_data WHERE timeframe='{timeframe}' AND ticker NOT IN ('NIFTY.NS', '^NSEI') ORDER BY ticker ASC")
        all_tickers = [r[0] for r in cursor.fetchall()]

    stock_renko = {}
    all_timestamps = set()

    with sqlite3.connect("market_data.db") as conn:
        for ticker in tqdm(all_tickers, desc=f"Renko {timeframe}"):
            q = f"SELECT timestamp, open, high, low, close, volume FROM market_data WHERE ticker = ? AND timeframe = '{timeframe}' AND timestamp >= '2020-01-01' ORDER BY timestamp ASC"
            df = pd.read_sql_query(q, conn, params=(ticker,))
            if df.empty or len(df) < 100: continue
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.sort_values(by="timestamp").reset_index(drop=True)

            avg_p = df["close"].iloc[0]
            brick_size = max(1.0, round(avg_p * brick_pct, 2))

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
    print(f"Built {len(stock_renko)} stocks across {len(all_ts)} timestamps.")
    return stock_renko, all_ts


def load_macro_indicators(timeframe="4H"):
    print(f"Loading {timeframe} macro indicators from DB...")
    macro_info = {}
    with sqlite3.connect("market_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT DISTINCT ticker FROM market_data WHERE timeframe='{timeframe}' AND ticker NOT IN ('NIFTY.NS', '^NSEI')")
        tickers = [r[0] for r in cursor.fetchall()]

        for t in tqdm(tickers, desc="Indicators"):
            q = f"SELECT timestamp, close, volume FROM market_data WHERE ticker = ? AND timeframe = '{timeframe}' AND timestamp >= '2019-06-01' ORDER BY timestamp ASC"
            df = pd.read_sql_query(q, conn, params=(t,))
            if df.empty: continue
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            c = df["close"]
            v = df["volume"]
            ema50 = c.ewm(span=50, adjust=False).mean()
            ema200 = c.ewm(span=200, adjust=False).mean()
            vol20 = v.rolling(20).mean()
            vol_ratio = (v / vol20.replace(0, np.nan)).fillna(1.0)
            mom60 = (c / c.shift(60).replace(0, np.nan) - 1.0) * 100.0

            m_dict = {}
            for idx, r in df.iterrows():
                m_dict[r["timestamp"]] = {
                    "close": r["close"],
                    "ema50": ema50.iloc[idx],
                    "ema200": ema200.iloc[idx],
                    "vol_ratio": vol_ratio.iloc[idx],
                    "mom60": mom60.iloc[idx]
                }
            macro_info[t] = m_dict
    return macro_info


def run_targeted_backtest(stock_renko, all_ts, macro_info=None,
                          filter_macro=False,
                          filter_vol=False,
                          filter_mom=False,
                          max_pos=8,
                          alloc_pct=0.125,
                          be_buffer_bricks=2.0,
                          monthly_sip=5000.0,
                          initial_cap=100000.0,
                          label=""):
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

        # Exits
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

            # Ratchet BE at +be_buffer_bricks (gives breathing room!)
            if not p["be_locked"] and high >= entry + (be_buffer_bricks * b_size):
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
            for s, series in stock_renko.items():
                if len(positions) >= max_pos: break
                if s in positions or ts not in series: continue
                b = series[ts]
                if b["signal"] == 1:
                    # Apply filters if configured
                    if macro_info and s in macro_info and ts in macro_info[s]:
                        mi = macro_info[s][ts]
                        if filter_macro and (mi["close"] <= mi["ema200"] or mi["ema50"] <= mi["ema200"]):
                            continue
                        if filter_vol and mi["vol_ratio"] < 1.0:
                            continue
                        if filter_mom and mi["mom60"] <= 0:
                            continue
                        mom_score = mi.get("mom60", 0)
                    else:
                        mom_score = 0

                    candidates.append((s, b["close"], b["brick_size"], mom_score))

            if candidates:
                # Rank by momentum
                candidates.sort(key=lambda x: x[3], reverse=True)

                for s, c, b_sz, _ in candidates:
                    if len(positions) >= max_pos or cash < 5000.0: break
                    target_alloc = min(curr_eq * alloc_pct, cash)
                    if target_alloc < 5000.0: continue
                    entry_px = c * 1.002
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
    losses = tdf[tdf["reason"] == "Initial_Brick_SL"]
    be_saves = tdf[tdf["reason"] == "Break_Even_SL"]
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
        "invested": total_invested,
        "final_eq": final_eq,
        "net_profit": final_eq - total_invested,
        "win_rate": len(wins) / len(tdf) * 100 if len(tdf) > 0 else 0,
        "profit_factor": pf,
        "max_dd": max_dd,
        "trades": len(tdf),
        "trades_per_month": round(trades_per_month, 1),
        "initial_losses": len(losses),
        "be_saves": len(be_saves),
        "avg_hold": round(tdf["days_held"].mean(), 1),
        "fric_total": tdf["fric"].sum() if len(tdf) > 0 else 0,
        "tdf": tdf
    }


def main():
    print("=" * 90)
    print("OPTIMIZING FOR USER'S SWEET SPOT: 12 TO 15 TRADES PER MONTH")
    print("=" * 90)

    # 1. Load 4H Renko data from cache
    with open("nifty500_renko_cache_2020.pkl", "rb") as f:
        renko_4h, ts_4h = pickle.load(f)

    # 2. Load Macro indicators
    macro_4h = load_macro_indicators(timeframe="4H")

    # 3. Load 1D Renko data (2.0% brick size)
    renko_1d, ts_1d = load_or_build_renko(timeframe="1D", brick_pct=0.02)

    tests = [
        # Model 1: 4H Precision Renko - 8 Slots (12.5% alloc) + Macro Filter + BE @ +2B
        (renko_4h, ts_4h, macro_4h, True, True, False, 8, 0.125, 2.0, "1. Precision 4H Renko (8 Slots, Macro+Vol, BE @ +2B)"),

        # Model 2: 4H Precision Renko - 10 Slots (10% alloc) + Macro Filter + BE @ +2B
        (renko_4h, ts_4h, macro_4h, True, True, False, 10, 0.10, 2.0, "2. Precision 4H Renko (10 Slots, Macro+Vol, BE @ +2B)"),

        # Model 3: 4H Precision Renko - 8 Slots + Macro + Momentum + Vol + BE @ +2B
        (renko_4h, ts_4h, macro_4h, True, True, True, 8, 0.125, 2.0, "3. Strict 4H Alpha Renko (8 Slots, Macro+Vol+Mom, BE @ +2B)"),

        # Model 4: Daily Renko (2.0% Bricks, 8 Slots, BE @ +2B)
        (renko_1d, ts_1d, None, False, False, False, 8, 0.125, 2.0, "4. Daily Renko 2.0% Bricks (8 Slots, BE @ +2B)"),

        # Model 5: Daily Renko (2.0% Bricks, 10 Slots, BE @ +2B)
        (renko_1d, ts_1d, None, False, False, False, 10, 0.10, 2.0, "5. Daily Renko 2.0% Bricks (10 Slots, BE @ +2B)"),
    ]

    print("\nRunning Targeted 12-15 Trades/Month Tournament...")
    results = []
    for r_data, ts, m_info, f_macro, f_vol, f_mom, m_pos, alloc, be_buf, lbl in tests:
        res = run_targeted_backtest(
            r_data, ts, macro_info=m_info,
            filter_macro=f_macro, filter_vol=f_vol, filter_mom=f_mom,
            max_pos=m_pos, alloc_pct=alloc, be_buffer_bricks=be_buf,
            monthly_sip=5000.0, label=lbl
        )
        results.append(res)

    print("\n" + "=" * 140)
    print(f"{'Strategy Architecture':<54} | {'Trades/Mo':<9} | {'Win %':<6} | {'Losses':<6} | {'BE Saves':<8} | {'PF':<5} | {'Max DD%':<7} | {'Final Capital':<15} | {'Net Profit'}")
    print("=" * 140)
    for r in results:
        cap_str = f"₹{r['final_eq']:>13,.0f}"
        pnl_str = f"₹{r['net_profit']:>13,.0f}"
        print(f"{r['label']:<54} | {r['trades_per_month']:>7.1f}/mo | {r['win_rate']:>5.1f}% | {r['initial_losses']:>6} | {r['be_saves']:>8} | {r['profit_factor']:>4.2f} | {r['max_dd']:>6.1f}% | {cap_str} | {pnl_str}")
    print("-" * 140)

    # Save the absolute champion to CSV
    # Filter candidates between 12.0 and 16.0 trades/month
    in_range = [r for r in results if 11.5 <= r["trades_per_month"] <= 16.5]
    if in_range:
        champion = max(in_range, key=lambda x: x["final_eq"])
    else:
        champion = max(results, key=lambda x: x["final_eq"])

    champ_file = "champion_sweetspot_15trades_month.csv"
    champion["tdf"].to_csv(champ_file, index=False)
    print(f"\n🏆 CHAMPION SELECTED: {champion['label']}")
    print(f"Exported full trade ledger to {champ_file}")


if __name__ == "__main__":
    main()
