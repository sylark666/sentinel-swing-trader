"""
comprehensive_strategy_optimizer.py
Exhaustive Quantitative Strategy Tournament on Local NSE Market Data.
Benchmarks different Entry Models, Exit Paradigms, Dynamic Position Sizing,
and Asset Rotation to find the optimal strategy targeting >= 3% net profit per month.
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

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Select top 75 liquid, high-momentum swing candidates across Nifty 500 sectors
TOP_LIQUID_UNIVERSE = [
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
    "KALYANKJIL.NS", "BOSCHLTD.NS", "CIPLA.NS", "DRREDDY.NS", "LUPIN.NS",
    "APOLLOHOSP.NS", "MAXHEALTH.NS", "INDIGO.NS", "MOTHERSON.NS", "HEROMOTOCO.NS",
    "BAJAJ-AUTO.NS", "BAJAJFINSV.NS", "MUTHOOTFIN.NS", "SBILIFE.NS", "HDFCLIFE.NS",
    "CANBK.NS", "BANKBARODA.NS", "FEDERALBNK.NS", "IDFCFIRSTB.NS", "AUROPHARMA.NS",
    "DIVISLAB.NS", "TORNTPOWER.NS", "TATACOMM.NS", "TATACHEM.NS", "TATACONSUM.NS"
]

def prepare_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Vectorized calculation of technical indicators for swing trading."""
    if df is None or len(df) < 50:
        return pd.DataFrame()

    df = df.copy().sort_values(by="timestamp").reset_index(drop=True)

    # Moving Averages
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

    # ATR (14)
    hl = df['high'] - df['low']
    hc = np.abs(df['high'] - df['close'].shift())
    lc = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(window=14).mean().fillna(df['close'] * 0.02)

    # Volume Surge (20 SMA)
    df['Vol_SMA20'] = df['volume'].rolling(window=20).mean()
    df['Vol_Surge'] = (df['volume'] / df['Vol_SMA20'].replace(0, np.nan)).fillna(1.0)

    # Donchian 20-day High / Low
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
    df['Chandelier_Stop'] = long_stop

    return df.dropna(subset=['EMA200', 'ATR', 'RSI'])

def load_data_dict(start_date: str = "2020-01-01") -> Tuple[Dict[str, Dict], List[pd.Timestamp]]:
    """Loads and caches stock features from local DB."""
    print(f"Loading data from market_data.db starting from {start_date}...")
    
    # Load NIFTY for regime filter
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

    for sym in TOP_LIQUID_UNIVERSE:
        raw_df = load_candles_from_db(sym, timeframe="1D")
        if raw_df.empty or len(raw_df) < 100:
            continue
        feat_df = prepare_indicators(raw_df)
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
    print(f"Loaded {len(stock_dfs)} stocks across {len(all_timestamps)} trading days.")
    return stock_dfs, all_timestamps

