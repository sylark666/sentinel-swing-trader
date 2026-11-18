"""
export_manual_pullback_champion.py
=============================================================================
Exports full rich trade ledger for Champion Manual Pullback Springboard:
- Daily (1D) Candlestick Timeframe
- 5 Concurrent Slots (20% Dynamic Allocation per trade)
- ₹5,000/Month SIP Capital Injection
- Universe: All Nifty 500 Stocks (2020-2026)
- 2-Stage Zerodha GTT Execution (Target 1 +2.5R, Runner Chandelier 2.5x ATR)
=============================================================================
"""

import sys
import os
import sqlite3
import pandas as pd
import numpy as np
from tqdm import tqdm
from backtester import EngineMechanics

def run_and_export():
    print("Loading 1D candles from market_data.db...")
    with sqlite3.connect("market_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT ticker FROM market_data WHERE timeframe='1D' AND ticker NOT IN ('NIFTY.NS', '^NSEI') ORDER BY ticker ASC")
        all_tickers = [r[0] for r in cursor.fetchall()]

    stock_data = {}
    all_dates = set()

    with sqlite3.connect("market_data.db") as conn:
        for ticker in tqdm(all_tickers, desc="Processing 1D Candles"):
            q = "SELECT timestamp, open, high, low, close, volume FROM market_data WHERE ticker = ? AND timeframe = '1D' AND timestamp >= '2019-06-01' ORDER BY timestamp ASC"
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

            # Chandelier 2.5x ATR
            atr22 = tr.ewm(alpha=1/22, adjust=False).mean() * 2.5
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

            # Pullback to 20 EMA
            dist_ema20 = (l - ema20) / ema20 * 100.0
            pullback_touch = (dist_ema20 >= -2.0) & (dist_ema20 <= 1.5)
            bullish_candle = c > df["open"]
            pullback_signal = pullback_touch & bullish_candle & (c > ema50) & (ema50 > ema200)

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
                    "mom60": mom60.iloc[idx], "pullback_signal": bool(pullback_signal.iloc[idx]),
                    "ch_dir": ch_dir[idx]
                }
            stock_data[ticker] = records

    all_dates = sorted(list(all_dates))

    initial_cap = 100000.0
    monthly_sip = 5000.0
    max_pos = 5
    alloc_pct = 0.20
    target1_r = 2.5

    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_pos)
    cash = float(initial_cap)
    total_invested = float(initial_cap)
    positions = {}
    trades = []
    equity_curve = []
    last_month = None

    print(f"Simulating across {len(all_dates)} trading days...")

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

            # GTT Target 1: Book 50% at +2.5R (+4.5x ATR)
            target_1 = entry + (target1_r * 1.8 * atr)
            if not p["target1_hit"] and high >= target_1:
                p["target1_hit"] = True
                p["sl"] = entry * 1.002  # GTT auto-moves to cost
                half_qty = int(p["qty"] * 0.50)
                if half_qty > 0:
                    gross_s = half_qty * target_1
                    gross_b = half_qty * entry
                    fric = engine.calculate_friction(gross_b, True) + engine.calculate_friction(gross_s, False)
                    net = (gross_s - gross_b) - fric
                    cash += (gross_s - engine.calculate_friction(gross_s, False))
                    trades.append({
                        "Trade_No": len(trades) + 1,
                        "Ticker": s,
                        "Entry_Date": str(p["entry_date"])[:10],
                        "Exit_Date": str(ts)[:10],
                        "Holding_Days": p["days_held"],
                        "Entry_Price": round(entry, 2),
                        "Exit_Price": round(target_1, 2),
                        "Quantity": half_qty,
                        "Capital_Invested": round(gross_b, 2),
                        "Gross_Exit_Value": round(gross_s, 2),
                        "Gross_PnL": round(gross_s - gross_b, 2),
                        "Zerodha_Friction_Taxes": round(fric, 2),
                        "Net_Realized_PnL": round(net, 2),
                        "Return_Pct": round(((target_1 - entry) / entry) * 100.0, 2),
                        "Exit_Reason": f"GTT_Target_{target1_r}R_50%_Locked",
                        "Category": "Win (50% Target)",
                        "Portfolio_Equity_After_Trade": round(curr_eq + net, 2),
                        "Year": ts.year,
                        "Month": f"{ts.year}-{ts.month:02d}"
                    })
                    p["qty"] -= half_qty

            exit_px = None
            reason = None

            # Hard stop
            if low <= p["sl"]:
                exit_px = p["sl"]
                reason = "GTT_Break_Even_Stop" if p["target1_hit"] else "GTT_Initial_Stop"
            elif p["target1_hit"] and p["days_held"] >= 5 and b["ch_dir"] == -1:
                exit_px = close
                reason = "Runner_Exit_Chandelier"

            if exit_px and p["qty"] > 0:
                gross_b = p["qty"] * entry
                gross_s = p["qty"] * exit_px
                fric = engine.calculate_friction(gross_b, True) + engine.calculate_friction(gross_s, False)
                net = (gross_s - gross_b) - fric
                cash += (gross_s - engine.calculate_friction(gross_s, False))
                cat = "Win (Runner)" if net > 0 else ("Break-Even (Protected)" if reason == "GTT_Break_Even_Stop" else "Loss (Stop Hit)")
                trades.append({
                    "Trade_No": len(trades) + 1,
                    "Ticker": s,
                    "Entry_Date": str(p["entry_date"])[:10],
                    "Exit_Date": str(ts)[:10],
                    "Holding_Days": p["days_held"],
                    "Entry_Price": round(entry, 2),
                    "Exit_Price": round(exit_px, 2),
                    "Quantity": p["qty"],
                    "Capital_Invested": round(gross_b, 2),
                    "Gross_Exit_Value": round(gross_s, 2),
                    "Gross_PnL": round(gross_s - gross_b, 2),
                    "Zerodha_Friction_Taxes": round(fric, 2),
                    "Net_Realized_PnL": round(net, 2),
                    "Return_Pct": round(((exit_px - entry) / entry) * 100.0, 2),
                    "Exit_Reason": reason,
                    "Category": cat,
                    "Portfolio_Equity_After_Trade": round(curr_eq + net, 2),
                    "Year": ts.year,
                    "Month": f"{ts.year}-{ts.month:02d}"
                })
                to_close.append(s)

        for s in to_close: del positions[s]

        # Entries: Rank candidates by 60-day relative strength
        if len(positions) < max_pos and cash >= 10000:
            candidates = []
            for s, series in stock_data.items():
                if s in positions or ts not in series: continue
                b = series[ts]
                if b["pullback_signal"] and b["close"] > b["ema200"] and b["mom60"] > 10.0 and b["vol_ratio"] >= 1.0:
                    candidates.append((s, b["close"], b["atr"], b["mom60"]))

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
                        "sl": entry_px - (1.8 * atr), "target1_hit": False, "days_held": 0
                    }

    tdf = pd.DataFrame(trades)
    csv_file = "trades_manual_pullback_champion.csv"
    tdf.to_csv(csv_file, index=False)

    final_eq = equity_curve[-1] if equity_curve else initial_cap
    wins = tdf[tdf["Net_Realized_PnL"] > 0]
    losses = tdf[tdf["Net_Realized_PnL"] <= 0]
    gross_w = wins["Net_Realized_PnL"].sum()
    gross_l = abs(losses["Net_Realized_PnL"].sum())
    pf = (gross_w / gross_l) if gross_l > 0 else 999.0
    eq_s = pd.Series(equity_curve)
    peak = eq_s.cummax()
    dd_s = (eq_s - peak) / peak * 100.0
    max_dd = abs(float(dd_s.min()))

    print("\n" + "=" * 80)
    print("MANUAL PULLBACK CHAMPION SIMULATION COMPLETED")
    print("=" * 80)
    print(f"  • Total Trades Logged:       {len(tdf)}")
    print(f"  • File Saved:                {csv_file}")
    print(f"  • Total Capital Invested:    ₹{total_invested:,.2f}")
    print(f"  • Final Portfolio Value:     ₹{final_eq:,.2f}")
    print(f"  • Net Realized Profit:       ₹{final_eq - total_invested:,.2f} (+{(final_eq - total_invested)/total_invested * 100:.1f}%)")
    print(f"  • Win Rate:                  {len(wins) / len(tdf) * 100:.1f}% ({len(wins)} Wins / {len(losses)} Losses)")
    print(f"  • Profit Factor:             {pf:.2f}")
    print(f"  • Max Drawdown:              {max_dd:.1f}%")
    print(f"  • Average Holding Days:      {tdf['Holding_Days'].mean():.1f} days")
    print(f"  • Average Trades per Year:   {len(tdf) / 6.8:.1f} trades / year (~3.9 trades / month)")

    print("\nYearly Performance Breakdown:")
    yearly = tdf.groupby("Year").agg(
        Trades=("Trade_No", "count"),
        Wins=("Category", lambda x: (x.str.contains("Win")).sum()),
        Losses=("Category", lambda x: (x.str.contains("Loss")).sum()),
        Net_PnL=("Net_Realized_PnL", "sum"),
        Taxes=("Zerodha_Friction_Taxes", "sum")
    )
    print(yearly.to_string())

if __name__ == "__main__":
    run_and_export()
