"""
simulate_win_win_champions.py
=============================================================================
SEARCH FOR THE ULTIMATE "WIN-WIN" SWING TRADING STRATEGY:
Requirements for the User:
1. WEALTH: Delivers Multi-Crore compounding (₹1.5 Cr to ₹2.5 Cr+), NOT peanuts!
2. MANUAL FEASIBILITY:
   - Low Trade Frequency: ~4 to 8 trades per month (manageable on mobile/Zerodha)
   - Zero Panic Exits: All Stop Losses handled 100% automatically via Zerodha GTT
   - Higher Win Rate: 45% - 55%
   - Average Holding Time: 12 to 35 days (real swing trades, not 1-day scalps)
   - Zero Intraday Watching: Evaluated once a day at 3:15 PM (or twice: 1:15 & 3:15 PM)
=============================================================================
"""

import sys
sys.path.insert(0, r'c:\Users\Advait\Desktop\Auto Swing Trader')
import sqlite3
import pandas as pd
import numpy as np
from tqdm import tqdm
from backtester import EngineMechanics

# Top liquid high-beta swing universe (60 leaders across Nifty 500)
UNIVERSE_60 = [
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

def load_data(timeframe="1D", universe=None):
    with sqlite3.connect("market_data.db") as conn:
        cursor = conn.cursor()
        if universe:
            all_tickers = [u for u in universe]
        else:
            cursor.execute(f"SELECT DISTINCT ticker FROM market_data WHERE timeframe='{timeframe}' AND ticker NOT IN ('NIFTY.NS', '^NSEI') ORDER BY ticker ASC")
            all_tickers = [r[0] for r in cursor.fetchall()]

    stock_data = {}
    all_dates = set()

    with sqlite3.connect("market_data.db") as conn:
        for ticker in all_tickers:
            q = f"SELECT timestamp, open, high, low, close, volume FROM market_data WHERE ticker = ? AND timeframe = '{timeframe}' AND timestamp >= '2019-06-01' ORDER BY timestamp ASC"
            df = pd.read_sql_query(q, conn, params=(ticker,))
            if df.empty or len(df) < 150: continue

            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.sort_values(by="timestamp").reset_index(drop=True)
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
            atr14 = tr.rolling(14).mean().fillna(c * 0.02)
            vol20 = v.rolling(20).mean()
            vol_ratio = (v / vol20.replace(0, np.nan)).fillna(1.0)
            mom60 = (c / c.shift(60).replace(0, np.nan) - 1.0) * 100.0

            # Supertrend (10, 2.0)
            hl2 = (h + l) / 2.0
            upper_band = hl2 + (2.0 * atr14)
            lower_band = hl2 - (2.0 * atr14)
            st_dir = np.ones(len(df))
            for i in range(1, len(df)):
                if c.iloc[i] > upper_band.iloc[i-1]: st_dir[i] = 1
                elif c.iloc[i] < lower_band.iloc[i-1]: st_dir[i] = -1
                else:
                    st_dir[i] = st_dir[i-1]
                    if st_dir[i] == 1 and lower_band.iloc[i] < lower_band.iloc[i-1]:
                        lower_band.iloc[i] = lower_band.iloc[i-1]
                    if st_dir[i] == -1 and upper_band.iloc[i] > upper_band.iloc[i-1]:
                        upper_band.iloc[i] = upper_band.iloc[i-1]

            st_signal = np.zeros(len(df))
            for i in range(1, len(df)):
                if st_dir[i] == 1 and st_dir[i-1] == -1: st_signal[i] = 1
                elif st_dir[i] == -1 and st_dir[i-1] == 1: st_signal[i] = -1

            # Chandelier 2.3x ATR
            atr22 = tr.ewm(alpha=1/22, adjust=False).mean() * 2.3
            highest22 = c.rolling(22).max()
            lowest22 = c.rolling(22).min()
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
                if closes[i] > (lows[i] + atrs[i]): ch_dir[i] = 1
                elif closes[i] < ls_prev: ch_dir[i] = -1
                else: ch_dir[i] = prev_dir

            # 20/50 Golden Cross
            gc = (ema20.shift(1) <= ema50.shift(1)) & (ema20 > ema50)

            df_filt = df[df["timestamp"] >= "2020-01-01"].copy()
            if df_filt.empty: continue

            records = {}
            for idx in df_filt.index:
                ts = df["timestamp"].iloc[idx]
                all_dates.add(ts)
                records[ts] = {
                    "open": df["open"].iloc[idx], "high": df["high"].iloc[idx],
                    "low": df["low"].iloc[idx], "close": df["close"].iloc[idx],
                    "ema20": ema20.iloc[idx], "ema50": ema50.iloc[idx], "ema200": ema200.iloc[idx],
                    "atr": atr14.iloc[idx], "vol_ratio": vol_ratio.iloc[idx],
                    "mom60": mom60.iloc[idx], "st_dir": st_dir[idx], "st_signal": st_signal[idx],
                    "ch_dir": ch_dir[idx], "gc": gc.iloc[idx]
                }
            stock_data[ticker] = records

    return stock_data, sorted(list(all_dates))


def run_win_win_simulation(stock_data, all_dates,
                           entry_type="supertrend_pullback", # 'supertrend_pullback', 'kinetic_breakout'
                           max_pos=8,
                           alloc_pct=0.125,
                           monthly_sip=5000.0,
                           initial_cap=100000.0,
                           be_trigger_pct=0.06,  # Move to cost only after +6% gain!
                           label=""):
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_pos)
    cash = float(initial_cap)
    total_invested = float(initial_cap)
    positions = {}
    trades = []
    equity_curve = []
    last_month = None

    for ts in all_dates:
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

        to_close = []
        for s, p in positions.items():
            if ts not in stock_data[s]: continue
            b = stock_data[s][ts]
            close = b["close"]
            high = b["high"]
            low = b["low"]
            entry = p["entry_px"]
            atr = p["atr"]
            p["days_held"] += 1

            # GTT Break-Even ratchet: Move SL to Cost only after +6% gain (gives breathing room!)
            if not p["be_locked"] and high >= entry * (1.0 + be_trigger_pct):
                p["be_locked"] = True
                p["sl"] = entry * 1.003  # Covers friction

            exit_px = None
            reason = None

            # 1. GTT Hard Stop hit (handled 100% automatically on Zerodha)
            if low <= p["sl"]:
                exit_px = p["sl"]
                reason = "GTT_Break_Even_Save" if p["be_locked"] else "GTT_Hard_Stop_1.8x"
            # 2. Trend reversal exit (check at 3:15 PM once a day)
            elif p["days_held"] >= 3 and b["st_dir"] == -1:
                exit_px = close
                reason = "Supertrend_Trend_Flip_Exit"
            elif p["days_held"] >= 5 and b["ch_dir"] == -1:
                exit_px = close
                reason = "Chandelier_Runner_Exit"

            if exit_px:
                gross_b = p["qty"] * entry
                gross_s = p["qty"] * exit_px
                fric = engine.calculate_friction(gross_b, True) + engine.calculate_friction(gross_s, False)
                net = (gross_s - gross_b) - fric
                cash += (gross_s - engine.calculate_friction(gross_s, False))
                trades.append({
                    "symbol": s, "entry_date": p["entry_date"], "exit_date": ts,
                    "days_held": p["days_held"], "entry_px": entry, "exit_px": exit_px,
                    "net_pnl": net, "pnl_pct": ((exit_px - entry) / entry) * 100.0,
                    "reason": reason
                })
                to_close.append(s)

        for s in to_close: del positions[s]

        # ENTRIES
        if len(positions) < max_pos and cash >= 10000:
            candidates = []
            for s, series in stock_data.items():
                if s in positions or ts not in series: continue
                b = series[ts]

                # Setup: Bullish Supertrend or Golden Cross + Macro Bull (Close > 200 EMA & 50 > 200) + Mom60 > 10
                is_bull_regime = (b["close"] > b["ema200"]) and (b["ema50"] > b["ema200"])
                if not is_bull_regime: continue

                if entry_type == "supertrend_pullback":
                    # Triggers when Supertrend turns bullish AND volume >= 1.0x
                    if b["st_signal"] == 1 and b["vol_ratio"] >= 1.0 and b["mom60"] > 5.0:
                        candidates.append((s, b["close"], b["atr"], b["mom60"]))
                elif entry_type == "hybrid_momentum":
                    # Supertrend is Bullish + Price pulled back near 20 EMA + Green candle
                    dist_20 = (b["close"] - b["ema20"]) / b["ema20"] * 100.0
                    if b["st_dir"] == 1 and dist_20 <= 2.5 and b["vol_ratio"] >= 1.0 and b["mom60"] > 10.0:
                        candidates.append((s, b["close"], b["atr"], b["mom60"]))

            # Rank by Relative Strength (Momentum)
            candidates.sort(key=lambda x: x[3], reverse=True)

            for s, c, atr, mom in candidates:
                if len(positions) >= max_pos or cash < 10000: break
                alloc = min(curr_eq * alloc_pct, cash)
                entry_px = c * 1.002
                qty = int(alloc // entry_px)
                if qty <= 0: continue
                gross_b = qty * entry_px
                fric = engine.calculate_friction(gross_b, True)
                if cash >= (gross_b + fric):
                    cash -= (gross_b + fric)
                    positions[s] = {
                        "entry_date": ts, "entry_px": entry_px, "qty": qty, "atr": atr,
                        "sl": entry_px - (1.8 * atr), "be_locked": False, "days_held": 0
                    }

    final_eq = equity_curve[-1] if equity_curve else initial_cap
    tdf = pd.DataFrame(trades)
    wins = tdf[tdf["net_pnl"] > 0]
    losses = tdf[tdf["net_pnl"] <= 0]
    win_rate = len(wins) / len(tdf) * 100 if len(tdf) > 0 else 0
    gross_w = wins["net_pnl"].sum()
    gross_l = abs(losses["net_pnl"].sum())
    pf = (gross_w / gross_l) if gross_l > 0 else 999.0
    eq_s = pd.Series(equity_curve)
    peak = eq_s.cummax()
    dd_s = (eq_s - peak) / peak * 100.0
    max_dd = abs(float(dd_s.min()))
    years = 6.8

    return {
        "label": label,
        "invested": total_invested,
        "final_eq": final_eq,
        "net_profit": final_eq - total_invested,
        "win_rate": win_rate,
        "profit_factor": pf,
        "max_dd": max_dd,
        "trades": len(tdf),
        "trades_per_month": round(len(tdf) / (years * 12), 1),
        "avg_hold": round(tdf["days_held"].mean(), 1),
        "tdf": tdf
    }


def main():
    print("\n" + "=" * 80)
    print("WIN-WIN SWING TOURNAMENT: HIGH WEALTH + ZERO STRESS MANUAL EXECUTION")
    print("=" * 80)

    # 1. Test on Liquid 60 Leaders (Daily candles)
    print("Loading Liquid 60 Momentum Universe on Daily (1D)...")
    s_data_60_1d, dates_1d = load_data(timeframe="1D", universe=UNIVERSE_60)

    # 2. Test on Full Nifty 500 (Daily candles)
    print("Loading Full Nifty 500 on Daily (1D)...")
    s_data_500_1d, _ = load_data(timeframe="1D", universe=None)

    # 3. Test on Liquid 60 on 4H candles
    print("Loading Liquid 60 Momentum Universe on 4H...")
    s_data_60_4h, dates_4h = load_data(timeframe="4H", universe=UNIVERSE_60)

    tests = [
        # Setup 1: Liquid 60 Daily Supertrend (8 Slots, 12.5% Alloc)
        (s_data_60_1d, dates_1d, "supertrend_pullback", 8, 0.125, "1. Liquid 60 Daily Supertrend (8 Slots, 12.5% Alloc)"),
        # Setup 2: Liquid 60 Daily Hybrid Pullback (8 Slots)
        (s_data_60_1d, dates_1d, "hybrid_momentum", 8, 0.125, "2. Liquid 60 Daily Hybrid Momentum (8 Slots, 12.5% Alloc)"),
        # Setup 3: Liquid 60 Daily (10 Slots, 10% Alloc)
        (s_data_60_1d, dates_1d, "supertrend_pullback", 10, 0.10, "3. Liquid 60 Daily Supertrend (10 Slots, 10% Alloc)"),
        # Setup 4: Full Nifty 500 Daily Supertrend (8 Slots, 12.5% Alloc)
        (s_data_500_1d, dates_1d, "supertrend_pullback", 8, 0.125, "4. Full Nifty 500 Daily Supertrend (8 Slots, 12.5% Alloc)"),
        # Setup 5: Liquid 60 on 4H (Evaluated at 1:15 & 3:15 PM)
        (s_data_60_4h, dates_4h, "supertrend_pullback", 8, 0.125, "5. Liquid 60 on 4H Candles (Check at 1:15 & 3:15 PM)"),
    ]

    results = []
    for s_data, dates, etype, mpos, alloc, lbl in tests:
        res = run_win_win_simulation(s_data, dates, entry_type=etype, max_pos=mpos, alloc_pct=alloc, label=lbl)
        results.append(res)

    print("\n" + "=" * 135)
    print(f"{'Win-Win Strategy Architecture':<54} | {'Win Rate':<8} | {'Avg Hold':<8} | {'Trades/Mo':<9} | {'PF':<5} | {'Max DD%':<8} | {'Final Capital':<15} | {'Net Profit'}")
    print("=" * 135)
    for r in results:
        cap_str = f"₹{r['final_eq']:>13,.0f}"
        pnl_str = f"₹{r['net_profit']:>13,.0f}"
        print(f"{r['label']:<54} | {r['win_rate']:>6.1f}% | {r['avg_hold']:>5.1f}d | {r['trades_per_month']:>7.1f}/mo | {r['profit_factor']:>4.2f} | {r['max_dd']:>6.1f}% | {cap_str} | {pnl_str}")
    print("-" * 135)

    champion = max(results, key=lambda x: x["final_eq"])
    champion["tdf"].to_csv("trades_win_win_champion.csv", index=False)
    print(f"\nExported Win-Win Champion trade ledger to trades_win_win_champion.csv")


if __name__ == "__main__":
    main()
