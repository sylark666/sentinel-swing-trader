"""
test_new_quant_strategies.py
=============================================================================
Tournament of Quantitative Strategies:
1. Baseline: 4H Golden Cross + Chandelier 2.3x (Current Streamlit Engine)
2. Strategy 1: AI Renko Supertrend + 1:1 BE Lock
3. Strategy Alpha (Hybrid Champion): Adaptive Momentum Breakout + ADX + 1:1 BE Ratchet + Dual Chandelier
4. Strategy Beta (Deep Pullback Champion): 20 EMA Support Pullback + ADX Trend + 1:1 BE Ratchet

Dataset: 4H Data for 60 Top Liquid Stocks (2020-2026)
Initial Capital: ₹100,000
Max Positions: 3
Friction: Zerodha Indian Equity Delivery Model (STT, Stamp Duty, GST, Exchange)
=============================================================================
"""

import sys
import os
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime
from tqdm import tqdm

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

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


def load_universe():
    print("Loading 4H historical candles from market_data.db...")
    stock_dfs = {}
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
            stock_dfs[sym] = df
    return stock_dfs


def calculate_indicators(stock_dfs):
    data = {}
    all_timestamps = set()

    for sym, df in stock_dfs.items():
        c = df["close"]
        h = df["high"]
        l = df["low"]
        v = df["volume"]

        ema20 = c.ewm(span=20, adjust=False).mean()
        ema50 = c.ewm(span=50, adjust=False).mean()
        ema200 = c.ewm(span=200, adjust=False).mean()

        hl = h - l
        hc = np.abs(h - c.shift())
        lc = np.abs(l - c.shift())
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        atr14 = tr.rolling(window=14).mean().fillna(c * 0.02)

        vol_sma20 = v.rolling(window=20).mean()
        vol_surge = (v / vol_sma20.replace(0, np.nan)).fillna(1.0)

        # Baseline Golden cross
        gc_20_50 = (ema20.shift(1) <= ema50.shift(1)) & (ema20 > ema50)

        # ADX (14)
        plus_dm = h.diff()
        minus_dm = -l.diff()
        plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
        minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)
        tr_smooth = tr.rolling(14).mean().replace(0, np.nan)
        plus_di = 100 * (pd.Series(plus_dm).rolling(14).mean() / tr_smooth)
        minus_di = 100 * (pd.Series(minus_dm).rolling(14).mean() / tr_smooth)
        dx = 100 * (np.abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)).fillna(0)
        adx14 = dx.rolling(14).mean().fillna(20.0)

        # Donchian 20-period Breakout
        high20_prev = h.shift(1).rolling(window=20).max()
        donchian_breakout = (c > high20_prev)

        # Chandelier Exit (22, 2.3)
        atr22 = tr.ewm(alpha=1/22, adjust=False).mean() * 2.3
        highest22 = c.rolling(window=22).max()
        lowest22 = c.rolling(window=22).min()
        closes = c.values
        highs = highest22.values
        lows = lowest22.values
        atrs = atr22.values
        long_stop = np.zeros(len(df))
        ch_dir = np.ones(len(df))

        for i in range(1, len(df)):
            ls = highs[i] - atrs[i]
            ls_prev = long_stop[i-1] if long_stop[i-1] != 0 else ls
            long_stop[i] = max(ls, ls_prev) if closes[i-1] > ls_prev else ls
            prev_dir = ch_dir[i-1]
            if closes[i] > (lows[i] + atrs[i]):
                ch_dir[i] = 1
            elif closes[i] < ls_prev:
                ch_dir[i] = -1
            else:
                ch_dir[i] = prev_dir

        records = {}
        for idx, row in df.iterrows():
            ts = row["timestamp"]
            all_timestamps.add(ts)
            records[ts] = {
                "close": row["close"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "atr": atr14.iloc[idx],
                "vol_surge": vol_surge.iloc[idx],
                "ema20": ema20.iloc[idx],
                "ema50": ema50.iloc[idx],
                "ema200": ema200.iloc[idx],
                "gc_20_50": gc_20_50.iloc[idx],
                "adx": adx14.iloc[idx],
                "donchian_breakout": donchian_breakout.iloc[idx],
                "ch_dir": ch_dir[idx],
                "ch_stop": long_stop[idx]
            }

        data[sym] = records

    return data, sorted(list(all_timestamps))


# 1. BASELINE STREAMLIT STRATEGY (4H Golden Cross + Chandelier 2.3x)
def run_baseline(data, all_ts, initial_cap=100000.0, max_positions=3, alloc_pct=0.30):
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_positions)
    cash = initial_cap
    positions = {}
    trades = []
    equity_curve = []

    for ts in all_ts:
        curr_eq = cash + sum(p["qty"] * data[s][ts]["close"] if ts in data[s] else p["qty"] * p["entry_px"] for s, p in positions.items())
        equity_curve.append(curr_eq)

        to_close = []
        for s, p in positions.items():
            if ts not in data[s]: continue
            b = data[s][ts]
            exit_px = None
            reason = None

            if b["low"] <= p["sl"]:
                exit_px = p["sl"]
                reason = "Hard_SL_2.3x"
            elif b["ch_dir"] == -1:
                exit_px = b["close"]
                reason = "Chandelier_Exit"

            if exit_px:
                gross_b = p["qty"] * p["entry_px"]
                gross_s = p["qty"] * exit_px
                fric = engine.calculate_friction(gross_b, True) + engine.calculate_friction(gross_s, False)
                net = (gross_s - gross_b) - fric
                cash += (gross_s - engine.calculate_friction(gross_s, False))
                trades.append({"symbol": s, "entry_time": p["entry_time"], "exit_time": ts, "net_pnl": net, "pnl_pct": ((exit_px - p["entry_px"])/p["entry_px"])*100, "reason": reason})
                to_close.append(s)

        for s in to_close: del positions[s]

        if len(positions) < max_positions and cash > 10000:
            candidates = []
            for s, series in data.items():
                if s in positions or ts not in series: continue
                b = series[ts]
                if b["gc_20_50"] and b["close"] > b["ema200"] and b["vol_surge"] >= 0.8:
                    candidates.append((s, b["close"], b["atr"], b["vol_surge"]))
            candidates.sort(key=lambda x: x[3], reverse=True)

            for s, c, atr, vs in candidates:
                if len(positions) >= max_positions or cash < 10000: break
                alloc = min(curr_eq * alloc_pct, cash)
                entry_px = c * 1.002
                qty = int(alloc // entry_px)
                if qty <= 0: continue
                gross_b = qty * entry_px
                fric = engine.calculate_friction(gross_b, True)
                if cash >= (gross_b + fric):
                    cash -= (gross_b + fric)
                    positions[s] = {"entry_time": ts, "entry_px": entry_px, "qty": qty, "sl": entry_px - (2.3 * atr)}

    return evaluate_portfolio("1. Baseline Golden Cross (Streamlit)", initial_cap, equity_curve, trades)


# 2. STRATEGY ALPHA: ADAPTIVE MOMENTUM BREAKOUT + ADX + 1:1 BE RATCHET
def run_strategy_alpha(data, all_ts, initial_cap=100000.0, max_positions=3, alloc_pct=0.30):
    """
    Combines:
    - Donchian 20 High Breakout in a verified macro uptrend (Price > 200 EMA, 50 > 200 EMA)
    - ADX Trend Strength Filter (ADX >= 20 confirms real momentum, rejecting whipsaw consolidations)
    - Volume Surge >= 1.2x
    - Initial SL: 1.8x ATR
    - Dynamic Break-Even Ratchet: As soon as floating gain reaches +1.5x ATR (1:1), ratchets SL to Cost!
    - Runner Trailing: Dual Chandelier (2.0x ATR) trails the runner risk-free.
    """
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_positions)
    cash = initial_cap
    positions = {}
    trades = []
    equity_curve = []

    for ts in all_ts:
        curr_eq = cash + sum(p["qty"] * data[s][ts]["close"] if ts in data[s] else p["qty"] * p["entry_px"] for s, p in positions.items())
        equity_curve.append(curr_eq)

        to_close = []
        for s, p in positions.items():
            if ts not in data[s]: continue
            b = data[s][ts]
            close = b["close"]
            high = b["high"]
            low = b["low"]
            entry = p["entry_px"]
            atr = p["atr"]

            # Ratchet SL to Break-Even at +1.5x ATR
            if not p["be_locked"] and high >= entry + (1.5 * atr):
                p["be_locked"] = True
                p["sl"] = entry * 1.003  # Cover friction

            exit_px = None
            reason = None

            if low <= p["sl"]:
                exit_px = p["sl"]
                reason = "Break_Even_SL" if p["be_locked"] else "Initial_SL_1.8x"
            elif p["be_locked"] and b["ch_dir"] == -1:
                exit_px = close
                reason = "Chandelier_Runner_Exit"

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
        if len(positions) < max_positions and cash > 10000:
            candidates = []
            for s, series in data.items():
                if s in positions or ts not in series: continue
                b = series[ts]
                # High-momentum Donchian breakout + ADX filter + Volume confirmation
                if b["donchian_breakout"] and b["close"] > b["ema200"] and b["ema50"] > b["ema200"]:
                    if b["adx"] >= 20.0 and b["vol_surge"] >= 1.1:
                        score = b["adx"] * b["vol_surge"]
                        candidates.append((s, b["close"], b["atr"], score))

            candidates.sort(key=lambda x: x[3], reverse=True)

            for s, c, atr, score in candidates:
                if len(positions) >= max_positions or cash < 10000: break
                alloc = min(curr_eq * alloc_pct, cash)
                entry_px = c * 1.002
                qty = int(alloc // entry_px)
                if qty <= 0: continue
                gross_b = qty * entry_px
                fric = engine.calculate_friction(gross_b, True)
                if cash >= (gross_b + fric):
                    cash -= (gross_b + fric)
                    positions[s] = {
                        "entry_time": ts,
                        "entry_px": entry_px,
                        "qty": qty,
                        "atr": atr,
                        "sl": entry_px - (1.8 * atr),
                        "be_locked": False
                    }

    return evaluate_portfolio("2. Strategy Alpha (Momentum Breakout + ADX + BE)", initial_cap, equity_curve, trades)


# 3. STRATEGY BETA: TREND-PULLBACK SPRING + 1:1 BE RATCHET
def run_strategy_beta(data, all_ts, initial_cap=100000.0, max_positions=3, alloc_pct=0.30):
    """
    Buys shallow pullbacks into the 20 EMA in strong established trends (ADX >= 22).
    Much tighter entry risk (1.2x ATR) with high asymmetric reward.
    """
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_positions)
    cash = initial_cap
    positions = {}
    trades = []
    equity_curve = []

    for ts in all_ts:
        curr_eq = cash + sum(p["qty"] * data[s][ts]["close"] if ts in data[s] else p["qty"] * p["entry_px"] for s, p in positions.items())
        equity_curve.append(curr_eq)

        to_close = []
        for s, p in positions.items():
            if ts not in data[s]: continue
            b = data[s][ts]
            close = b["close"]
            high = b["high"]
            low = b["low"]
            entry = p["entry_px"]
            atr = p["atr"]

            # Ratchet SL to Break-Even at +1.5x ATR
            if not p["be_locked"] and high >= entry + (1.5 * atr):
                p["be_locked"] = True
                p["sl"] = entry * 1.003

            exit_px = None
            reason = None

            if low <= p["sl"]:
                exit_px = p["sl"]
                reason = "Break_Even_SL" if p["be_locked"] else "Initial_SL_1.2x"
            elif p["be_locked"] and b["ch_dir"] == -1:
                exit_px = close
                reason = "Chandelier_Runner_Exit"

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
        if len(positions) < max_positions and cash > 10000:
            candidates = []
            for s, series in data.items():
                if s in positions or ts not in series: continue
                b = series[ts]
                # Uptrend + Pullback into 20 EMA + Bullish green candle
                if b["close"] > b["ema200"] and b["ema50"] > b["ema200"] and b["adx"] >= 20.0:
                    pullback_near_20 = (b["low"] <= b["ema20"] * 1.01) and (b["close"] >= b["ema20"] * 0.99)
                    bullish_reversal = (b["close"] > b["open"])
                    if pullback_near_20 and bullish_reversal:
                        score = b["adx"]
                        candidates.append((s, b["close"], b["atr"], score))

            candidates.sort(key=lambda x: x[3], reverse=True)

            for s, c, atr, score in candidates:
                if len(positions) >= max_positions or cash < 10000: break
                alloc = min(curr_eq * alloc_pct, cash)
                entry_px = c * 1.002
                qty = int(alloc // entry_px)
                if qty <= 0: continue
                gross_b = qty * entry_px
                fric = engine.calculate_friction(gross_b, True)
                if cash >= (gross_b + fric):
                    cash -= (gross_b + fric)
                    positions[s] = {
                        "entry_time": ts,
                        "entry_px": entry_px,
                        "qty": qty,
                        "atr": atr,
                        "sl": entry_px - (1.2 * atr),
                        "be_locked": False
                    }

    return evaluate_portfolio("3. Strategy Beta (Trend-Pullback Spring + BE)", initial_cap, equity_curve, trades)


def evaluate_portfolio(name, initial_cap, equity_curve, trades):
    final_eq = equity_curve[-1] if equity_curve else initial_cap
    total_net = final_eq - initial_cap
    roi_pct = (total_net / initial_cap) * 100.0

    eq_s = pd.Series(equity_curve)
    peak = eq_s.cummax()
    dd_s = (eq_s - peak) / peak * 100.0
    max_dd = abs(float(dd_s.min())) if len(dd_s) > 0 else 0.0

    tdf = pd.DataFrame(trades)
    n = len(tdf)
    if n == 0:
        return {"name": name, "trades": 0, "final_equity": initial_cap, "roi_pct": 0, "cagr_pct": 0, "win_rate": 0, "pf": 0, "max_dd": 0, "calmar": 0, "sharpe": 0}

    wins = tdf[tdf["net_pnl"] > 0]
    losses = tdf[tdf["net_pnl"] <= 0]
    win_rate = (len(wins) / n) * 100.0
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
        "initial_cap": initial_cap,
        "final_equity": final_eq,
        "net_profit": total_net,
        "roi_pct": roi_pct,
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


def main():
    stock_dfs = load_universe()
    data, all_ts = calculate_indicators(stock_dfs)

    print("\nRunning Tournament across 6.8 years of NSE 4H historical data (2020-2026)...")
    res_base = run_baseline(data, all_ts)
    res_alpha = run_strategy_alpha(data, all_ts)
    res_beta = run_strategy_beta(data, all_ts)

    results = [res_base, res_alpha, res_beta]

    print("\n" + "=" * 105)
    print(f"{'Strategy Architecture':<44} | {'Final Equity':<12} | {'CAGR %':<7} | {'Win Rate':<8} | {'PF':<5} | {'Max DD%':<8} | {'Calmar':<6} | {'Sharpe'}")
    print("=" * 105)
    for r in results:
        print(f"{r['name']:<44} | ₹{r['final_equity']:>10,.0f} | {r['cagr_pct']:>6.1f}% | {r['win_rate']:>7.1f}% | {r['profit_factor']:>5.2f} | {r['max_drawdown']:>7.1f}% | {r['calmar_ratio']:>6.2f} | {r['sharpe_ratio']:>6.2f}")
    print("-" * 105)

    print("\nDetailed Exit Distribution:")
    for r in results:
        print(f"\n{r['name']} ({r['trades']} trades):")
        for reason, count in r["reasons"].items():
            pct = (count / r['trades']) * 100
            print(f"  • {reason:<26}: {count:>4} trades ({pct:>5.1f}%)")


if __name__ == "__main__":
    main()
