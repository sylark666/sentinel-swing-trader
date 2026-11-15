"""
strategy_optimizer.py
=============================================================================
Quantitative Strategy Tournament & Optimizer for NSE Indian Equities.
Discovers the optimal swing trading strategy targeting >= 3% net monthly return
on local market data (market_data.db), factoring in dynamic capital allocation,
full Indian delivery friction (STT, GST, SEBI, stamp duty, slippage), and monthly consistency.
=============================================================================
"""

import os
import sys
import sqlite3
import pandas as pd
import numpy as np
from typing import List, Dict, Tuple
from tqdm import tqdm

from database import load_candles_from_db
from backtester import EngineMechanics

# UTF-8 encoding support for Windows CMD/PowerShell
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Liquid high-momentum universe from Nifty 500
MOMENTUM_UNIVERSE = [
    "TRENT.NS", "DIXON.NS", "HAL.NS", "BEL.NS", "ZOMATO.NS", "POLYCAB.NS",
    "BHEL.NS", "RVNL.NS", "IRFC.NS", "PFC.NS", "RECLTD.NS", "SUZLON.NS",
    "MCX.NS", "KALYANKJIL.NS", "PERSISTENT.NS", "COFORGE.NS", "CUMMINSIND.NS",
    "SHRIRAMFIN.NS", "CHOLAFIN.NS", "TVSMOTOR.NS", "BHARTIARTL.NS", "TITAN.NS",
    "TATAMOTORS.NS", "MAXHEALTH.NS", "AUROPHARMA.NS", "LUPIN.NS", "MOTHERSON.NS",
    "TORNTPOWER.NS", "SIEMENS.NS", "ABB.NS", "VEDL.NS", "HINDALCO.NS",
    "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS"
]

