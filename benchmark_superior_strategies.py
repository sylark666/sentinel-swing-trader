"""
benchmark_superior_strategies.py
=============================================================================
Rigorous Head-to-Head Comparison:
1. Baseline Production Engine: 4H Golden Cross + Dual Chandelier (2.3x ATR)
2. Superior Strategy A: Kinetic Pullback Engine (KPE) - Trend Pullback + Staged 1:1 BE Lock
3. Superior Strategy B: Volatility Squeeze Breakout (VSB) - Squeeze Expansion + Vol Parity

Dataset: Local NSE 4H Data from market_data.db (2020-2026)
Initial Capital: ₹1,00,000
Max Positions: 3
Friction: Full Zerodha Indian Equity Delivery Friction Model (STT, Stamp, GST, Txn)
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


def load_dataset():
    """Loads 4H OHLCV candles from market_data.db."""
    print("Loading 4H candles from market_data.db for 60 liquid stocks (2020-2026)...")
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
    print(f"Loaded {len(stock_dfs)} stocks.")
    return stock_dfs


def precalculate_indicators(stock_dfs):
    """Computes all technical indicators for the 3 strategies."""
    data = {}
    all_timestamps = set()

    for sym, df in stock_dfs.items():
        c = df["close"]
        h = df["high"]
        l = df["low"]
        v = df["volume"]

        # EMAs
        ema9 = c.ewm(span=9, adjust=False).mean()
        ema20 = c.ewm(span=20, adjust=False).mean()
        ema50 = c.ewm(span=50, adjust=False).mean()
        ema200 = c.ewm(span=200, adjust=False).mean()

        # ATR (14)
        hl = h - l
        hc = np.abs(h - c.shift())
        lc = np.abs(l - c.shift())
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        atr = tr.rolling(window=14).mean().fillna(c * 0.02)

        # Volume
        vol_sma20 = v.rolling(window=20).mean()
        vol_surge = (v / vol_sma20.replace(0, np.nan)).fillna(1.0)

        # 1. Baseline Indicators: Golden Cross & Chandelier (22, 2.3)
        gc_20_50 = (ema20.shift(1) <= ema50.shift(1)) & (ema20 > ema50)

        # Chandelier Exit
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

        # 2. Strategy A Indicators: Kinetic Pullback Engine (KPE)
        # Pullback into 20 EMA in an uptrend (Close > EMA50 > EMA200)
        # Low within 1.0% of EMA20, RSI hook below 45
        delta = c.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss.replace(0, np.nan))
        rsi14 = (100 - (100 / (1 + rs))).fillna(50)

        dist_to_ema20 = (c - ema20) / ema20
        # Pullback touch: low dropped near or below 20 EMA, but close is above 20 EMA
        is_uptrend = (ema50 > ema200) & (c > ema200)
        pullback_touch = (l <= ema20 * 1.01) & (c >= ema20 * 0.985) & (rsi14 < 48)
        # Bullish confirmation candle
        bullish_confirm = (c > df["open"]) & (c > c.shift(1))
        kpe_signal = is_uptrend & pullback_touch & bullish_confirm

        # 3. Strategy B Indicators: Volatility Squeeze Breakout (VSB)
        # Bollinger Bands (20, 2.0)
        sma20 = c.rolling(window=20).mean()
        std20 = c.rolling(window=20).std()
        bb_upper = sma20 + (2.0 * std20)
        bb_lower = sma20 - (2.0 * std20)

        # Keltner Channels (20, 1.5 ATR)
        kc_upper = sma20 + (1.5 * atr)
        kc_lower = sma20 - (1.5 * atr)

        # Squeeze On: BB is completely inside KC
        squeeze_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)
        prev_squeeze = squeeze_on.shift(1) | squeeze_on.shift(2) | squeeze_on.shift(3)
        # Squeeze Fired: BB expands outside KC
        squeeze_fired = prev_squeeze & (~squeeze_on)
        # Breakout impulse: Close > BB Upper + Volume surge
        vsb_signal = squeeze_fired & (c > bb_upper.shift(1)) & (vol_surge >= 1.25) & (c > ema200)

        # Donchian 10-period low for trailing runner
        donchian10_low = l.rolling(window=10).min()

        records = {}
        for idx, row in df.iterrows():
            ts = row["timestamp"]
            all_timestamps.add(ts)
            records[ts] = {
                "close": row["close"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "volume": row["volume"],
                "atr": atr.iloc[idx],
                "vol_surge": vol_surge.iloc[idx],
                "ema20": ema20.iloc[idx],
                "ema50": ema50.iloc[idx],
                "ema200": ema200.iloc[idx],
                "gc_20_50": gc_20_50.iloc[idx],
                "ch_dir": ch_dir[idx],
                "ch_stop": long_stop[idx],
                "kpe_signal": kpe_signal.iloc[idx],
                "vsb_signal": vsb_signal.iloc[idx],
                "donchian10_low": donchian10_low.iloc[idx]
            }

        data[sym] = records

    return data, sorted(list(all_timestamps))


# =============================================================================
# 1. SIMULATOR: BASELINE 4H GOLDEN CROSS (CURRENT STREAMLIT ENGINE)
# =============================================================================
def simulate_baseline(data, all_ts, initial_cap=100000.0, max_positions=3, alloc_pct=0.30):
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_positions)
    cash = initial_cap
    positions = {}
    trades = []
    equity_curve = []

    for ts in all_ts:
        # Equity
        curr_eq = cash
        for s, p in positions.items():
            if ts in data[s]:
                curr_eq += p["qty"] * data[s][ts]["close"]
            else:
                curr_eq += p["qty"] * p["entry_px"]
        equity_curve.append(curr_eq)

        # Exits: Chandelier flip or 2.3x ATR initial SL
        to_close = []
        for s, p in positions.items():
            if ts not in data[s]: continue
            bar = data[s][ts]
            close = bar["close"]
            low = bar["low"]

            exit_px = None
            reason = None

            if low <= p["sl"]:
                exit_px = p["sl"]
                reason = "Hard_SL_2.3x"
            elif bar["ch_dir"] == -1:
                exit_px = close
                reason = "Chandelier_Exit"

            if exit_px is not None:
                gross_buy = p["qty"] * p["entry_px"]
                gross_sell = p["qty"] * exit_px
                fric = engine.calculate_friction(gross_buy, True) + engine.calculate_friction(gross_sell, False)
                net = (gross_sell - gross_buy) - fric
                cash += (gross_sell - engine.calculate_friction(gross_sell, False))
                trades.append({
                    "strategy": "Baseline (Golden Cross)",
                    "symbol": s,
                    "entry_time": p["entry_time"],
                    "exit_time": ts,
                    "entry_px": p["entry_px"],
                    "exit_px": exit_px,
                    "pnl": net,
                    "return_pct": ((exit_px - p["entry_px"]) / p["entry_px"]) * 100.0,
                    "reason": reason
                })
                to_close.append(s)

        for s in to_close:
            del positions[s]

        # Entries: 20/50 Golden Cross + Price > 200 EMA + Volume Surge
        if len(positions) < max_positions and cash > 10000:
            candidates = []
            for s, series in data.items():
                if s in positions or ts not in series: continue
                b = series[ts]
                if b["gc_20_50"] and b["close"] > b["ema200"] and b["vol_surge"] >= 0.8:
                    candidates.append((s, b["close"], b["atr"], b["vol_surge"]))

            # Sort by volume surge
            candidates.sort(key=lambda x: x[3], reverse=True)

            for s, c, atr, vs in candidates:
                if len(positions) >= max_positions or cash < 10000: break
                trade_alloc = min(curr_eq * alloc_pct, cash)
                entry_px = c * 1.002
                qty = int(trade_alloc // entry_px)
                if qty <= 0: continue
                gross_buy = qty * entry_px
                fric = engine.calculate_friction(gross_buy, True)
                if cash >= (gross_buy + fric):
                    cash -= (gross_buy + fric)
                    sl = entry_px - (2.3 * atr)
                    positions[s] = {
                        "entry_time": ts,
                        "entry_px": entry_px,
                        "qty": qty,
                        "sl": sl
                    }

    return compute_stats("Baseline (Golden Cross 4H)", initial_cap, equity_curve, trades)


# =============================================================================
# 2. SIMULATOR: SUPERIOR STRATEGY A - KINETIC PULLBACK ENGINE (KPE)
# =============================================================================
def simulate_kpe(data, all_ts, initial_cap=100000.0, max_positions=3, alloc_pct=0.30):
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_positions)
    cash = initial_cap
    positions = {}
    trades = []
    equity_curve = []

    for ts in all_ts:
        curr_eq = cash
        for s, p in positions.items():
            if ts in data[s]:
                curr_eq += p["qty"] * data[s][ts]["close"]
            else:
                curr_eq += p["qty"] * p["entry_px"]
        equity_curve.append(curr_eq)

        # Exits: Asymmetric 2-Stage Profit Scaling
        to_close = []
        for s, p in positions.items():
            if ts not in data[s]: continue
            bar = data[s][ts]
            close = bar["close"]
            high = bar["high"]
            low = bar["low"]
            entry = p["entry_px"]
            atr = p["atr"]

            # 1. Check Stage 1 Partial Profit (Take 50% at +1.8x ATR, move SL to Cost + 0.3%)
            if not p["scaled_out"]:
                target_1 = entry + (1.8 * atr)
                if high >= target_1:
                    p["scaled_out"] = True
                    p["sl"] = entry * 1.003  # Lock profit/cost (Break-Even)
                    scale_qty = int(p["qty"] * 0.5)
                    if scale_qty > 0:
                        gross_sell = scale_qty * target_1
                        gross_buy = scale_qty * entry
                        fric = engine.calculate_friction(gross_buy, True) + engine.calculate_friction(gross_sell, False)
                        net = (gross_sell - gross_buy) - fric
                        cash += (gross_sell - engine.calculate_friction(gross_sell, False))
                        trades.append({
                            "strategy": "KPE (Pullback)",
                            "symbol": s,
                            "entry_time": p["entry_time"],
                            "exit_time": ts,
                            "entry_px": entry,
                            "exit_px": target_1,
                            "pnl": net,
                            "return_pct": ((target_1 - entry) / entry) * 100.0,
                            "reason": "Target1_Scaleout_50%"
                        })
                        p["qty"] -= scale_qty

            # 2. Check Stop Loss
            exit_px = None
            reason = None
            if low <= p["sl"]:
                exit_px = p["sl"]
                reason = "Break_Even_SL" if p["scaled_out"] else "Tight_Swing_SL_1.3x"
            # 3. Runner Exit: 20 EMA close breakdown
            elif p["scaled_out"] and close < bar["ema20"]:
                exit_px = close
                reason = "EMA20_Runner_Exit"

            if exit_px is not None and p["qty"] > 0:
                gross_buy = p["qty"] * entry
                gross_sell = p["qty"] * exit_px
                fric = engine.calculate_friction(gross_buy, True) + engine.calculate_friction(gross_sell, False)
                net = (gross_sell - gross_buy) - fric
                cash += (gross_sell - engine.calculate_friction(gross_sell, False))
                trades.append({
                    "strategy": "KPE (Pullback)",
                    "symbol": s,
                    "entry_time": p["entry_time"],
                    "exit_time": ts,
                    "entry_px": entry,
                    "exit_px": exit_px,
                    "pnl": net,
                    "return_pct": ((exit_px - entry) / entry) * 100.0,
                    "reason": reason
                })
                to_close.append(s)

        for s in to_close:
            del positions[s]

        # Entries: Kinetic Pullback Engine
        if len(positions) < max_positions and cash > 10000:
            candidates = []
            for s, series in data.items():
                if s in positions or ts not in series: continue
                b = series[ts]
                if b["kpe_signal"]:
                    # Rank candidates by trend alignment (Close / EMA200)
                    trend_score = b["close"] / b["ema200"]
                    candidates.append((s, b["close"], b["atr"], trend_score))

            candidates.sort(key=lambda x: x[3], reverse=True)

            for s, c, atr, score in candidates:
                if len(positions) >= max_positions or cash < 10000: break
                trade_alloc = min(curr_eq * alloc_pct, cash)
                entry_px = c * 1.002
                qty = int(trade_alloc // entry_px)
                if qty <= 0: continue
                gross_buy = qty * entry_px
                fric = engine.calculate_friction(gross_buy, True)
                if cash >= (gross_buy + fric):
                    cash -= (gross_buy + fric)
                    # Tight initial SL: 1.3x ATR below entry
                    sl = entry_px - (1.3 * atr)
                    positions[s] = {
                        "entry_time": ts,
                        "entry_px": entry_px,
                        "qty": qty,
                        "atr": atr,
                        "sl": sl,
                        "scaled_out": False
                    }

    return compute_stats("Superior A: Kinetic Pullback (KPE)", initial_cap, equity_curve, trades)


# =============================================================================
# 3. SIMULATOR: SUPERIOR STRATEGY B - VOLATILITY SQUEEZE BREAKOUT (VSB)
# =============================================================================
def simulate_vsb(data, all_ts, initial_cap=100000.0, max_positions=3, alloc_pct=0.30):
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_positions)
    cash = initial_cap
    positions = {}
    trades = []
    equity_curve = []

    for ts in all_ts:
        curr_eq = cash
        for s, p in positions.items():
            if ts in data[s]:
                curr_eq += p["qty"] * data[s][ts]["close"]
            else:
                curr_eq += p["qty"] * p["entry_px"]
        equity_curve.append(curr_eq)

        # Exits: 1.5x ATR Target 1 + Donchian Trail Runner
        to_close = []
        for s, p in positions.items():
            if ts not in data[s]: continue
            bar = data[s][ts]
            close = bar["close"]
            high = bar["high"]
            low = bar["low"]
            entry = p["entry_px"]
            atr = p["atr"]

            # Scale out 50% at 2.0x ATR
            if not p["scaled_out"]:
                target_1 = entry + (2.0 * atr)
                if high >= target_1:
                    p["scaled_out"] = True
                    p["sl"] = entry * 1.002  # Break-even
                    scale_qty = int(p["qty"] * 0.5)
                    if scale_qty > 0:
                        gross_sell = scale_qty * target_1
                        gross_buy = scale_qty * entry
                        fric = engine.calculate_friction(gross_buy, True) + engine.calculate_friction(gross_sell, False)
                        net = (gross_sell - gross_buy) - fric
                        cash += (gross_sell - engine.calculate_friction(gross_sell, False))
                        trades.append({
                            "strategy": "VSB (Squeeze)",
                            "symbol": s,
                            "entry_time": p["entry_time"],
                            "exit_time": ts,
                            "entry_px": entry,
                            "exit_px": target_1,
                            "pnl": net,
                            "return_pct": ((target_1 - entry) / entry) * 100.0,
                            "reason": "Target1_Scaleout_50%"
                        })
                        p["qty"] -= scale_qty

            exit_px = None
            reason = None

            # Hard/BE Stop
            if low <= p["sl"]:
                exit_px = p["sl"]
                reason = "Break_Even_SL" if p["scaled_out"] else "Squeeze_Stop_1.5x"
            # Donchian 10-bar trail for runner
            elif p["scaled_out"] and low <= bar["donchian10_low"]:
                exit_px = bar["donchian10_low"]
                reason = "Donchian10_Trail_Exit"

            if exit_px is not None and p["qty"] > 0:
                gross_buy = p["qty"] * entry
                gross_sell = p["qty"] * exit_px
                fric = engine.calculate_friction(gross_buy, True) + engine.calculate_friction(gross_sell, False)
                net = (gross_sell - gross_buy) - fric
                cash += (gross_sell - engine.calculate_friction(gross_sell, False))
                trades.append({
                    "strategy": "VSB (Squeeze)",
                    "symbol": s,
                    "entry_time": p["entry_time"],
                    "exit_time": ts,
                    "entry_px": entry,
                    "exit_px": exit_px,
                    "pnl": net,
                    "return_pct": ((exit_px - entry) / entry) * 100.0,
                    "reason": reason
                })
                to_close.append(s)

        for s in to_close:
            del positions[s]

        # Entries: Volatility Squeeze Breakout with Volatility Parity Sizing
        if len(positions) < max_positions and cash > 10000:
            candidates = []
            for s, series in data.items():
                if s in positions or ts not in series: continue
                b = series[ts]
                if b["vsb_signal"]:
                    candidates.append((s, b["close"], b["atr"], b["vol_surge"]))

            candidates.sort(key=lambda x: x[3], reverse=True)

            for s, c, atr, vs in candidates:
                if len(positions) >= max_positions or cash < 10000: break
                entry_px = c * 1.002
                # Volatility-parity: Risk exactly 1.2% of equity per trade
                risk_per_share = 1.5 * atr
                target_risk_rs = curr_eq * 0.012
                qty_by_risk = int(target_risk_rs // risk_per_share) if risk_per_share > 0 else 0
                qty_by_cash = int((curr_eq * alloc_pct) // entry_px)
                qty = max(1, min(qty_by_risk, qty_by_cash))
                
                gross_buy = qty * entry_px
                fric = engine.calculate_friction(gross_buy, True)
                if cash >= (gross_buy + fric):
                    cash -= (gross_buy + fric)
                    sl = entry_px - (1.5 * atr)
                    positions[s] = {
                        "entry_time": ts,
                        "entry_px": entry_px,
                        "qty": qty,
                        "atr": atr,
                        "sl": sl,
                        "scaled_out": False
                    }

    return compute_stats("Superior B: Volatility Squeeze (VSB)", initial_cap, equity_curve, trades)


def compute_stats(name, initial_cap, equity_curve, trades):
    final_eq = equity_curve[-1] if equity_curve else initial_cap
    total_net = final_eq - initial_cap
    roi_pct = (total_net / initial_cap) * 100.0

    eq_series = pd.Series(equity_curve)
    peak = eq_series.cummax()
    dd_series = (eq_series - peak) / peak * 100.0
    max_dd = abs(float(dd_series.min())) if len(dd_series) > 0 else 0.0

    tdf = pd.DataFrame(trades)
    n_trades = len(tdf)
    if n_trades == 0:
        return {"name": name, "trades": 0, "net_profit": 0, "roi": 0, "win_rate": 0, "pf": 0, "max_dd": 0, "calmar": 0, "sharpe": 0}

    wins = tdf[tdf["pnl"] > 0]
    losses = tdf[tdf["pnl"] <= 0]
    win_rate = (len(wins) / n_trades) * 100.0

    gross_win = wins["pnl"].sum()
    gross_loss = abs(losses["pnl"].sum())
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else 999.0

    expectancy = tdf["pnl"].mean()
    avg_win = wins["pnl"].mean() if len(wins) > 0 else 0
    avg_loss = abs(losses["pnl"].mean()) if len(losses) > 0 else 1.0
    payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0

    # Annualized Sharpe & Calmar (~6 years, 2020-2026)
    years = 6.8
    cagr = ((final_eq / initial_cap) ** (1.0 / years) - 1.0) * 100.0
    calmar = (cagr / max_dd) if max_dd > 0 else 0.0

    pnl_series = tdf["pnl"]
    sharpe = (pnl_series.mean() / pnl_series.std()) * np.sqrt(n_trades / years) if pnl_series.std() > 0 else 0.0

    return {
        "name": name,
        "initial_cap": initial_cap,
        "final_equity": final_eq,
        "net_profit": total_net,
        "roi_pct": roi_pct,
        "cagr_pct": cagr,
        "trades": n_trades,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "expectancy_rs": expectancy,
        "payoff_ratio": payoff_ratio,
        "max_drawdown": max_dd,
        "calmar_ratio": calmar,
        "sharpe_ratio": sharpe,
        "trades_df": tdf
    }


def main():
    print("=" * 80)
    print("🏛️ INSTITUTIONAL QUANT TOURNAMENT: BASELINE VS SUPERIOR STRATEGIES")
    print("=" * 80)

    stock_dfs = load_dataset()
    data, all_ts = precalculate_indicators(stock_dfs)

    print("\nRunning Backtest 1: Baseline 4H Golden Cross + Dual Chandelier...")
    res_base = simulate_baseline(data, all_ts)

    print("Running Backtest 2: Superior Strategy A - Kinetic Pullback Engine (KPE)...")
    res_kpe = simulate_kpe(data, all_ts)

    print("Running Backtest 3: Superior Strategy B - Volatility Squeeze Breakout (VSB)...")
    res_vsb = simulate_vsb(data, all_ts)

    results = [res_base, res_kpe, res_vsb]

    print("\n" + "=" * 90)
    print(f"{'Strategy Architecture':<36} | {'Final Equity':<12} | {'CAGR %':<7} | {'Win Rate':<8} | {'PF':<5} | {'Max DD%':<8} | {'Calmar':<6} | {'Sharpe'}")
    print("=" * 90)
    for r in results:
        print(f"{r['name']:<36} | ₹{r['final_equity']:>10,.0f} | {r['cagr_pct']:>6.1f}% | {r['win_rate']:>7.1f}% | {r['profit_factor']:>5.2f} | {r['max_drawdown']:>7.1f}% | {r['calmar_ratio']:>6.2f} | {r['sharpe_ratio']:>6.2f}")
    print("-" * 90)

    # Save detailed CSVs
    for r in results:
        fname = f"trades_{r['name'][:10].strip().replace(' ', '_').lower()}.csv"
        r["trades_df"].to_csv(fname, index=False)
        print(f"Saved trade log to {fname}")

    return results


if __name__ == "__main__":
    main()
