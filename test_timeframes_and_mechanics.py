"""
test_timeframes_and_mechanics.py
Compares 1D vs 1H swing strategies on local DB to discover the true 3%+ monthly engine.
Enforces Anti-Churn Minimum Holding Lock, Dynamic Compounding Sizing, and Full Indian Delivery Friction.
"""

import sys
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple
from tqdm import tqdm

from database import load_candles_from_db
from backtester import EngineMechanics

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Top Momentum Swingers from Nifty 500
MOMENTUM_LEADERS = [
    "TRENT.NS", "DIXON.NS", "HAL.NS", "BEL.NS", "ZOMATO.NS", "POLYCAB.NS",
    "BHEL.NS", "RVNL.NS", "IRFC.NS", "PFC.NS", "RECLTD.NS", "SUZLON.NS",
    "MCX.NS", "KALYANKJIL.NS", "PERSISTENT.NS", "COFORGE.NS", "CUMMINSIND.NS",
    "SHRIRAMFIN.NS", "CHOLAFIN.NS", "TVSMOTOR.NS", "BHARTIARTL.NS", "TITAN.NS",
    "TATAMOTORS.NS", "MAXHEALTH.NS", "AUROPHARMA.NS", "LUPIN.NS", "MOTHERSON.NS",
    "TORNTPOWER.NS", "SIEMENS.NS", "ABB.NS", "VEDL.NS", "HINDALCO.NS"
]

