import sqlite3
import pandas as pd
import numpy as np
import time
from tqdm import tqdm
from backtester import EngineMechanics

# 1. Load universe from market_data.db
print("Connecting to market_data.db...")
with sqlite3.connect('market_data.db') as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT ticker FROM market_data WHERE ticker != 'NIFTY' ORDER BY ticker ASC")
    all_syms = [r[0] for r in cursor.fetchall()]

def compute_indicators(df: pd.DataFrame, ch_mult: float = 2.3) -> pd.DataFrame:
    df = df.copy()
    c = df['close']
    h = df['high']
    l = df['low']
    
    # EMAs
    df['EMA20'] = c.ewm(span=20, adjust=False).mean()
    df['EMA50'] = c.ewm(span=50, adjust=False).mean()
    df['EMA200'] = c.ewm(span=200, adjust=False).mean()

    # Golden cross 20/50
    prev_ema20 = df['EMA20'].shift(1)
    prev_ema50 = df['EMA50'].shift(1)
    df['GC_20_50'] = (prev_ema20 <= prev_ema50) & (df['EMA20'] > df['EMA50'])

    # True Range & ATR
    hl = h - l
    hc = np.abs(h - c.shift())
    lc = np.abs(l - c.shift())
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(window=14).mean().fillna(c * 0.02)

    # Volume Surge
    df['Vol_SMA20'] = df['volume'].rolling(window=20).mean()
    df['Vol_Surge'] = (df['volume'] / df['Vol_SMA20'].replace(0, np.nan)).fillna(1.0)

    # Chandelier Trailing Exit (22, ch_mult)
    atr22 = tr.ewm(alpha=1/22, adjust=False).mean() * ch_mult
    highest22 = c.rolling(window=22).max()
    lowest22 = c.rolling(window=22).min()

    closes = c.values
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
    return df

def load_universe_with_ch_mult(ch_mult: float):
    print(f"\nLoading raw candles and computing indicators with Chandelier {ch_mult}x ATR...")
    t0 = time.time()
    stock_dfs = {}
    all_timestamps = set()

    with sqlite3.connect('market_data.db') as conn:
        for sym in tqdm(all_syms, desc=f"Indicators (Chandelier {ch_mult}x)"):
            query = f"""
                SELECT timestamp, open, high, low, close, volume 
                FROM market_data 
                WHERE ticker = ? AND timeframe = '4H' AND timestamp >= '2018-01-01'
                ORDER BY timestamp ASC
            """
            df = pd.read_sql_query(query, conn, params=(f"{sym}.NS",))
            if df.empty or len(df) < 200:
                continue

            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.set_index('timestamp')
            df = compute_indicators(df, ch_mult=ch_mult)
            
            # Convert to dict of records for high-speed simulation
            # We keep what the engine needs: close, open, high, low, ATR, Vol_Surge, GC_20_50, EMA200, Chandelier_Dir
            records = df[['close', 'open', 'high', 'low', 'ATR', 'Vol_Surge', 'GC_20_50', 'EMA200', 'Chandelier_Dir']].to_dict('index')
            stock_dfs[sym] = records
            all_timestamps.update(records.keys())

    all_ts_sorted = sorted(list(all_timestamps))
    print(f"Loaded {len(stock_dfs)} stocks across {len(all_ts_sorted)} timestamps in {time.time()-t0:.1f}s.")
    return stock_dfs, all_ts_sorted

