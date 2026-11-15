"""
golden_cross_4h_optimizer.py
=============================================================================
4H Golden Cross Strategy Tournament on Local NSE Market Data (market_data.db).
- Timeframe: 4H
- Entry: Golden Crossover setups (50 > 200 EMA, 20 > 50 EMA in major uptrend)
- Profit Booking: NO fixed % cap or time limit — pure trend riding
- Exit: Testing the "Right Exit" paradigms (Chandelier, 50 EMA, Death Cross, SuperTrend)
- Capital: Starting ₹1,00,000, dynamic compounding allocation, full Indian delivery friction (0.35%)
=============================================================================
"""

import sys
import sqlite3
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

# Broad liquid universe across Nifty 500
UNIVERSE_4H = [
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

def calculate_4h_indicators(df: pd.DataFrame, chandelier_mult: float = 3.0) -> pd.DataFrame:
    """Vectorized calculation of 4H trend, golden cross, and trailing exits."""
    if df is None or len(df) < 220:
        return pd.DataFrame()

    df = df.copy().sort_values(by="timestamp").reset_index(drop=True)

    # Moving Averages
    df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['EMA200'] = df['close'].ewm(span=200, adjust=False).mean()

    # Crossover Signals
    # 1. Classic Golden Cross: 50 EMA crosses above 200 EMA
    df['GC_50_200'] = (df['EMA50'] > df['EMA200']) & (df['EMA50'].shift(1) <= df['EMA200'].shift(1))
    
    # 2. Swing Golden Cross: 20 EMA crosses above 50 EMA while above 200 EMA
    df['GC_20_50'] = (df['EMA20'] > df['EMA50']) & (df['EMA20'].shift(1) <= df['EMA50'].shift(1)) & (df['close'] > df['EMA200'])

    # 3. Pullback in established Golden Cross (50 > 200 and bounce off 50 EMA)
    df['Established_GC'] = (df['EMA50'] > df['EMA200']) & (df['close'] > df['EMA200'])
    near_50 = (np.abs(df['close'] - df['EMA50']) / df['close']) <= 0.015
    df['GC_Pullback'] = df['Established_GC'] & near_50 & (df['close'] > df['open']) & (df['close'].shift(1) <= df['open'].shift(1))

    # Death Cross (50 crosses below 200)
    df['DC_50_200'] = (df['EMA50'] < df['EMA200']) & (df['EMA50'].shift(1) >= df['EMA200'].shift(1))

    # True Range & ATR (14)
    hl = df['high'] - df['low']
    hc = np.abs(df['high'] - df['close'].shift())
    lc = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(window=14).mean().fillna(df['close'] * 0.02)

    # Volume Surge
    df['Vol_SMA20'] = df['volume'].rolling(window=20).mean()
    df['Vol_Surge'] = (df['volume'] / df['Vol_SMA20'].replace(0, np.nan)).fillna(1.0)

    # Chandelier Trailing Exit (22, chandelier_mult)
    atr22 = tr.ewm(alpha=1/22, adjust=False).mean() * chandelier_mult
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

    df['Chandelier_Dir'] = dir_arr
    df['Chandelier_Stop'] = long_stop

    # SuperTrend (14, 2.5)
    st_atr = tr.rolling(window=14).mean() * 2.5
    hl2 = (df['high'] + df['low']) / 2.0
    st_upper = hl2 + st_atr
    st_lower = hl2 - st_atr
    st_dir = np.ones(len(df))
    for i in range(1, len(df)):
        if closes[i] > st_upper.iloc[i-1]:
            st_dir[i] = 1
        elif closes[i] < st_lower.iloc[i-1]:
            st_dir[i] = -1
        else:
            st_dir[i] = st_dir[i-1]
    df['SuperTrend_Dir'] = st_dir

    return df.dropna(subset=['EMA200', 'ATR'])

def load_4h_universe(
    start_date: str = "2018-01-01", 
    symbols: List[str] = None, 
    timeframe: str = "4H",
    progress_callback = None,
    chandelier_mult: float = 3.0
) -> Tuple[Dict[str, Dict], List[pd.Timestamp]]:
    """Loads candles and pre-calculates indicators for the universe on any chosen timeframe."""
    target_symbols = symbols if symbols else UNIVERSE_4H
    total_syms = len(target_symbols)
    print(f"Loading {timeframe} candles from market_data.db (from {start_date}, Chandelier {chandelier_mult}x ATR)...")
    
    # Load Nifty for regime gate
    df_nifty = load_candles_from_db("NIFTY", timeframe=timeframe)
    if not df_nifty.empty:
        df_nifty = df_nifty[df_nifty['timestamp'] >= start_date].copy()
        df_nifty['Nifty_EMA50'] = df_nifty['close'].ewm(span=50, adjust=False).mean()
        df_nifty['Regime_Bullish'] = (df_nifty['close'] > df_nifty['Nifty_EMA50']).astype(int)
        df_nifty = df_nifty.set_index('timestamp')[['Regime_Bullish']].shift(1)
    else:
        df_nifty = pd.DataFrame(columns=['Regime_Bullish'])

    stock_dfs = {}
    all_ts = set()

    for idx, sym in enumerate(target_symbols):
        if progress_callback and (idx % 10 == 0 or idx == total_syms - 1):
            progress_callback(min(0.90, (idx + 1) / total_syms), f"Loading {timeframe} Data: [{sym}] ({idx+1}/{total_syms} stocks)...")

        raw_df = load_candles_from_db(sym, timeframe=timeframe)
        if raw_df.empty or len(raw_df) < 50:
            continue
        feat_df = calculate_4h_indicators(raw_df, chandelier_mult=chandelier_mult)
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
    print(f"Loaded {len(stock_dfs)} stocks on {timeframe} timeframe across {len(all_timestamps)} bars.")
    return stock_dfs, all_timestamps

def simulate_4h_golden_cross(
    stock_dfs: Dict[str, Dict],
    all_timestamps: List[pd.Timestamp],
    initial_cap: float = 100000.0,
    max_alloc_pct: float = 0.30,       # 30% per trade (max 3 concurrent)
    min_alloc_pct: float = 0.15,
    max_positions: int = 3,
    entry_trigger: str = "CLASSIC_GC", # "CLASSIC_GC", "SWING_GC", "DUAL_GC", "ESTABLISHED_PULLBACK"
    exit_model: str = "CHANDELIER",    # "CHANDELIER", "EMA50_CROSS", "DEATH_CROSS", "SUPERTREND", "EMA20_CROSS"
    initial_sl_atr: float = 2.5,       # Wide initial catastrophic safety stop (no upside cap)
    strategy_name: str = "",
    timeframe: str = "4H",
    entry_filter_gate: str = "ACTIVE_VOLUME", # "NO_GATE", "TRUE_FLIP_ONLY", "ACTIVE_VOLUME", "HIGH_QUALITY", "CHANDELIER_ALIGNED", "STRICT_INSTITUTIONAL"
    monthly_sip: float = 0.0
) -> Dict:
    """
    Simulates a pure trend-following Golden Cross strategy across any timeframe (1D, 4H, 2H, 1WK):
    - NO target price limit (let winners ride indefinitely).
    - NO time limit.
    - Exits cleanly when the trend signals a reversal (the 'right exit').
    - Evaluates trade-level compounding and monthly consistency.
    """
    tf_upper = timeframe.upper()
    if tf_upper in ["1D", "D"]:
        bars_per_day = 1.0
    elif tf_upper in ["1WK", "1W", "W"]:
        bars_per_day = 0.2
    elif tf_upper in ["4H"]:
        bars_per_day = 2.0
    elif tf_upper in ["2H"]:
        bars_per_day = 3.1
    elif tf_upper in ["1H"]:
        bars_per_day = 6.25
    else:
        bars_per_day = 1.0

    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_positions)
    available_cash = float(initial_cap)
    total_invested = float(initial_cap)
    open_positions = {}
    closed_trades = []
    equity_series = {}
    last_month = None

    for ts in all_timestamps:
        # Monthly Top-Up / SIP injection on the first trading bar of a new calendar month
        current_month = (ts.year, ts.month)
        if last_month is not None and current_month != last_month and monthly_sip > 0:
            available_cash += monthly_sip
            total_invested += monthly_sip
        last_month = current_month

        current_equity = available_cash
        for s, p in open_positions.items():
            if ts in stock_dfs[s]:
                current_equity += p['qty'] * stock_dfs[s][ts]['close']
            else:
                current_equity += p['qty'] * p['entry_price']

        dyn_max_trade = current_equity * max_alloc_pct
        dyn_min_trade = current_equity * min_alloc_pct

        # -------------------------------------------------------------
        # STEP 1: EVALUATE EXITS (THE "RIGHT EXIT" PARADIGM)
        # -------------------------------------------------------------
        to_close = []
        for sym, pos in open_positions.items():
            pos['bars_held'] = pos.get('bars_held', 0) + 1
            if ts not in stock_dfs[sym]:
                continue

            bar = stock_dfs[sym][ts]
            close = bar['close']
            low = bar['low']
            high = bar['high']
            entry = pos['entry_price']

            # Track maximum favorable run-up (MFE)
            if high > pos.get('max_high', entry):
                pos['max_high'] = high

            # Track whether position experienced confirmed bullish trend
            if bar.get('Chandelier_Dir', 0) == 1:
                pos['saw_chandelier_bullish'] = True

            exit_price = None
            reason = ""

            # 1. Catastrophic Volatility Stop (protects capital during unexpected crashes)
            if initial_sl_atr > 0 and low <= pos['hard_stop']:
                exit_price = pos['hard_stop']
                reason = "Initial ATR Safety Stop"

            # 2. The Right Exit Rules (only after holding at least 2 bars)
            elif pos['bars_held'] >= 2:
                if exit_model == "CHANDELIER":
                    if entry_filter_gate != "NO_GATE":
                        # True state flip: must have confirmed bullish state before exiting on -1
                        if pos.get('saw_chandelier_bullish', False) and bar['Chandelier_Dir'] == -1:
                            exit_price = close
                            reason = "Chandelier Trend Reversal"
                        elif pos['bars_held'] >= 6 and bar['Chandelier_Dir'] == -1:
                            exit_price = close
                            reason = "Failed Breakout Consolidation"
                    else:
                        if bar['Chandelier_Dir'] == -1:
                            exit_price = close
                            reason = "Chandelier Trend Reversal"

                elif exit_model == "EMA50_CROSS" and close < bar['EMA50']:
                    exit_price = close
                    reason = "4H Close Below 50 EMA"

                elif exit_model == "EMA20_CROSS" and close < bar['EMA20']:
                    exit_price = close
                    reason = "4H Close Below 20 EMA"

                elif exit_model == "DEATH_CROSS" and (bar['EMA50'] < bar['EMA200']):
                    exit_price = close
                    reason = "4H Death Cross (50 < 200 EMA)"

                elif exit_model == "SUPERTREND" and bar['SuperTrend_Dir'] == -1:
                    exit_price = close
                    reason = "SuperTrend Bearish Flip"

                elif exit_model == "HYBRID_TRAIL":
                    # Let winners run with Chandelier, but if up > 15%, lock with 20 EMA
                    profit_pct = ((close - entry) / entry) * 100
                    if profit_pct >= 15.0 and close < bar['EMA20']:
                        exit_price = close
                        reason = "Runner Locked (Close < 20 EMA at +15% gain)"
                    elif entry_filter_gate != "NO_GATE":
                        if pos.get('saw_chandelier_bullish', False) and bar['Chandelier_Dir'] == -1:
                            exit_price = close
                            reason = "Chandelier Trend Reversal"
                        elif pos['bars_held'] >= 6 and bar['Chandelier_Dir'] == -1:
                            exit_price = close
                            reason = "Failed Breakout Consolidation"
                    elif bar['Chandelier_Dir'] == -1:
                        exit_price = close
                        reason = "Chandelier Trend Reversal"

            if exit_price:
                gross_buy = pos['qty'] * entry
                gross_sell = pos['qty'] * exit_price
                fric = engine.calculate_friction(gross_buy, True) + engine.calculate_friction(gross_sell, False)
                net_pnl = (gross_sell - gross_buy) - fric
                pnl_pct = (net_pnl / gross_buy) * 100

                available_cash += (gross_sell - engine.calculate_friction(gross_sell, False))

                closed_trades.append({
                    "ticker": sym,
                    "alpha_score": pos.get('alpha_score', 100.0),
                    "entry_date": pos['entry_date'],
                    "exit_date": ts,
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "net_pnl": net_pnl,
                    "pnl_pct": pnl_pct,
                    "max_runup_pct": ((pos.get('max_high', entry) - entry) / entry) * 100,
                    "reason": reason,
                    "bars_held": pos['bars_held'],
                    "days_held": pos['bars_held'] / bars_per_day
                })
                to_close.append(sym)

        for sym in to_close:
            del open_positions[sym]

        # -------------------------------------------------------------
        # STEP 2: SCAN FOR 4H GOLDEN CROSSOVER ENTRIES
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
            is_entry = False
            alpha = 70.0

            if entry_trigger == "CLASSIC_GC":
                # Fresh 50 EMA crosses 200 EMA + Close > 50 EMA
                if b['GC_50_200'] and c > b['EMA50']:
                    is_entry = True
                    alpha += 30

            elif entry_trigger == "SWING_GC":
                # 20 EMA crosses 50 EMA while stock is above 200 EMA
                if b['GC_20_50'] and c > b['EMA200']:
                    is_entry = True
                    alpha += 30

            elif entry_trigger == "DUAL_GC":
                # Either a fresh 50/200 cross OR a fresh 20/50 cross in major bull trend
                if (b['GC_50_200'] or b['GC_20_50']) and c > b['EMA200']:
                    is_entry = True
                    alpha += 35

            elif entry_trigger == "ESTABLISHED_PULLBACK":
                # Established Golden Cross (50 > 200) + pullback bounce off 50 EMA
                if b['GC_Pullback']:
                    is_entry = True
                    alpha += 25

            # Apply False-Positive Quality Gates
            if is_entry:
                if entry_filter_gate == "ACTIVE_VOLUME":
                    # Filters out completely dead/dormant volume (<0.8x SMA20) while preserving quiet pre-breakout pause bars
                    if b.get('Vol_Surge', 1.0) < 0.8:
                        is_entry = False
                elif entry_filter_gate == "CHANDELIER_ALIGNED":
                    if b.get('Chandelier_Dir', 0) != 1:
                        is_entry = False
                elif entry_filter_gate == "HIGH_QUALITY":
                    # Strict: Green candle + Vol >= 1.0x SMA20
                    if c <= b['open'] or b.get('Vol_Surge', 1.0) < 1.0:
                        is_entry = False
                elif entry_filter_gate == "STRICT_INSTITUTIONAL":
                    # Chandelier 1 + Green candle + Vol >= 1.2x + Anti-FOMO extension <= 2.5 ATR
                    if (b.get('Chandelier_Dir', 0) != 1 or 
                        c <= b['open'] or 
                        b.get('Vol_Surge', 1.0) < 1.2 or 
                        ((c - b['EMA20']) / atr) > 2.5):
                        is_entry = False

            if is_entry:
                if b['Vol_Surge'] >= 1.3: alpha += 15
                if b['Chandelier_Dir'] == 1: alpha += 10
                candidates.append((sym, c, atr, alpha, b.get('Chandelier_Dir', 0) == 1))

        # Rank candidates by alpha score descending
        candidates.sort(key=lambda x: x[3], reverse=True)

        for sym, c, atr, alpha, is_ch_bull in candidates:
            if len(open_positions) >= max_positions or available_cash < dyn_min_trade:
                break

            alloc = min(dyn_max_trade, available_cash)
            if alloc < dyn_min_trade:
                continue

            entry_price = c * 1.002 # 0.2% limit execution buffer
            qty = int(alloc // entry_price)
            if qty <= 0:
                continue

            gross_buy = qty * entry_price
            fric_buy = engine.calculate_friction(gross_buy, True)
            if available_cash < (gross_buy + fric_buy):
                continue

            available_cash -= (gross_buy + fric_buy)
            hard_stop = entry_price - (initial_sl_atr * atr)

            open_positions[sym] = {
                "qty": qty,
                "entry_price": entry_price,
                "entry_date": ts,
                "hard_stop": hard_stop,
                "max_high": entry_price,
                "bars_held": 0,
                "saw_chandelier_bullish": is_ch_bull,
                "alpha_score": alpha
            }

        equity_series[ts] = current_equity

    # -------------------------------------------------------------
    # STEP 3: STATISTICAL BREAKDOWN & METRICS
    # -------------------------------------------------------------
    if not closed_trades:
        return {"strategy": strategy_name, "total_trades": 0, "net_pnl": 0}

    df_trades = pd.DataFrame(closed_trades)
    df_trades['month'] = pd.to_datetime(df_trades['exit_date']).dt.to_period('M')

    monthly_pnl = df_trades.groupby('month')['net_pnl'].sum()
    monthly_pct = (monthly_pnl / initial_cap) * 100

    if monthly_sip > 0:
        final_equity = equity_series[all_timestamps[-1]] if equity_series else available_cash
        total_net = final_equity - total_invested
    else:
        total_net = df_trades['net_pnl'].sum()
        final_equity = initial_cap + total_net

    win_rate = (len(df_trades[df_trades['net_pnl'] > 0]) / len(df_trades)) * 100

    gains = df_trades[df_trades['net_pnl'] > 0]['net_pnl'].sum()
    losses = abs(df_trades[df_trades['net_pnl'] < 0]['net_pnl'].sum())
    profit_factor = (gains / losses) if losses > 0 else 99.0

    eq_vals = np.array(list(equity_series.values()))
    max_peaks = np.maximum.accumulate(eq_vals)
    dd_arr = (max_peaks - eq_vals) / max_peaks * 100
    max_dd = np.max(dd_arr) if len(dd_arr) > 0 else 0

    avg_win = df_trades[df_trades['net_pnl'] > 0]['pnl_pct'].mean() if len(df_trades[df_trades['net_pnl'] > 0]) > 0 else 0
    avg_loss = df_trades[df_trades['net_pnl'] < 0]['pnl_pct'].mean() if len(df_trades[df_trades['net_pnl'] < 0]) > 0 else 0
    max_single_win = df_trades['pnl_pct'].max()

    avg_hold_days = df_trades['days_held'].mean()
    months_over_3pct = (monthly_pct >= 3.0).sum()
    total_months = len(monthly_pct)
    consistency_rate = (months_over_3pct / total_months) * 100 if total_months > 0 else 0

    n_years = len(all_timestamps) / (252.0 * bars_per_day) if bars_per_day > 0 else 1.0
    cagr = ((final_equity / total_invested) ** (1.0 / n_years) - 1.0) * 100 if n_years > 0 and final_equity > 0 else 0
    compounded_monthly = ((1.0 + cagr/100.0) ** (1.0/12.0) - 1.0) * 100 if cagr > -100 else 0

    quick_exits = len(df_trades[df_trades['days_held'] <= 1.5])
    quick_pct = (quick_exits / len(df_trades) * 100) if len(df_trades) > 0 else 0

    active_positions_list = []
    if open_positions:
        last_ts = all_timestamps[-1]
        for sym, pos in open_positions.items():
            curr_price = stock_dfs[sym][last_ts]['close'] if last_ts in stock_dfs[sym] else pos['entry_price']
            unrealized_pnl = ((curr_price - pos['entry_price']) / pos['entry_price']) * 100
            active_positions_list.append({
                "ticker": sym,
                "alpha_score": pos.get('alpha_score', 100.0),
                "entry_date": pos['entry_date'],
                "entry_price": pos['entry_price'],
                "current_price": curr_price,
                "unrealized_pnl_pct": unrealized_pnl,
                "stop_loss": pos['hard_stop'],
                "bars_held": pos['bars_held'],
                "days_held": pos['bars_held'] / bars_per_day,
                "status": "🟢 ACTIVE (Trailing Chandelier)"
            })

    return {
        "strategy": strategy_name,
        "entry_trigger": entry_trigger,
        "exit_model": exit_model,
        "entry_filter_gate": entry_filter_gate,
        "total_trades": len(df_trades),
        "total_invested": round(total_invested, 2),
        "monthly_sip": round(monthly_sip, 2),
        "quick_exits": quick_exits,
        "quick_exit_pct": round(quick_pct, 1),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "total_net_pnl": round(total_net, 2),
        "final_equity": round(final_equity, 2),
        "roi_pct": round((total_net / total_invested) * 100, 1),
        "cagr_pct": round(cagr, 2),
        "compounded_monthly": round(compounded_monthly, 2),
        "max_drawdown_pct": round(max_dd, 1),
        "avg_win_pct": round(avg_win, 1),
        "avg_loss_pct": round(avg_loss, 1),
        "max_single_win_pct": round(max_single_win, 1),
        "avg_hold_days": round(avg_hold_days, 1),
        "months_over_3pct": int(months_over_3pct),
        "total_months": total_months,
        "consistency_rate": round(consistency_rate, 1),
        "monthly_breakdown": monthly_pct.to_dict(),
        "equity_curve": list(equity_series.values()),
        "equity_dates": list(equity_series.keys()),
        "trades_df": df_trades,
        "active_positions": active_positions_list
    }

def run_golden_cross_tournament():
    stock_dfs, all_timestamps = load_4h_universe(start_date="2018-01-01")
    if not stock_dfs:
        print("Error: No data loaded.")
        return

    print(f"\nSimulating 4H Golden Cross Strategy Matrix (2018-2026, ~8.8 years, {len(all_timestamps)} bars)...")
    print("Rules: Strictly Golden Crossover entries | ZERO profit target cap | Natural Trend Reversal Exits\n")

    # Benchmark test matrix of Golden Cross setups and Exit mechanisms
    test_matrix = [
        # --- Group 1: Classic Golden Cross (50 crosses 200 EMA) ---
        {"strategy_name": "Classic GC (50>200) -> Chandelier Exit", "entry_trigger": "CLASSIC_GC", "exit_model": "CHANDELIER", "initial_sl_atr": 2.5},
        {"strategy_name": "Classic GC (50>200) -> 50 EMA Trailing Exit", "entry_trigger": "CLASSIC_GC", "exit_model": "EMA50_CROSS", "initial_sl_atr": 2.5},
        {"strategy_name": "Classic GC (50>200) -> Full Death Cross Exit", "entry_trigger": "CLASSIC_GC", "exit_model": "DEATH_CROSS", "initial_sl_atr": 2.5},
        {"strategy_name": "Classic GC (50>200) -> SuperTrend Exit", "entry_trigger": "CLASSIC_GC", "exit_model": "SUPERTREND", "initial_sl_atr": 2.5},

        # --- Group 2: Swing Golden Cross (20 crosses 50 EMA above 200) ---
        {"strategy_name": "Swing GC (20>50) -> Chandelier Exit", "entry_trigger": "SWING_GC", "exit_model": "CHANDELIER", "initial_sl_atr": 2.5},
        {"strategy_name": "Swing GC (20>50) -> 50 EMA Trailing Exit", "entry_trigger": "SWING_GC", "exit_model": "EMA50_CROSS", "initial_sl_atr": 2.5},
        {"strategy_name": "Swing GC (20>50) -> 20 EMA Trailing Exit", "entry_trigger": "SWING_GC", "exit_model": "EMA20_CROSS", "initial_sl_atr": 2.0},
        {"strategy_name": "Swing GC (20>50) -> Hybrid Trailing Exit", "entry_trigger": "SWING_GC", "exit_model": "HYBRID_TRAIL", "initial_sl_atr": 2.5},

        # --- Group 3: Dual Golden Cross (Both 50>200 & 20>50) ---
        {"strategy_name": "Dual GC (All Golden Crosses) -> Chandelier", "entry_trigger": "DUAL_GC", "exit_model": "CHANDELIER", "initial_sl_atr": 2.5},
        {"strategy_name": "Dual GC (All Golden Crosses) -> 50 EMA Exit", "entry_trigger": "DUAL_GC", "exit_model": "EMA50_CROSS", "initial_sl_atr": 2.5},
        {"strategy_name": "Dual GC (All Golden Crosses) -> Hybrid Trail", "entry_trigger": "DUAL_GC", "exit_model": "HYBRID_TRAIL", "initial_sl_atr": 2.5},

        # --- Group 4: Established GC Pullback Bounce ---
        {"strategy_name": "Established GC (50 EMA Pullback Bounce)", "entry_trigger": "ESTABLISHED_PULLBACK", "exit_model": "CHANDELIER", "initial_sl_atr": 2.2},
    ]

    results = []
    for cfg in tqdm(test_matrix, desc="Benchmarking 4H Golden Cross Setups"):
        res = simulate_4h_golden_cross(
            stock_dfs=stock_dfs,
            all_timestamps=all_timestamps,
            initial_cap=100000.0,
            max_alloc_pct=0.30,
            min_alloc_pct=0.15,
            max_positions=3,
            **cfg
        )
        results.append(res)

    results.sort(key=lambda x: x['total_net_pnl'], reverse=True)

    print("\n" + "="*125)
    print("🏆  4H GOLDEN CROSS STRATEGY TOURNAMENT LEADERBOARD (UNLIMITED RUNNERS - NO PROFIT CAP)")
    print("="*125)
    header = f"{'Rank':<4} {'Strategy Configuration':<46} {'Mo Return':<10} {'WinRate':<8} {'ProfitFac':<10} {'MaxDD':<8} {'Avg Win':<9} {'Avg Hold':<9} {'Ending Capital'}"
    print(header)
    print("-"*125)

    for i, r in enumerate(results, 1):
        sign = "+" if r['compounded_monthly'] >= 0 else ""
        print(
            f"{i:<4} {r['strategy']:<46} "
            f"{sign}{r['compounded_monthly']:>5.2f}%/mo   "
            f"{r['win_rate']:>5.1f}%   "
            f"{r['profit_factor']:>7.2f}    "
            f"{r['max_drawdown_pct']:>5.1f}%   "
            f"+{r['avg_win_pct']:>4.1f}%    "
            f"{r['avg_hold_days']:>4.1f}d     "
            f"₹{r['final_equity']:>11,.2f}"
        )

    print("="*125)
    best = results[0]
    print(f"\n🥇 CHAMPION 4H GOLDEN CROSS STRATEGY:")
    print(f"   ► Setup Configuration        : {best['strategy']}")
    print(f"   ► Ending Capital (from ₹1L)  : ₹{best['final_equity']:,.2f} (+₹{best['total_net_pnl']:,.2f} Net Profit)")
    print(f"   ► Compounded Monthly Return  : +{best['compounded_monthly']}% / month (~{best['cagr_pct']}% CAGR)")
    print(f"   ► Win Rate                   : {best['win_rate']}%")
    print(f"   ► Profit Factor              : {best['profit_factor']}")
    print(f"   ► Maximum Drawdown           : {best['max_drawdown_pct']}%")
    print(f"   ► Average Winning Trade Size : +{best['avg_win_pct']}% (Max single winner: +{best['max_single_win_pct']}%)")
    print(f"   ► Average Loss Size          : {best['avg_loss_pct']}% (Reward-to-Risk Ratio: {abs(best['avg_win_pct']/best['avg_loss_pct']):.2f}:1)")
    print(f"   ► Average Holding Duration   : {best['avg_hold_days']} days (~{int(best['avg_hold_days']*2)} four-hour candles)")
    print(f"   ► Consistency (>=3% Months)  : {best['months_over_3pct']}/{best['total_months']} months ({best['consistency_rate']}%)")

if __name__ == "__main__":
    run_golden_cross_tournament()

