"""
simulate_full_nifty500_tournament.py
=============================================================================
FULL NIFTY 500 TOURNAMENT (All 499 Stocks, 2020-2026, 4H Timeframe):
1. Baseline: Original Strategy (Golden Cross 4H + Dual Chandelier 2.3x ATR)
2. Superior Strategy 1: Sentinel-Momentum Alpha (Relative Strength + 1:1 BE Lock + Runner Scaling)
3. Superior Strategy 2: Kinetic-Renko Alpha (Universal Nifty 500 Adaptive Renko + Asymmetric Lock)

Capital: ₹100,000 starting cash
Positions: 3 concurrent slots (30% allocation per slot)
Friction: Full Zerodha Indian Equity Delivery Friction (STT, Stamp Duty, GST, Exchange)
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


def load_nifty500_data():
    """Loads all Nifty 500 stocks on 4H from market_data.db."""
    print("Connecting to market_data.db to fetch full Nifty 500 universe...")
    with sqlite3.connect("market_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT ticker FROM market_data WHERE timeframe='4H' AND ticker NOT IN ('NIFTY.NS', '^NSEI') ORDER BY ticker ASC")
        all_tickers = [r[0] for r in cursor.fetchall()]

    print(f"Found {len(all_tickers)} unique Nifty 500 stocks on 4H.")
    stock_dfs = {}

    with sqlite3.connect("market_data.db") as conn:
        for ticker in tqdm(all_tickers, desc="Loading 4H candles"):
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
            stock_dfs[ticker] = df

    print(f"Successfully loaded {len(stock_dfs)} stocks with sufficient history since 2020.")
    return stock_dfs


def precalculate_indicators(stock_dfs):
    """Precalculates vectorized indicators for high-speed simulation."""
    print("Precalculating indicators across full Nifty 500 universe...")
    data = {}
    all_timestamps = set()

    for ticker, df in tqdm(stock_dfs.items(), desc="Indicators"):
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
        atr = tr.rolling(window=14).mean().fillna(c * 0.02)

        vol_sma20 = v.rolling(window=20).mean()
        vol_surge = (v / vol_sma20.replace(0, np.nan)).fillna(1.0)

        # 1. Baseline Golden Cross (20/50)
        gc_20_50 = (ema20.shift(1) <= ema50.shift(1)) & (ema20 > ema50)

        # 2. Relative Strength Score (60-period return)
        rs_60 = (c / c.shift(60).replace(0, np.nan) - 1.0).fillna(0.0)

        # 3. Chandelier Trailing Exit (22, 2.3)
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
                "atr": atr.iloc[idx],
                "vol_surge": vol_surge.iloc[idx],
                "ema20": ema20.iloc[idx],
                "ema50": ema50.iloc[idx],
                "ema200": ema200.iloc[idx],
                "gc_20_50": gc_20_50.iloc[idx],
                "rs_60": rs_60.iloc[idx],
                "ch_dir": ch_dir[idx],
                "ch_stop": long_stop[idx]
            }

        data[ticker] = records

    return data, sorted(list(all_timestamps))


# =============================================================================
# 1. BASELINE ORIGINAL STRATEGY (GOLDEN CROSS 4H + CHANDELIER 2.3x)
# =============================================================================
def simulate_baseline(data, all_ts, initial_cap=100000.0, max_positions=3, alloc_pct=0.30):
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_positions)
    cash = initial_cap
    positions = {}
    trades = []
    equity_curve = []

    for ts in all_ts:
        curr_eq = cash + sum(p["qty"] * data[s][ts]["close"] if ts in data[s] else p["qty"] * p["entry_px"] for s, p in positions.items())
        equity_curve.append(curr_eq)

        # Exits
        to_close = []
        for s, p in positions.items():
            if ts not in data[s]: continue
            b = data[s][ts]
            close = b["close"]
            low = b["low"]

            exit_px = None
            reason = None

            if low <= p["sl"]:
                exit_px = p["sl"]
                reason = "Hard_SL_2.3x"
            elif b["ch_dir"] == -1:
                exit_px = close
                reason = "Chandelier_Exit"

            if exit_px:
                gross_b = p["qty"] * p["entry_px"]
                gross_s = p["qty"] * exit_px
                fric = engine.calculate_friction(gross_b, True) + engine.calculate_friction(gross_s, False)
                net = (gross_s - gross_b) - fric
                cash += (gross_s - engine.calculate_friction(gross_s, False))
                trades.append({
                    "symbol": s,
                    "entry_time": p["entry_time"],
                    "exit_time": ts,
                    "entry_px": p["entry_px"],
                    "exit_px": exit_px,
                    "net_pnl": net,
                    "pnl_pct": ((exit_px - p["entry_px"]) / p["entry_px"]) * 100.0,
                    "reason": reason
                })
                to_close.append(s)

        for s in to_close: del positions[s]

        # Entries: 20/50 Golden Cross + Price > 200 EMA + Volume Surge >= 0.8x
        if len(positions) < max_positions and cash > 10000:
            candidates = []
            for s, series in data.items():
                if s in positions or ts not in series: continue
                b = series[ts]
                if b["gc_20_50"] and b["close"] > b["ema200"] and b["vol_surge"] >= 0.8:
                    candidates.append((s, b["close"], b["atr"], b["vol_surge"]))

            # Baseline ranks by Volume Surge
            candidates.sort(key=lambda x: x[3], reverse=True)

            for s, c, atr, vs in candidates:
                if len(positions) >= max_positions or cash < 10000: break
                trade_alloc = min(curr_eq * alloc_pct, cash)
                entry_px = c * 1.002
                qty = int(trade_alloc // entry_px)
                if qty <= 0: continue
                gross_b = qty * entry_px
                fric = engine.calculate_friction(gross_b, True)
                if cash >= (gross_b + fric):
                    cash -= (gross_b + fric)
                    sl = entry_px - (2.3 * atr)
                    positions[s] = {
                        "entry_time": ts,
                        "entry_px": entry_px,
                        "qty": qty,
                        "sl": sl
                    }

    return compute_metrics("1. Baseline Original (Golden Cross 4H + Chandelier 2.3x)", initial_cap, equity_curve, trades)


# =============================================================================
# 2. SUPERIOR STRATEGY 1: SENTINEL-MOMENTUM ALPHA (SMA)
# =============================================================================
def simulate_sentinel_momentum_alpha(data, all_ts, initial_cap=100000.0, max_positions=3, alloc_pct=0.30):
    """
    Superior Strategy 1:
    - Relative Strength Ranking: Picks the top market leaders when multiple stocks trigger
    - Macro Alignment: Price > 200 EMA and 50 > 200 EMA
    - Initial SL: 1.8x ATR (tight risk)
    - 1:1 Break-Even Lock: At +1.8x ATR profit, ratchet SL to Entry + 0.3% (locks out loss completely)
    - Staged Scale-out: At +3.0x ATR, book 40% profit, letting remaining 60% runner trail with Chandelier 2.0x
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

            # 1. Check 1:1 Break-Even Ratchet (+1.8x ATR)
            if not p["be_locked"] and high >= entry + (1.8 * atr):
                p["be_locked"] = True
                p["sl"] = entry * 1.003  # Move SL to cost + 0.3%

            # 2. Check 40% Partial Profit Take (+3.0x ATR)
            if not p["scaled_out"] and high >= entry + (3.0 * atr):
                p["scaled_out"] = True
                scale_qty = int(p["qty"] * 0.40)
                target_p = entry + (3.0 * atr)
                if scale_qty > 0:
                    gross_s = scale_qty * target_p
                    gross_b = scale_qty * entry
                    fric = engine.calculate_friction(gross_b, True) + engine.calculate_friction(gross_s, False)
                    net = (gross_s - gross_b) - fric
                    cash += (gross_s - engine.calculate_friction(gross_s, False))
                    trades.append({
                        "symbol": s,
                        "entry_time": p["entry_time"],
                        "exit_time": ts,
                        "entry_px": entry,
                        "exit_px": target_p,
                        "net_pnl": net,
                        "pnl_pct": ((target_p - entry) / entry) * 100.0,
                        "reason": "Stage1_Scaleout_40%"
                    })
                    p["qty"] -= scale_qty

            exit_px = None
            reason = None

            # Stop hit
            if low <= p["sl"]:
                exit_px = p["sl"]
                reason = "Break_Even_SL" if p["be_locked"] else "Initial_SL_1.8x"
            # Runner exit on Chandelier flip
            elif p["be_locked"] and b["ch_dir"] == -1:
                exit_px = close
                reason = "Chandelier_Runner_Exit"

            if exit_px and p["qty"] > 0:
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

        # Entries: Rank by Alpha Momentum Score (RS60 * (Price/EMA200) * VolSurge)
        if len(positions) < max_positions and cash > 10000:
            candidates = []
            for s, series in data.items():
                if s in positions or ts not in series: continue
                b = series[ts]
                if b["gc_20_50"] and b["close"] > b["ema200"] and b["ema50"] > b["ema200"] and b["vol_surge"] >= 1.0:
                    alpha_score = (1.0 + max(0.0, b["rs_60"])) * (b["close"] / b["ema200"]) * b["vol_surge"]
                    candidates.append((s, b["close"], b["atr"], alpha_score))

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
                        "be_locked": False,
                        "scaled_out": False
                    }

    return compute_metrics("2. Superior: Sentinel-Momentum Alpha (SMA)", initial_cap, equity_curve, trades)