def run_simulation(stock_dfs, all_ts, ch_mult_label="2.3"):
    initial_cap = 100000.0
    monthly_sip = 5000.0
    alloc_pct = 0.30
    max_positions = 3
    initial_sl_atr = 2.3

    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_positions)
    available_cash = float(initial_cap)
    total_invested = float(initial_cap)
    open_positions = {}
    closed_trades = []
    equity_curve = []
    last_month = None

    for ts in all_ts:
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

        equity_curve.append(current_equity)

        dyn_max_trade = current_equity * alloc_pct
        dyn_min_trade = current_equity * (alloc_pct * 0.5)

        # 1. EXITS
        to_close = []
        for sym, pos in open_positions.items():
            pos['bars_held'] = pos.get('bars_held', 0) + 1
            if ts not in stock_dfs[sym]: continue

            bar = stock_dfs[sym][ts]
            close = bar['close']
            high = bar['high']
            low = bar['low']
            entry = pos['entry_price']

            if high > pos.get('peak_high', entry):
                pos['peak_high'] = high
            peak = pos.get('peak_high', entry)
            max_runup_pct = ((peak - entry) / entry) * 100

            if bar.get('Chandelier_Dir', 0) == 1:
                pos['saw_chandelier_bullish'] = True

            exit_price = None
            exit_reason = None

            # Hard stop
            if initial_sl_atr > 0 and low <= pos['hard_stop']:
                exit_price = pos['hard_stop']
                exit_reason = "Initial ATR Safety Stop"

            elif pos['bars_held'] >= 2:
                ch_dir = bar.get('Chandelier_Dir', 0)
                if pos.get('saw_chandelier_bullish', False) and ch_dir == -1:
                    exit_price = close
                    exit_reason = "Chandelier Trend Reversal"
                elif pos['bars_held'] >= 6 and ch_dir == -1:
                    exit_price = close
                    exit_reason = "Failed Breakout Consolidation"

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
                    "exit_date": str(ts),
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "max_runup_pct": max_runup_pct,
                    "net_pnl": net_pnl,
                    "pnl_pct": pnl_pct,
                    "exit_reason": exit_reason,
                    "days_held": pos['bars_held'] / 2.0
                })
                to_close.append(sym)

        for sym in to_close:
            del open_positions[sym]

        # 2. ENTRIES
        candidates = []
        for sym, bars in stock_dfs.items():
            if ts not in bars or sym in open_positions: continue
            b = bars[ts]
            c, atr = b['close'], b['ATR']
            if b['GC_20_50'] and c > b['EMA200']:
                if b.get('Vol_Surge', 1.0) >= 0.8:
                    alpha = 70.0 + 30
                    if b['Vol_Surge'] >= 1.3: alpha += 15
                    if b['Chandelier_Dir'] == 1: alpha += 10
                    candidates.append((sym, c, atr, alpha, b.get('Chandelier_Dir', 0) == 1))

        candidates.sort(key=lambda x: x[3], reverse=True)

        for sym, c, atr, alpha, is_ch_bull in candidates:
            if len(open_positions) >= max_positions or available_cash < dyn_min_trade:
                break
            alloc = min(dyn_max_trade, available_cash)
            if alloc < dyn_min_trade: continue

            entry_price = c * 1.002
            qty = int(alloc // entry_price)
            if qty <= 0: continue

            gross_buy = qty * entry_price
            fric_buy = engine.calculate_friction(gross_buy, True)
            if available_cash < (gross_buy + fric_buy): continue
            available_cash -= (gross_buy + fric_buy)
            hard_stop = entry_price - (initial_sl_atr * atr)

            open_positions[sym] = {
                "qty": qty, "entry_price": entry_price, "hard_stop": hard_stop,
                "bars_held": 0, "saw_chandelier_bullish": is_ch_bull,
                "peak_high": entry_price, "entry_date": str(ts)
            }

    final_equity = equity_curve[-1] if equity_curve else available_cash
    tdf = pd.DataFrame(closed_trades)
    net_profit = final_equity - total_invested
    win_rate = len(tdf[tdf['net_pnl'] > 0]) / len(tdf) * 100 if len(tdf) > 0 else 0
    wins = tdf[tdf['net_pnl'] > 0]['net_pnl'].sum()
    losses = abs(tdf[tdf['net_pnl'] < 0]['net_pnl'].sum())
    pf = wins / losses if losses > 0 else 999.0

    # Max Drawdown
    eq_series = pd.Series(equity_curve)
    peak_series = eq_series.cummax()
    dd_series = (eq_series - peak_series) / peak_series * 100
    max_dd = abs(dd_series.min())

    return {
        "ch_mult": ch_mult_label,
        "invested": total_invested,
        "final_equity": final_equity,
        "net_profit": net_profit,
        "trades_count": len(tdf),
        "win_rate": win_rate,
        "profit_factor": pf,
        "max_drawdown": max_dd,
        "trades_df": tdf
    }

if __name__ == "__main__":
    # Test 2.3 ATR Chandelier
    stock_dfs_23, all_ts_23 = load_universe_with_ch_mult(2.3)
    res_23 = run_simulation(stock_dfs_23, all_ts_23, ch_mult_label="2.3 ATR Chandelier")

    # Test 3.0 ATR Chandelier (Baseline for comparison)
    stock_dfs_30, all_ts_30 = load_universe_with_ch_mult(3.0)
    res_30 = run_simulation(stock_dfs_30, all_ts_30, ch_mult_label="3.0 ATR Chandelier (Baseline)")

    # Also test intermediate 2.5 ATR
    stock_dfs_25, all_ts_25 = load_universe_with_ch_mult(2.5)
    res_25 = run_simulation(stock_dfs_25, all_ts_25, ch_mult_label="2.5 ATR Chandelier")

    summary_df = pd.DataFrame([{
        "Chandelier Multiplier": r['ch_mult'],
        "Final Portfolio": f"₹{r['final_equity']:,.2f}",
        "Net Profit": f"₹{r['net_profit']:,.2f}",
        "Win Rate %": f"{r['win_rate']:.1f}%",
        "Profit Factor": f"{r['profit_factor']:.2f}",
        "Max Drawdown %": f"{r['max_drawdown']:.1f}%",
        "Total Trades": r['trades_count']
    } for r in [res_30, res_25, res_23]])

    print("\n" + "="*85)
    print("CHANDELIER MULTIPLIER COMPARISON (3.0 vs 2.5 vs 2.3 ATR)")
    print("="*85)
    print(summary_df.to_string(index=False))

    # Save trades df for deep analysis of the 45 check trades
    res_23['trades_df'].to_csv("trades_chandelier_2_3.csv", index=False)
    res_30['trades_df'].to_csv("trades_chandelier_3_0.csv", index=False)