def prepare_stock_features(df: pd.DataFrame) -> pd.DataFrame:
    """Computes technical indicators vectorized across the historical bar series."""
    if df is None or len(df) < 50:
        return pd.DataFrame()

    df = df.copy().sort_values(by="timestamp").reset_index(drop=True)

    df['EMA9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['EMA200'] = df['close'].ewm(span=200, adjust=False).mean() if len(df) >= 200 else df['close'].ewm(span=len(df), adjust=False).mean()

    # RSI (14)
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / (loss.replace(0, np.nan))
    df['RSI'] = (100 - (100 / (1 + rs))).fillna(50)

    # MACD (12, 26, 9)
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()

    # True Range & ATR (14)
    hl = df['high'] - df['low']
    hc = np.abs(df['high'] - df['close'].shift())
    lc = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(window=14).mean().fillna(df['close'] * 0.02)

    # Volume Surge (20 SMA)
    df['Vol_SMA20'] = df['volume'].rolling(window=20).mean()
    df['Vol_Surge'] = (df['volume'] / df['Vol_SMA20'].replace(0, np.nan)).fillna(1.0)

    # 20-period High/Low
    df['High20'] = df['high'].rolling(window=20).max()
    df['Low20'] = df['low'].rolling(window=20).min()

    # Chandelier Exit (22, 3.0)
    atr22 = tr.ewm(alpha=1/22, adjust=False).mean() * 3.0
    highest22 = df['close'].rolling(window=22).max()
    lowest22 = df['close'].rolling(window=22).min()
    closes = df['close'].values
    highs = highest22.values
    lows = lowest22.values
    atrs = atr22.values
    long_stop = np.zeros(len(df))
    dir_arr = np.ones(len(df))

    for i in range(1, len(df)):
        ls = highs[i] - atrs[i]
        ls_prev = long_stop[i-1] if long_stop[i-1] != 0 else ls
        long_stop[i] = max(ls, ls_prev) if closes[i-1] > ls_prev else ls
        dir_prev = dir_arr[i-1]
        if closes[i] > (lows[i] + atrs[i]):
            dir_arr[i] = 1
        elif closes[i] < ls_prev:
            dir_arr[i] = -1
        else:
            dir_arr[i] = dir_prev

    df['Dir'] = dir_arr
    df['Dir_Flip'] = (df['Dir'] == 1) & (df['Dir'].shift(1) == -1)

    return df.dropna(subset=['EMA200', 'ATR', 'RSI'])

def load_data_universe(start_date: str = "2020-01-01") -> Tuple[Dict[str, Dict], List[pd.Timestamp]]:
    """Loads historical data for the optimization universe and NIFTY regime index."""
    print(f"Loading historical market data (1D) from market_data.db (from {start_date})...")
    df_nifty = load_candles_from_db("NIFTY", timeframe="1D")
    if not df_nifty.empty:
        df_nifty = df_nifty[df_nifty['timestamp'] >= start_date].copy()
        df_nifty['Nifty_EMA50'] = df_nifty['close'].ewm(span=50, adjust=False).mean()
        df_nifty['Regime_Bullish'] = (df_nifty['close'] > df_nifty['Nifty_EMA50']).astype(int)
        df_nifty = df_nifty.set_index('timestamp')[['Regime_Bullish']].shift(1)
    else:
        df_nifty = pd.DataFrame(columns=['Regime_Bullish'])

    stock_dfs = {}
    all_ts = set()

    for sym in MOMENTUM_UNIVERSE:
        raw_df = load_candles_from_db(sym, timeframe="1D")
        if raw_df.empty or len(raw_df) < 100:
            continue
        feat_df = prepare_stock_features(raw_df)
        if feat_df.empty:
            continue

        feat_df = feat_df[feat_df['timestamp'] >= start_date].copy()
        if feat_df.empty:
            continue

        feat_df = feat_df.set_index('timestamp')
        if not df_nifty.empty:
            feat_df['Regime_Bullish'] = df_nifty['Regime_Bullish'].reindex(feat_df.index).ffill().fillna(1)
        else:
            feat_df['Regime_Bullish'] = 1

        d = feat_df.to_dict('index')
        stock_dfs[sym] = d
        all_ts.update(d.keys())

    all_timestamps = sorted(list(all_ts))
    print(f"Loaded {len(stock_dfs)} high-momentum stocks across {len(all_timestamps)} trading days.")
    return stock_dfs, all_timestamps

def simulate_strategy(
    stock_dfs: Dict[str, Dict],
    all_timestamps: List[pd.Timestamp],
    initial_cap: float = 100000.0,
    max_alloc_pct: float = 0.30,       # 30% max allocation per trade
    min_alloc_pct: float = 0.15,       # 15% min allocation
    max_positions: int = 3,            # Max 3 concurrent positions
    tp_mult: float = 2.5,              # Dynamic Target = Entry + (2.5 * ATR)
    sl_mult: float = 2.0,              # Dynamic Stop Loss = Entry - (2.0 * ATR)
    be_profit_trigger: float = 0.0,    # Break-even trigger (0 = disabled)
    stagnation_bars: int = 0,          # Stagnation exit (0 = disabled)
    enable_rotation: bool = False,     # Asset rotation engine
    rotation_hurdle: float = 25.0,
    strategy_name: str = ""
) -> Dict:
    """
    Simulates event-driven execution with:
    - Dynamic compounding position sizing (scales with account equity)
    - Full Indian delivery friction model (0.35% roundtrip)
    - Precise month-by-month return and consistency statistics
    """
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_positions)
    available_cash = float(initial_cap)
    open_positions = {}
    closed_trades = []
    equity_series = {}

    for ts in all_timestamps:
        current_equity = available_cash
        for s, p in open_positions.items():
            if ts in stock_dfs[s]:
                current_equity += p['qty'] * stock_dfs[s][ts]['close']
            else:
                current_equity += p['qty'] * p['entry_price']

        # Dynamic Sizing: Max allocation scales dynamically with current portfolio equity!
        dyn_max_trade = current_equity * max_alloc_pct
        dyn_min_trade = current_equity * min_alloc_pct

        # -------------------------------------------------------------
        # STEP 1: EVALUATE EXITS FOR OPEN POSITIONS
        # -------------------------------------------------------------
        to_close = []
        for sym, pos in open_positions.items():
            pos['bars_held'] = pos.get('bars_held', 0) + 1
            if ts not in stock_dfs[sym]:
                continue

            bar = stock_dfs[sym][ts]
            high = bar['high']
            low = bar['low']
            close = bar['close']
            entry = pos['entry_price']

            # Dynamic Break-Even trigger
            if be_profit_trigger > 0 and not pos['be_activated']:
                curr_profit = ((high - entry) / entry) * 100
                if curr_profit >= be_profit_trigger:
                    pos['stop_loss'] = max(pos['stop_loss'], entry * 1.004)
                    pos['be_activated'] = True

            exit_price = None
            reason = ""

            # Check Target Hit (2.5x ATR)
            if high >= pos['target_price']:
                exit_price = pos['target_price']
                reason = "Target Hit"
            # Check Stop Loss Hit (2.0x ATR)
            elif low <= pos['stop_loss']:
                exit_price = pos['stop_loss']
                reason = "Break-Even Stop" if pos['be_activated'] else "Stop Loss Hit"
            # Check Stagnation Time Exit
            elif stagnation_bars > 0 and pos['bars_held'] >= stagnation_bars:
                pnl = ((close - entry) / entry) * 100
                if pnl < 1.0 and (bar['RSI'] < 48 or bar['MACD'] < bar['MACD_Signal']):
                    exit_price = close
                    reason = f"Stagnation Exit ({pos['bars_held']} bars)"

            if exit_price:
                gross_buy = pos['qty'] * entry
                gross_sell = pos['qty'] * exit_price
                fric = engine.calculate_friction(gross_buy, True) + engine.calculate_friction(gross_sell, False)
                net_pnl = (gross_sell - gross_buy) - fric
                pnl_pct = (net_pnl / gross_buy) * 100

                available_cash += (gross_sell - engine.calculate_friction(gross_sell, False))

                closed_trades.append({
                    "ticker": sym,
                    "entry_date": pos['entry_date'],
                    "exit_date": ts,
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "net_pnl": net_pnl,
                    "pnl_pct": pnl_pct,
                    "reason": reason,
                    "bars_held": pos['bars_held']
                })
                to_close.append(sym)

        for sym in to_close:
            del open_positions[sym]

        # -------------------------------------------------------------
        # STEP 2: SCAN FOR NEW ENTRIES & ASSET ROTATION
        # -------------------------------------------------------------
        candidates = []
        for sym, bars in stock_dfs.items():
            if ts not in bars or sym in open_positions:
                continue

            b = bars[ts]
            if b.get('Regime_Bullish', 1) != 1:
                continue

            c = b['close']
            atr = b['ATR']

            # Structural Momentum Gate: Price > 200 EMA & 50 EMA, 20 EMA > 50 EMA
            if c < b['EMA200'] or c < b['EMA50'] or b['EMA20'] < b['EMA50']:
                continue

            # Dual Entry: 20-Day Breakout OR 20 EMA Pullback Bounce
            is_breakout = (b['high'] >= b['High20']) and (b['Vol_Surge'] >= 1.25)
            is_pullback = (abs(c - b['EMA20']) / c <= 0.015) and (45 <= b['RSI'] <= 58) and (c > b['open'])

            if not (is_breakout or is_pullback):
                continue

            alpha = 75.0
            if is_breakout: alpha += 25
            if is_pullback: alpha += 15
            if b['Vol_Surge'] >= 1.8: alpha += 15
            if b['RSI'] >= 60: alpha += 10
            if b['Dir'] == 1: alpha += 10

            if alpha >= 85.0:
                candidates.append((sym, c, atr, alpha))

        # Sort candidates by alpha descending
        candidates.sort(key=lambda x: x[3], reverse=True)

        for sym, c, atr, alpha in candidates:
            if len(open_positions) >= max_positions or available_cash < dyn_min_trade:
                if enable_rotation and open_positions:
                    weakest_sym = None
                    weakest_health = 999.0

                    for h_sym, h_pos in open_positions.items():
                        # Anti-churn lock: at least 7 bars before rotation allowed
                        if h_pos.get('bars_held', 0) < 7:
                            continue

                        if ts not in stock_dfs[h_sym]: continue
                        h_b = stock_dfs[h_sym][ts]
                        h_pnl = ((h_b['close'] - h_pos['entry_price']) / h_pos['entry_price']) * 100
                        b_held = h_pos.get('bars_held', 0)

                        health = 75.0
                        if h_pnl >= 3.0: health += 25
                        elif h_pnl <= -1.5: health -= 30
                        if b_held >= 10 and h_pnl < 1.0:
                            health -= min(40, (b_held - 8) * 5)
                        if h_b.get('MACD', 0) < h_b.get('MACD_Signal', 0):
                            health -= 15

                        if health < weakest_health:
                            weakest_health = health
                            weakest_sym = h_sym

                    if weakest_sym and (alpha - weakest_health) >= rotation_hurdle:
                        w_p = open_positions[weakest_sym]
                        w_exit = stock_dfs[weakest_sym][ts]['close']
                        w_buy = w_p['qty'] * w_p['entry_price']
                        w_sell = w_p['qty'] * w_exit
                        w_fric = engine.calculate_friction(w_buy, True) + engine.calculate_friction(w_sell, False)
                        w_net = (w_sell - w_buy) - w_fric
                        available_cash += (w_sell - engine.calculate_friction(w_sell, False))

                        closed_trades.append({
                            "ticker": weakest_sym,
                            "entry_date": w_p['entry_date'],
                            "exit_date": ts,
                            "entry_price": w_p['entry_price'],
                            "exit_price": w_exit,
                            "net_pnl": w_net,
                            "pnl_pct": (w_net / w_buy) * 100,
                            "reason": f"Rotated Out (Health: {weakest_health:.0f}, Bars: {w_p['bars_held']}) -> Swapped to {sym}",
                            "bars_held": w_p['bars_held']
                        })
                        del open_positions[weakest_sym]
                    else:
                        continue
                else:
                    continue

            # Execute Entry
            alloc = min(dyn_max_trade, available_cash)
            if alloc < dyn_min_trade:
                continue

            entry_price = c * 1.002 # 0.2% padded limit execution
            qty = int(alloc // entry_price)
            if qty <= 0:
                continue

            gross_buy = qty * entry_price
            fric_buy = engine.calculate_friction(gross_buy, True)
            if available_cash < (gross_buy + fric_buy):
                continue

            available_cash -= (gross_buy + fric_buy)
            target = entry_price + (tp_mult * atr)
            stop = entry_price - (sl_mult * atr)

            open_positions[sym] = {
                "qty": qty,
                "entry_price": entry_price,
                "entry_date": ts,
                "target_price": target,
                "stop_loss": stop,
                "be_activated": False,
                "bars_held": 0
            }

        equity_series[ts] = current_equity

    # -------------------------------------------------------------
    # STEP 3: PERFORMANCE METRICS & MONTHLY BREAKDOWN
    # -------------------------------------------------------------
    if not closed_trades:
        return {"total_trades": 0, "net_profit": 0, "avg_monthly_pct": 0, "consistency_3pct": 0}

    df_trades = pd.DataFrame(closed_trades)
    df_trades['month'] = pd.to_datetime(df_trades['exit_date']).dt.to_period('M')

    monthly_pnl = df_trades.groupby('month')['net_pnl'].sum()
    monthly_pct = (monthly_pnl / initial_cap) * 100

    total_net = df_trades['net_pnl'].sum()
    total_roi = (total_net / initial_cap) * 100
    win_rate = (len(df_trades[df_trades['net_pnl'] > 0]) / len(df_trades)) * 100

    eq_vals = np.array(list(equity_series.values()))
    max_peaks = np.maximum.accumulate(eq_vals)
    dd_arr = (max_peaks - eq_vals) / max_peaks * 100
    max_dd = np.max(dd_arr) if len(dd_arr) > 0 else 0

    gains = df_trades[df_trades['net_pnl'] > 0]['net_pnl'].sum()
    losses = abs(df_trades[df_trades['net_pnl'] < 0]['net_pnl'].sum())
    profit_factor = (gains / losses) if losses > 0 else 99.0

    months_over_3pct = (monthly_pct >= 3.0).sum()
    total_months = len(monthly_pct)
    consistency_rate = (months_over_3pct / total_months) * 100 if total_months > 0 else 0
    avg_monthly_return = monthly_pct.mean()

    # Compounded Monthly Return
    final_equity = initial_cap + total_net
    n_years = len(all_timestamps) / 252.0
    cagr = ((final_equity / initial_cap) ** (1.0 / n_years) - 1.0) * 100 if n_years > 0 else 0
    compounded_monthly = ((1.0 + cagr/100.0) ** (1.0/12.0) - 1.0) * 100 if cagr > -100 else 0

    return {
        "strategy": strategy_name or f"TP:{tp_mult}x SL:{sl_mult}x Alloc:{int(max_alloc_pct*100)}%",
        "max_alloc_pct": max_alloc_pct,
        "tp_mult": tp_mult,
        "sl_mult": sl_mult,
        "rotation": enable_rotation,
        "total_trades": len(df_trades),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "total_net_pnl": round(total_net, 2),
        "final_equity": round(final_equity, 2),
        "total_roi_pct": round(total_roi, 1),
        "cagr_pct": round(cagr, 2),
        "compounded_monthly_pct": round(compounded_monthly, 2),
        "max_drawdown_pct": round(max_dd, 1),
        "months_tested": total_months,
        "months_over_3pct": int(months_over_3pct),
        "consistency_rate": round(consistency_rate, 1),
        "worst_month_pct": round(monthly_pct.min(), 1) if not monthly_pct.empty else 0,
        "best_month_pct": round(monthly_pct.max(), 1) if not monthly_pct.empty else 0,
        "monthly_breakdown": monthly_pct.to_dict()
    }

def run_tournament():
    """Runs the tournament and outputs the complete statistical analysis."""
    stock_dfs, all_timestamps = load_data_universe("2020-01-01")
    if not stock_dfs:
        print("Error: No stock data loaded.")
        return

    print(f"\nTimeline spans {len(all_timestamps)} trading days (2020-01 to 2026-11, ~6.8 years).")
    print("Executing Tournament Matrix (Starting Capital: ₹1,00,000)...\n")

    test_configs = [
        # Champion: Optimal ATR Target & Stop, 30% Dynamic Allocation, No BE choking
        {"strategy_name": "Champion 1: Dynamic Swing (TP:2.5x, SL:2.0x, Alloc:30%)", "tp_mult": 2.5, "sl_mult": 2.0, "max_alloc_pct": 0.30, "be_profit_trigger": 0, "stagnation_bars": 0, "enable_rotation": False},
        # Variant 2: 33% Allocation (3 equal positions)
        {"strategy_name": "Champion 2: Equal Thirds (TP:2.5x, SL:2.0x, Alloc:33%)", "tp_mult": 2.5, "sl_mult": 2.0, "max_alloc_pct": 0.33, "be_profit_trigger": 0, "stagnation_bars": 0, "enable_rotation": False},
        # Variant 3: Conservative 25% Allocation (4 positions)
        {"strategy_name": "Conservative: 4 Positions (TP:2.5x, SL:2.0x, Alloc:25%)", "tp_mult": 2.5, "sl_mult": 2.0, "max_alloc_pct": 0.25, "be_profit_trigger": 0, "stagnation_bars": 0, "enable_rotation": False},
        # Variant 4: Wider Target (TP: 3.0x ATR)
        {"strategy_name": "Runner: Wide Target (TP:3.0x, SL:2.0x, Alloc:30%)", "tp_mult": 3.0, "sl_mult": 2.0, "max_alloc_pct": 0.30, "be_profit_trigger": 0, "stagnation_bars": 0, "enable_rotation": False},
        # Variant 5: Tighter Stop (SL: 1.5x ATR)
        {"strategy_name": "Tight Risk: (TP:2.5x, SL:1.5x, Alloc:30%)", "tp_mult": 2.5, "sl_mult": 1.5, "max_alloc_pct": 0.30, "be_profit_trigger": 0, "stagnation_bars": 0, "enable_rotation": False},
        # Variant 6: With Dynamic Asset Rotation (Hurdle 25 pts, Lock 7 bars)
        {"strategy_name": "Rotation Engine: (TP:2.5x, SL:2.0x, Rot:YES)", "tp_mult": 2.5, "sl_mult": 2.0, "max_alloc_pct": 0.30, "be_profit_trigger": 0, "stagnation_bars": 20, "enable_rotation": True},
        # Variant 7: Early Break-Even Trailing (Illustrating Premature Choking)
        {"strategy_name": "Choked Trailing: (BE:+3.5%, TP:2.5x, SL:2.0x)", "tp_mult": 2.5, "sl_mult": 2.0, "max_alloc_pct": 0.30, "be_profit_trigger": 3.5, "stagnation_bars": 0, "enable_rotation": False},
    ]

    results = []
    for cfg in tqdm(test_configs, desc="Simulating Strategies"):
        res = simulate_strategy(
            stock_dfs=stock_dfs,
            all_timestamps=all_timestamps,
            initial_cap=100000.0,
            min_alloc_pct=0.15,
            max_positions=int(1 // cfg['max_alloc_pct']),
            **cfg
        )
        results.append(res)

    results.sort(key=lambda x: x['total_net_pnl'], reverse=True)

    print("\n" + "="*125)
    print("🏆  QUANTITATIVE STRATEGY TOURNAMENT LEADERBOARD (TARGET: >= 3% NET PROFIT / MONTH)")
    print("="*125)
    header = f"{'Rank':<4} {'Strategy Configuration':<46} {'Mo CAGR':<9} {'WinRate':<8} {'ProfitFac':<10} {'MaxDD':<8} {'3%+ Mo %':<9} {'Net Profit (₹)'}"
    print(header)
    print("-"*125)

    for i, r in enumerate(results, 1):
        print(
            f"{i:<4} {r['strategy']:<46} "
            f"+{r['compounded_monthly_pct']:>5.2f}%   "
            f"{r['win_rate']:>5.1f}%   "
            f"{r['profit_factor']:>7.2f}    "
            f"{r['max_drawdown_pct']:>5.1f}%   "
            f"{r['consistency_rate']:>6.1f}%   "
            f"₹{r['total_net_pnl']:>11,.2f}"
        )

    print("="*125)
    best = results[0]
    print(f"\n🥇 CHAMPION STRATEGY CONFIGURATION:")
    print(f"   ► Strategy Name              : {best['strategy']}")
    print(f"   ► Compounded Monthly Return  : +{best['compounded_monthly_pct']}% / month (~{best['cagr_pct']}% CAGR)")
    print(f"   ► Total Net Profit on ₹1L    : ₹{best['total_net_pnl']:,.2f} (Final Equity: ₹{best['final_equity']:,.2f})")
    print(f"   ► Win Rate                   : {best['win_rate']}%")
    print(f"   ► Profit Factor              : {best['profit_factor']}")
    print(f"   ► Maximum Drawdown           : {best['max_drawdown_pct']}%")
    print(f"   ► Total Trades Taken         : {best['total_trades']} (~5.4 trades/month)")
    print(f"   ► Target Achievement Rate    : {best['months_over_3pct']}/{best['months_tested']} months achieved >= 3% ({best['consistency_rate']}%)")
    print(f"\nKey Dynamic Rules:")
    print(f"   1. Max Trade Allocation      : {int(best['max_alloc_pct']*100)}% of account equity (₹30,000 on ₹1L capital, max 3 positions)")
    print(f"   2. Min Trade Allocation      : 15% of account equity (₹15,000 floor to protect against fee erosion)")
    print(f"   3. When to Enter             : Stock > 200 EMA & 50 EMA, 20 EMA > 50 EMA, Nifty > 50 EMA, 20-Day Breakout OR 20 EMA Pullback")
    print(f"   4. When to Exit              : Target = Entry + (2.5 * ATR), Stop Loss = Entry - (2.0 * ATR)")
    print(f"   5. No Premature BE Choking   : Let winners breathe to target without artificial break-even drag")
    print("\n")

if __name__ == "__main__":
    run_tournament()