def prepare_data(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) < 50:
        return pd.DataFrame()
    df = df.copy().sort_values(by="timestamp").reset_index(drop=True)

    df['EMA9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['EMA200'] = df['close'].ewm(span=200, adjust=False).mean() if len(df) >= 200 else df['close'].ewm(span=len(df), adjust=False).mean()

    # RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / (loss.replace(0, np.nan))
    df['RSI'] = (100 - (100 / (1 + rs))).fillna(50)

    # MACD
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()

    # ATR
    hl = df['high'] - df['low']
    hc = np.abs(df['high'] - df['close'].shift())
    lc = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(window=14).mean().fillna(df['close'] * 0.02)

    # Volume Surge
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

def load_timeframe_data(timeframe: str, start_date: str) -> Tuple[Dict[str, Dict], List[pd.Timestamp]]:
    stock_dfs = {}
    all_ts = set()

    for sym in MOMENTUM_LEADERS:
        raw = load_candles_from_db(sym, timeframe=timeframe)
        if raw.empty or len(raw) < 100:
            continue
        feat = prepare_data(raw)
        if feat.empty:
            continue
        feat = feat[feat['timestamp'] >= start_date].copy().set_index('timestamp')
        d = feat.to_dict('index')
        stock_dfs[sym] = d
        all_ts.update(d.keys())

    all_timestamps = sorted(list(all_ts))
    return stock_dfs, all_timestamps

def backtest_engine(
    stock_dfs: Dict[str, Dict],
    all_timestamps: List[pd.Timestamp],
    timeframe: str,
    initial_cap: float = 100000.0,
    max_alloc_pct: float = 0.30,
    min_alloc_pct: float = 0.15,
    max_positions: int = 3,
    tp_mult: float = 3.0,
    sl_mult: float = 1.8,
    be_profit_pct: float = 3.5,
    min_hold_bars_lock: int = 6,      # ANTI-CHURN: cannot rotate out until held at least 6 bars!
    stagnation_bars: int = 20,
    enable_rotation: bool = True,
    rotation_hurdle: float = 25.0,
    min_alpha: float = 85.0
) -> Dict:
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

        dyn_max_trade = current_equity * max_alloc_pct
        dyn_min_trade = current_equity * min_alloc_pct

        # 1. EVALUATE EXITS
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

            # Break-even lock
            if be_profit_pct > 0 and not pos['be_activated']:
                run_up = ((high - entry) / entry) * 100
                if run_up >= be_profit_pct:
                    pos['stop_loss'] = max(pos['stop_loss'], entry * 1.004)
                    pos['be_activated'] = True

            exit_price = None
            reason = ""

            if high >= pos['target_price']:
                exit_price = pos['target_price']
                reason = "Target Hit"
            elif low <= pos['stop_loss']:
                exit_price = pos['stop_loss']
                reason = "Break-Even Stop" if pos['be_activated'] else "Stop Loss Hit"
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
                available_cash += (gross_sell - engine.calculate_friction(gross_sell, False))

                closed_trades.append({
                    "ticker": sym,
                    "entry_date": pos['entry_date'],
                    "exit_date": ts,
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "net_pnl": net_pnl,
                    "pnl_pct": (net_pnl / gross_buy) * 100,
                    "reason": reason,
                    "bars_held": pos['bars_held']
                })
                to_close.append(sym)

        for sym in to_close:
            del open_positions[sym]

        # 2. SCAN ENTRIES
        candidates = []
        for sym, bars in stock_dfs.items():
            if ts not in bars or sym in open_positions:
                continue
            b = bars[ts]
            c = b['close']
            atr = b['ATR']

            # High-RS Momentum Trend: Above 200 EMA and 50 EMA, and 20 EMA > 50 EMA
            if c < b['EMA200'] or c < b['EMA50'] or b['EMA20'] < b['EMA50']:
                continue

            # Breakout + Volume Surge or Pullback bounce
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

            if alpha >= min_alpha:
                candidates.append((sym, c, atr, alpha))

        candidates.sort(key=lambda x: x[3], reverse=True)

        for sym, c, atr, alpha in candidates:
            # Slot capacity check
            if len(open_positions) >= max_positions or available_cash < dyn_min_trade:
                # Anti-Churn Capital Rotation
                if enable_rotation and open_positions:
                    weakest_sym = None
                    weakest_health = 999.0

                    for h_sym, h_pos in open_positions.items():
                        # ANTI-CHURN RULE: Cannot rotate if held less than min_hold_bars_lock!
                        if h_pos.get('bars_held', 0) < min_hold_bars_lock:
                            continue

                        if ts not in stock_dfs[h_sym]: continue
                        h_b = stock_dfs[h_sym][ts]
                        h_pnl = ((h_b['close'] - h_pos['entry_price']) / h_pos['entry_price']) * 100
                        b_held = h_pos.get('bars_held', 0)

                        health = 75.0
                        if h_pnl >= 3.5: health += 25
                        elif h_pnl <= -1.5: health -= 30
                        if b_held >= (min_hold_bars_lock + 4) and h_pnl < 1.0:
                            health -= min(40, (b_held - min_hold_bars_lock) * 5)
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

            alloc = min(dyn_max_trade, available_cash)
            if alloc < dyn_min_trade:
                continue

            entry_price = c * 1.002
            qty = int(alloc // entry_price)
            if qty <= 0: continue

            gross_buy = qty * entry_price
            fric_buy = engine.calculate_friction(gross_buy, True)
            if available_cash < (gross_buy + fric_buy): continue

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

    if not closed_trades:
        return {"total_trades": 0, "net_pnl": 0, "avg_monthly": 0}

    df_trades = pd.DataFrame(closed_trades)
    df_trades['month'] = pd.to_datetime(df_trades['exit_date']).dt.to_period('M')

    monthly_pnl = df_trades.groupby('month')['net_pnl'].sum()
    monthly_pct = (monthly_pnl / initial_cap) * 100

    total_net = df_trades['net_pnl'].sum()
    win_rate = (len(df_trades[df_trades['net_pnl'] > 0]) / len(df_trades)) * 100
    gains = df_trades[df_trades['net_pnl'] > 0]['net_pnl'].sum()
    losses = abs(df_trades[df_trades['net_pnl'] < 0]['net_pnl'].sum())
    profit_factor = (gains / losses) if losses > 0 else 99.0

    eq_vals = np.array(list(equity_series.values()))
    max_peaks = np.maximum.accumulate(eq_vals)
    dd_arr = (max_peaks - eq_vals) / max_peaks * 100
    max_dd = np.max(dd_arr) if len(dd_arr) > 0 else 0

    months_over_3pct = (monthly_pct >= 3.0).sum()
    total_months = len(monthly_pct)
    consistency_rate = (months_over_3pct / total_months) * 100 if total_months > 0 else 0
    avg_monthly = monthly_pct.mean()

    return {
        "timeframe": timeframe,
        "total_trades": len(df_trades),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "total_net": round(total_net, 2),
        "roi_pct": round((total_net / initial_cap) * 100, 1),
        "avg_monthly": round(avg_monthly, 2),
        "max_dd": round(max_dd, 1),
        "months_over_3pct": int(months_over_3pct),
        "total_months": total_months,
        "consistency_rate": round(consistency_rate, 1),
        "monthly_breakdown": monthly_pct.to_dict()
    }

def run_tests():
    print("Testing 1D Daily Swing Strategy (2020-2026)...")
    dfs_1d, ts_1d = load_timeframe_data("1D", "2020-01-01")
    res_1d = backtest_engine(
        stock_dfs=dfs_1d,
        all_timestamps=ts_1d,
        timeframe="1D",
        tp_mult=3.2,
        sl_mult=1.8,
        be_profit_pct=4.0,
        min_hold_bars_lock=6,
        stagnation_bars=18,
        enable_rotation=True
    )

    print("\nTesting 1H Hourly Swing Strategy (2024-2026)...")
    dfs_1h, ts_1h = load_timeframe_data("1H", "2024-01-01")
    res_1h = backtest_engine(
        stock_dfs=dfs_1h,
        all_timestamps=ts_1h,
        timeframe="1H",
        tp_mult=2.8,
        sl_mult=1.6,
        be_profit_pct=3.0,
        min_hold_bars_lock=12,    # On 1H, 12 bars is ~2 trading days minimum lock!
        stagnation_bars=40,       # ~6 trading days
        enable_rotation=True
    )

    print("\n" + "="*85)
    print("COMPARISON: 1D SWING VS 1H SWING STRATEGY ON MOMENTUM LEADERS")
    print("="*85)
    for r in [res_1d, res_1h]:
        print(f"Timeframe: {r['timeframe']}")
        print(f"  ► Total Closed Trades : {r['total_trades']}")
        print(f"  ► Win Rate            : {r['win_rate']}%")
        print(f"  ► Profit Factor       : {r['profit_factor']}")
        print(f"  ► Total Net Profit    : ₹{r['total_net']:,.2f} (+{r['roi_pct']}%)")
        print(f"  ► Avg Monthly Profit  : +{r['avg_monthly']}% / month")
        print(f"  ► Max Drawdown        : {r['max_dd']}%")
        print(f"  ► Months >= 3% Profit : {r['months_over_3pct']}/{r['total_months']} ({r['consistency_rate']}%)")
        print("-"*85)

if __name__ == "__main__":
    run_tests()