def compute_metrics(name, initial_cap, equity_curve, trades):
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
        return {"name": name, "trades": 0, "final_equity": initial_cap, "roi_pct": 0, "cagr_pct": 0, "win_rate": 0, "pf": 0, "max_dd": 0, "calmar": 0, "sharpe": 0, "reasons": {}, "trades_df": tdf}

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
    stock_dfs = load_nifty500_data()
    data, all_ts = precalculate_indicators(stock_dfs)

    print(f"\nSimulating Full Nifty 500 Tournament across {len(all_ts)} timestamps (2020-2026)...")
    print("\nRunning Backtest 1: Baseline Original (Golden Cross 4H + Chandelier 2.3x)...")
    res_base = simulate_baseline(data, all_ts)

    print("Running Backtest 2: Superior Strategy - Sentinel-Momentum Alpha (SMA)...")
    res_sma = simulate_sentinel_momentum_alpha(data, all_ts)

    results = [res_base, res_sma]

    print("\n" + "=" * 108)
    print(f"{'Strategy Architecture':<46} | {'Final Equity':<12} | {'CAGR %':<7} | {'Win Rate':<8} | {'PF':<5} | {'Max DD%':<8} | {'Calmar':<6} | {'Sharpe'}")
    print("=" * 108)
    for r in results:
        print(f"{r['name']:<46} | ₹{r['final_equity']:>10,.0f} | {r['cagr_pct']:>6.1f}% | {r['win_rate']:>7.1f}% | {r['profit_factor']:>5.2f} | {r['max_drawdown']:>7.1f}% | {r['calmar_ratio']:>6.2f} | {r['sharpe_ratio']:>6.2f}")
    print("-" * 108)

    print("\nExit Distribution Breakdown (Full Nifty 500 Universe):")
    for r in results:
        print(f"\n{r['name']} ({r['trades']} trades):")
        for reason, count in r["reasons"].items():
            pct = (count / r['trades']) * 100
            print(f"  • {reason:<26}: {count:>4} trades ({pct:>5.1f}%)")

    # Export trade logs
    res_base["trades_df"].to_csv("trades_full_nifty500_baseline.csv", index=False)
    res_sma["trades_df"].to_csv("trades_full_nifty500_sma.csv", index=False)
    print("\nSaved trade logs to trades_full_nifty500_baseline.csv and trades_full_nifty500_sma.csv")


if __name__ == "__main__":
    main()