def run_simulation(
    stock_dfs: Dict[str, Dict],
    all_timestamps: List[pd.Timestamp],
    initial_cap: float = 100000.0,
    max_alloc_pct: float = 0.30,       # 30% per trade (3 concurrent trades)
    min_alloc_pct: float = 0.15,       # 15% minimum to prevent friction erosion
    max_positions: int = 3,
    entry_model: str = "BREAKOUT",     # "BREAKOUT", "PULLBACK", "VCP_MOMENTUM", "TREND_RIDER"
    sl_atr_mult: float = 1.8,          # Stop loss = Entry - (k * ATR)
    tp_atr_mult: float = 3.0,          # Take profit = Entry + (m * ATR) (or 0 for trailing)
    trailing_exit: str = "NONE",       # "NONE", "CHANDELIER", "EMA20"
    be_profit_pct: float = 4.0,        # Move stop to breakeven only after reaching +4%
    stagnation_bars: int = 15,         # Time exit: only after 15 bars if still near/below entry
    enable_rotation: bool = True,      # Liquidate stagnant trade to fund ultra-alpha breakout
    rotation_hurdle: float = 25.0
) -> Dict:
    """
    Precision event-driven swing simulator with dynamic compounding capital allocation,
    strict Indian delivery friction (0.35% roundtrip), and monthly statistical aggregation.
    """
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_positions)
    available_cash = float(initial_cap)
    open_positions = {}
    closed_trades = []
    equity_series = {}

    for ts in all_timestamps:
        # Mark to market equity
        current_equity = available_cash
        for s, p in open_positions.items():
            if ts in stock_dfs[s]:
                current_equity += p['qty'] * stock_dfs[s][ts]['close']
            else:
                current_equity += p['qty'] * p['entry_price']

        # Dynamic allocation thresholds based on CURRENT total account equity
        dyn_max_trade = current_equity * max_alloc_pct
        dyn_min_trade = current_equity * min_alloc_pct

        # -------------------------------------------------------------
        # 1. EVALUATE EXITS
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

            # Dynamic Break-Even trigger (only activate after meaningful profit, e.g. +4%)
            if be_profit_pct > 0 and not pos['be_activated']:
                run_up = ((high - entry) / entry) * 100
                if run_up >= be_profit_pct:
                    pos['stop_loss'] = max(pos['stop_loss'], entry * 1.004) # Lock friction cost
                    pos['be_activated'] = True

            exit_price = None
            reason = ""

            # Check Fixed Target Hit
            if tp_atr_mult > 0 and high >= pos['target_price']:
                exit_price = pos['target_price']
                reason = "Target Hit"
            # Check Stop Loss Hit
            elif low <= pos['stop_loss']:
                exit_price = pos['stop_loss']
                reason = "Break-Even Stop" if pos['be_activated'] else "Stop Loss Hit"
            # Check Trailing Exit (Chandelier or EMA20)
            elif trailing_exit == "CHANDELIER" and bar['Dir'] == -1 and pos['bars_held'] >= 3:
                exit_price = close
                reason = "Chandelier Trend Reversal"
            elif trailing_exit == "EMA20" and close < bar['EMA20'] and pos['bars_held'] >= 4:
                exit_price = close
                reason = "EMA20 Trailing Exit"
            # Stagnation Exit: Only after holding for 15+ bars with no momentum
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
        # 2. SCAN NEW ENTRIES & ASSET ROTATION
        # -------------------------------------------------------------
        candidates = []
        for sym, bars in stock_dfs.items():
            if ts not in bars or sym in open_positions:
                continue

            b = bars[ts]
            # Macro Regime Gate
            if b.get('Regime_Bullish', 1) != 1:
                continue

            c = b['close']
            atr = b['ATR']

            # Major Trend Gate: Price above 200 EMA and 50 EMA
            if c < b['EMA200'] or c < b['EMA50']:
                continue

            is_entry = False
            alpha = 70.0

            if entry_model == "BREAKOUT":
                # High-conviction breakout: 20-day high test + Volume surge + Chandelier buy zone
                if (b['high'] >= b['High20']) and (b['Vol_Surge'] >= 1.25) and (b['Dir'] == 1):
                    is_entry = True
                    alpha += 30

            elif entry_model == "PULLBACK":
                # Healthy pullback in uptrend: 20 EMA bounce, RSI 42-55, Bullish green candle
                prev_c = bars.get(ts, {}).get('open', c)
                is_green = c > b['open']
                near_ema20 = abs(c - b['EMA20']) / c <= 0.02
                if near_ema20 and (42 <= b['RSI'] <= 55) and is_green:
                    is_entry = True
                    alpha += 25

            elif entry_model == "VCP_MOMENTUM":
                # Volatility Contraction Breakout: EMA9 > EMA20 > EMA50, RSI 55-68, Volume surge
                stack = (b['EMA9'] > b['EMA20']) and (b['EMA20'] > b['EMA50'])
                if stack and (55 <= b['RSI'] <= 70) and (b['Vol_Surge'] >= 1.3):
                    is_entry = True
                    alpha += 35

            elif entry_model == "TREND_RIDER":
                # Chandelier Flip confirmation with volume
                if b['Dir_Flip'] and (b['Vol_Surge'] >= 1.2):
                    is_entry = True
                    alpha += 30

            if is_entry:
                if b['Vol_Surge'] >= 1.8: alpha += 15
                if b['RSI'] >= 60: alpha += 10
                candidates.append((sym, c, atr, alpha))

        # Rank candidates by Alpha score descending
        candidates.sort(key=lambda x: x[3], reverse=True)

        for sym, c, atr, alpha in candidates:
            # Check slot capacity
            if len(open_positions) >= max_positions or available_cash < dyn_min_trade:
                # Dynamic Rotation check: Liquidate stagnant holding if candidate is vastly superior
                if enable_rotation and open_positions:
                    weakest_sym = None
                    weakest_health = 999.0

                    for h_sym, h_pos in open_positions.items():
                        if ts not in stock_dfs[h_sym]: continue
                        h_b = stock_dfs[h_sym][ts]
                        h_pnl = ((h_b['close'] - h_pos['entry_price']) / h_pos['entry_price']) * 100
                        b_held = h_pos.get('bars_held', 0)

                        health = 75.0
                        if h_pnl >= 3.0: health += 25
                        elif h_pnl <= -1.5: health -= 30
                        # Stagnation penalty: holding for 7+ bars without moving
                        if b_held >= 7 and h_pnl < 1.0:
                            health -= min(40, (b_held - 6) * 5)
                        if h_b.get('MACD', 0) < h_b.get('MACD_Signal', 0):
                            health -= 15

                        if health < weakest_health:
                            weakest_health = health
                            weakest_sym = h_sym

                    # Only rotate if the delta is significant (covers friction + hurdle)
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
                            "reason": f"Rotated Out (Health {weakest_health:.0f}) -> Swapped to {sym}",
                            "bars_held": w_p['bars_held']
                        })
                        del open_positions[weakest_sym]
                    else:
                        continue # Slots full and no viable rotation
                else:
                    continue

            # Calculate Dynamic Position Size
            alloc = min(dyn_max_trade, available_cash)
            if alloc < dyn_min_trade:
                continue

            entry_price = c * 1.002 # 0.2% execution slip
            qty = int(alloc // entry_price)
            if qty <= 0:
                continue

            gross_buy = qty * entry_price
            fric_buy = engine.calculate_friction(gross_buy, True)
            if available_cash < (gross_buy + fric_buy):
                continue

            available_cash -= (gross_buy + fric_buy)
            target = entry_price + (tp_atr_mult * atr) if tp_atr_mult > 0 else 999999.0
            stop = entry_price - (sl_atr_mult * atr)

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
    # 3. PERFORMANCE STATS & MONTHLY BREAKDOWN
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

    return {
        "entry_model": entry_model,
        "sl_atr_mult": sl_atr_mult,
        "tp_atr_mult": tp_atr_mult,
        "trailing_exit": trailing_exit,
        "be_profit_pct": be_profit_pct,
        "stagnation_bars": stagnation_bars,
        "enable_rotation": enable_rotation,
        "max_alloc_pct": max_alloc_pct,
        "total_trades": len(df_trades),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "total_net_pnl": round(total_net, 2),
        "total_roi_pct": round(total_roi, 1),
        "avg_monthly_pct": round(avg_monthly_return, 2),
        "max_drawdown_pct": round(max_dd, 1),
        "months_tested": total_months,
        "months_over_3pct": int(months_over_3pct),
        "consistency_rate": round(consistency_rate, 1),
        "worst_month_pct": round(monthly_pct.min(), 1) if not monthly_pct.empty else 0,
        "best_month_pct": round(monthly_pct.max(), 1) if not monthly_pct.empty else 0,
        "monthly_breakdown": monthly_pct.to_dict()
    }

def optimize_universe():
    stock_dfs, all_timestamps = load_data_dict(start_date="2020-01-01")
    if not stock_dfs:
        print("Error: No data loaded.")
        return

    print(f"\nSimulating across {len(all_timestamps)} trading days (2020-2026, ~6.5 years)...\n")

    # Parameter grid designed to find the exact high-profit settings
    test_matrix = [
        # --- Group A: VCP Momentum (Trend & Volume Squeeze) ---
        {"entry_model": "VCP_MOMENTUM", "tp_atr_mult": 2.5, "sl_atr_mult": 1.8, "trailing_exit": "NONE", "be_profit_pct": 3.5, "stagnation_bars": 12, "enable_rotation": True, "max_alloc_pct": 0.30},
        {"entry_model": "VCP_MOMENTUM", "tp_atr_mult": 3.0, "sl_atr_mult": 1.8, "trailing_exit": "NONE", "be_profit_pct": 4.0, "stagnation_bars": 15, "enable_rotation": True, "max_alloc_pct": 0.30},
        {"entry_model": "VCP_MOMENTUM", "tp_atr_mult": 3.5, "sl_atr_mult": 2.0, "trailing_exit": "NONE", "be_profit_pct": 4.5, "stagnation_bars": 15, "enable_rotation": True, "max_alloc_pct": 0.30},
        {"entry_model": "VCP_MOMENTUM", "tp_atr_mult": 0.0, "sl_atr_mult": 1.8, "trailing_exit": "EMA20", "be_profit_pct": 4.0, "stagnation_bars": 15, "enable_rotation": True, "max_alloc_pct": 0.30},

        # --- Group B: Breakout (20-Day High Surge) ---
        {"entry_model": "BREAKOUT", "tp_atr_mult": 2.5, "sl_atr_mult": 1.8, "trailing_exit": "NONE", "be_profit_pct": 4.0, "stagnation_bars": 12, "enable_rotation": True, "max_alloc_pct": 0.30},
        {"entry_model": "BREAKOUT", "tp_atr_mult": 3.0, "sl_atr_mult": 1.8, "trailing_exit": "NONE", "be_profit_pct": 4.0, "stagnation_bars": 15, "enable_rotation": True, "max_alloc_pct": 0.30},
        {"entry_model": "BREAKOUT", "tp_atr_mult": 3.5, "sl_atr_mult": 2.0, "trailing_exit": "NONE", "be_profit_pct": 4.5, "stagnation_bars": 18, "enable_rotation": True, "max_alloc_pct": 0.30},
        {"entry_model": "BREAKOUT", "tp_atr_mult": 3.0, "sl_atr_mult": 1.8, "trailing_exit": "CHANDELIER", "be_profit_pct": 4.0, "stagnation_bars": 15, "enable_rotation": True, "max_alloc_pct": 0.30},
        {"entry_model": "BREAKOUT", "tp_atr_mult": 3.0, "sl_atr_mult": 1.8, "trailing_exit": "NONE", "be_profit_pct": 4.0, "stagnation_bars": 15, "enable_rotation": False, "max_alloc_pct": 0.30},

        # --- Group C: Pullback Dip-Buying (20 EMA Reversals) ---
        {"entry_model": "PULLBACK", "tp_atr_mult": 2.5, "sl_atr_mult": 1.6, "trailing_exit": "NONE", "be_profit_pct": 3.5, "stagnation_bars": 12, "enable_rotation": True, "max_alloc_pct": 0.30},
        {"entry_model": "PULLBACK", "tp_atr_mult": 3.0, "sl_atr_mult": 1.8, "trailing_exit": "NONE", "be_profit_pct": 4.0, "stagnation_bars": 15, "enable_rotation": True, "max_alloc_pct": 0.30},
        {"entry_model": "PULLBACK", "tp_atr_mult": 0.0, "sl_atr_mult": 1.8, "trailing_exit": "EMA20", "be_profit_pct": 4.0, "stagnation_bars": 15, "enable_rotation": True, "max_alloc_pct": 0.30},

        # --- Group D: Trend Rider (Chandelier Flip) ---
        {"entry_model": "TREND_RIDER", "tp_atr_mult": 2.5, "sl_atr_mult": 1.8, "trailing_exit": "NONE", "be_profit_pct": 4.0, "stagnation_bars": 15, "enable_rotation": True, "max_alloc_pct": 0.30},
        {"entry_model": "TREND_RIDER", "tp_atr_mult": 3.0, "sl_atr_mult": 1.8, "trailing_exit": "NONE", "be_profit_pct": 4.0, "stagnation_bars": 15, "enable_rotation": True, "max_alloc_pct": 0.30},
        {"entry_model": "TREND_RIDER", "tp_atr_mult": 0.0, "sl_atr_mult": 1.8, "trailing_exit": "CHANDELIER", "be_profit_pct": 4.0, "stagnation_bars": 15, "enable_rotation": True, "max_alloc_pct": 0.30},

        # --- Group E: Capital Allocation Sensitivity (25% vs 33% vs 35%) ---
        {"entry_model": "BREAKOUT", "tp_atr_mult": 3.0, "sl_atr_mult": 1.8, "trailing_exit": "NONE", "be_profit_pct": 4.0, "stagnation_bars": 15, "enable_rotation": True, "max_alloc_pct": 0.25},
        {"entry_model": "BREAKOUT", "tp_atr_mult": 3.0, "sl_atr_mult": 1.8, "trailing_exit": "NONE", "be_profit_pct": 4.0, "stagnation_bars": 15, "enable_rotation": True, "max_alloc_pct": 0.33},
        {"entry_model": "BREAKOUT", "tp_atr_mult": 3.0, "sl_atr_mult": 1.8, "trailing_exit": "NONE", "be_profit_pct": 4.0, "stagnation_bars": 15, "enable_rotation": True, "max_alloc_pct": 0.35},
    ]

    results = []
    for cfg in tqdm(test_matrix, desc="Optimizing Strategy Grid"):
        res = run_simulation(
            stock_dfs=stock_dfs,
            all_timestamps=all_timestamps,
            initial_cap=100000.0,
            min_alloc_pct=0.15,
            max_positions=int(1 // cfg['max_alloc_pct']),
            **cfg
        )
        results.append(res)

    results.sort(key=lambda x: x['avg_monthly_pct'], reverse=True)

    print("\n" + "="*125)
    print("🏆  PRECISION STRATEGY TOURNAMENT LEADERBOARD (2020-2026, 75 LIQUID NSE STOCKS)")
    print("="*125)
    header = f"{'Rank':<4} {'Setup Description':<46} {'Avg Mo %':<9} {'WinRate':<8} {'ProfitFac':<10} {'MaxDD':<8} {'3%+ Mo %':<9} {'Total PnL (₹)'}"
    print(header)
    print("-"*125)

    for i, r in enumerate(results, 1):
        target_str = f"TP:{r['tp_atr_mult']}x" if r['tp_atr_mult'] > 0 else f"Trail:{r['trailing_exit']}"
        desc = f"{r['entry_model']} [{target_str}, SL:{r['sl_atr_mult']}x, Alloc:{int(r['max_alloc_pct']*100)}%, Rot:{'YES' if r['enable_rotation'] else 'NO'}]"
        print(
            f"{i:<4} {desc:<46} "
            f"{r['avg_monthly_pct']:>6.2f}%   "
            f"{r['win_rate']:>5.1f}%   "
            f"{r['profit_factor']:>7.2f}    "
            f"{r['max_drawdown_pct']:>5.1f}%   "
            f"{r['consistency_rate']:>6.1f}%   "
            f"₹{r['total_net_pnl']:>10,.2f}"
        )

    print("="*125)
    best = results[0]
    print(f"\n🥇 CHAMPION STRATEGY CONFIGURATION:")
    print(f"   ► Setup Model                : {best['entry_model']}")
    print(f"   ► Take-Profit Target         : {best['tp_atr_mult']}x ATR (or Trailing: {best['trailing_exit']})")
    print(f"   ► Initial Stop Loss          : {best['sl_atr_mult']}x ATR")
    print(f"   ► Max Trade Allocation       : {int(best['max_alloc_pct']*100)}% of account equity")
    print(f"   ► Min Trade Allocation       : 15% of account equity")
    print(f"   ► Break-Even Profit Lock     : Triggered only after +{best['be_profit_pct']}% gain")
    print(f"   ► Stagnation Time Exit       : {best['stagnation_bars']} bars (with momentum penalty)")
    print(f"   ► Asset Rotation Engine      : {'Active (hurdle: 25 pts)' if best['enable_rotation'] else 'Disabled'}")
    print(f"   ► Average Monthly Net Profit : +{best['avg_monthly_pct']}% / month")
    print(f"   ► Total Net Profit on ₹1L    : ₹{best['total_net_pnl']:,.2f} (+{best['total_roi_pct']}%)")
    print(f"   ► Win Rate                   : {best['win_rate']}%")
    print(f"   ► Profit Factor              : {best['profit_factor']}")
    print(f"   ► Maximum Drawdown           : {best['max_drawdown_pct']}%")
    print(f"   ► Target Achievement Rate    : {best['months_over_3pct']}/{best['months_tested']} months achieved >= 3% net profit ({best['consistency_rate']}%)")
    print(f"   ► Worst Month                : {best['worst_month_pct']}%")
    print(f"   ► Best Month                 : +{best['best_month_pct']}%")

    print("\nRecent 12-Month Performance Breakdown:")
    recent_months = list(best['monthly_breakdown'].keys())[-12:]
    for m in recent_months:
        p = best['monthly_breakdown'][m]
        status = "✅ TARGET MET" if p >= 3.0 else ("⚠️ BELOW TARGET" if p > 0 else "🔴 LOSS")
        print(f"   {m}: {p:>+6.2f}%  [{status}]")
    print("\n")

if __name__ == "__main__":
    optimize_universe()

