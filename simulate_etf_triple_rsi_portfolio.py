"""
simulate_etf_triple_rsi_portfolio.py
=============================================================================
Simulates Portfolio Compounding for Triple RSI Strategy on Indian ETFs:
- Initial Capital: ₹50,000
- Monthly SIP: ₹5,000/month
- Database: etf_market_data.db
- Period: Historical data across 10 Indian ETFs
- Compares:
  1. All 10 ETFs Basket (Raw Rules)
  2. All 10 ETFs Basket (With 7% Stop Loss & 8d Time-Stop)
  3. Curated Champion ETF Basket (GOLDBEES, BANKBEES, JUNIORBEES, PHARMABEES, MON100)
  4. Benchmark: Passive NIFTYBEES Monthly SIP
=============================================================================
"""

import sqlite3
import pandas as pd
import numpy as np
from backtester import EngineMechanics

DB_NAME = "etf_market_data.db"

def calculate_rsi(series: pd.Series, period: int = 5) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

def load_etf_series():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT DISTINCT ticker FROM etf_daily ORDER BY ticker")
    tickers = [r[0] for r in c.fetchall()]

    etf_data = {}
    all_timestamps = set()

    for t in tickers:
        q = "SELECT timestamp, open, high, low, close, volume FROM etf_daily WHERE ticker = ? ORDER BY timestamp ASC"
        df = pd.read_sql_query(q, conn, params=(t,))
        if df.empty or len(df) < 220: continue
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values(by="timestamp").reset_index(drop=True)

        close = df["close"]
        df["sma200"] = close.rolling(200).mean()
        df["rsi5"] = calculate_rsi(close, 5)

        r = df["rsi5"]
        c1 = r < 30.0
        c2 = (r < r.shift(1)) & (r.shift(1) < r.shift(2)) & (r.shift(2) < r.shift(3))
        c3 = r.shift(3) < 60.0
        c4 = close > df["sma200"]
        df["buy_signal"] = c1 & c2 & c3 & c4
        df["exit_signal"] = r > 50.0

        d_dict = {}
        for _, row in df.iterrows():
            ts = row["timestamp"]
            all_timestamps.add(ts)
            d_dict[ts] = {
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "rsi5": row["rsi5"],
                "buy_signal": bool(row["buy_signal"]),
                "exit_signal": bool(row["exit_signal"])
            }
        etf_data[t] = d_dict

    conn.close()
    return etf_data, sorted(list(all_timestamps))

def run_etf_portfolio(etf_data, all_ts, 
                      tickers_whitelist=None, 
                      sl_pct=0.0, 
                      max_days=0, 
                      max_pos=4, 
                      alloc_pct=0.25, 
                      initial_cap=50000.0, 
                      monthly_sip=5000.0, 
                      label=""):
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_pos)
    cash = float(initial_cap)
    total_invested = float(initial_cap)
    positions = {}
    trades = []
    equity_curve = []
    last_month = None

    eval_ts = [ts for ts in all_ts if ts >= pd.to_datetime("2015-01-01")]

    for ts in eval_ts:
        cur_m = (ts.year, ts.month)
        if last_month is not None and cur_m != last_month:
            cash += monthly_sip
            total_invested += monthly_sip
        last_month = cur_m

        curr_eq = cash + sum(
            p["qty"] * etf_data[s][ts]["close"] if ts in etf_data[s] else p["qty"] * p["entry_px"]
            for s, p in positions.items()
        )
        equity_curve.append(curr_eq)

        # Exits
        to_close = []
        for s, p in positions.items():
            if ts not in etf_data[s]: continue
            b = etf_data[s][ts]
            close = b["close"]
            low = b["low"]
            entry = p["entry_px"]
            p["days_held"] += 1

            exit_px = None
            reason = None

            if sl_pct > 0 and low <= entry * (1.0 - sl_pct):
                exit_px = entry * (1.0 - sl_pct)
                reason = "Stop_Loss"
            elif b["exit_signal"] and p["days_held"] >= 1:
                exit_px = close * 0.998
                reason = "RSI5_Cross_50"
            elif max_days > 0 and p["days_held"] >= max_days:
                exit_px = close * 0.998
                reason = "Time_Stop"

            if exit_px:
                gross_b = p["qty"] * entry
                gross_s = p["qty"] * exit_px
                fric = engine.calculate_friction(gross_b, True) + engine.calculate_friction(gross_s, False)
                net = (gross_s - gross_b) - fric
                cash += (gross_s - engine.calculate_friction(gross_s, False))
                trades.append({
                    "ticker": s,
                    "entry_date": p["entry_date"],
                    "exit_date": ts,
                    "days_held": p["days_held"],
                    "entry_px": entry,
                    "exit_px": exit_px,
                    "qty": p["qty"],
                    "capital": gross_b,
                    "net_pnl": net,
                    "pnl_pct": ((exit_px - entry) / entry) * 100.0,
                    "reason": reason
                })
                to_close.append(s)

        for s in to_close: del positions[s]

        # Entries
        can_open = len(positions) < max_pos and cash >= 5000.0
        if can_open:
            candidates = []
            target_list = tickers_whitelist if tickers_whitelist else list(etf_data.keys())
            for s in target_list:
                if len(positions) >= max_pos: break
                if s in positions or ts not in etf_data[s]: continue
                b = etf_data[s][ts]
                if b["buy_signal"]:
                    candidates.append((s, b["close"], b["rsi5"]))

            if candidates:
                candidates.sort(key=lambda x: x[2])
                for s, c, _ in candidates:
                    if len(positions) >= max_pos or cash < 5000.0: break
                    target_alloc = min(curr_eq * alloc_pct, cash)
                    if target_alloc < 5000.0: continue
                    entry_px = c * 1.002
                    qty = int(target_alloc // entry_px)
                    if qty <= 0: continue
                    gross_b = qty * entry_px
                    fric = engine.calculate_friction(gross_b, True)
                    if cash >= (gross_b + fric):
                        cash -= (gross_b + fric)
                        positions[s] = {
                            "entry_date": ts,
                            "entry_px": entry_px,
                            "qty": qty,
                            "days_held": 0
                        }

    final_eq = equity_curve[-1] if equity_curve else initial_cap
    tdf = pd.DataFrame(trades)
    wins = tdf[tdf["net_pnl"] > 0]
    gross_w = wins["net_pnl"].sum()
    gross_l = abs(tdf[tdf["net_pnl"] <= 0]["net_pnl"].sum())
    pf = (gross_w / gross_l) if gross_l > 0 else 999.0
    eq_s = pd.Series(equity_curve)
    peak = eq_s.cummax()
    dd_s = (eq_s - peak) / peak * 100.0
    max_dd = abs(float(dd_s.min()))
    years = (eval_ts[-1] - eval_ts[0]).days / 365.25

    return {
        "label": label,
        "invested": total_invested,
        "final_eq": final_eq,
        "net_profit": final_eq - total_invested,
        "return_pct": ((final_eq - total_invested) / total_invested) * 100.0,
        "trades": len(tdf),
        "win_rate": len(wins) / len(tdf) * 100.0 if len(tdf) > 0 else 0,
        "pf": pf,
        "max_dd": max_dd,
        "years": years,
        "tdf": tdf
    }

# Also benchmark passive NIFTYBEES SIP
def run_passive_niftybees_sip(initial_cap=50000.0, monthly_sip=5000.0):
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT timestamp, close FROM etf_daily WHERE ticker = 'NIFTYBEES.NS' ORDER BY timestamp ASC", conn)
    conn.close()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df[df["timestamp"] >= pd.to_datetime("2015-01-01")].sort_values(by="timestamp").reset_index(drop=True)

    cash = float(initial_cap)
    total_invested = float(initial_cap)
    qty = 0
    last_month = None

    # Buy initial holding
    first_p = df["close"].iloc[0]
    qty = int(cash // first_p)
    cash -= qty * first_p

    equity_curve = []

    for idx, row in df.iterrows():
        ts = row["timestamp"]
        p = row["close"]
        cur_m = (ts.year, ts.month)
        if last_month is not None and cur_m != last_month:
            total_invested += monthly_sip
            add_qty = int(monthly_sip // p)
            qty += add_qty
            cash += (monthly_sip - add_qty * p)
        last_month = cur_m
        equity_curve.append(cash + qty * p)

    eq_s = pd.Series(equity_curve)
    peak = eq_s.cummax()
    dd_s = (eq_s - peak) / peak * 100.0

    final_eq = equity_curve[-1]
    years = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days / 365.25

    return {
        "label": "Benchmark: Passive NIFTYBEES Monthly SIP",
        "invested": total_invested,
        "final_eq": final_eq,
        "net_profit": final_eq - total_invested,
        "return_pct": ((final_eq - total_invested) / total_invested) * 100.0,
        "trades": int(years * 12),
        "win_rate": 100.0,
        "pf": 999.0,
        "max_dd": abs(float(dd_s.min())),
        "years": years
    }

def main():
    etf_data, all_ts = load_etf_series()

    print("=" * 115)
    print("PORTFOLIO COMPOUNDING SIMULATION: ₹50,000 Initial + ₹5,000/Month SIP (2015-2026)")
    print("=" * 115)

    champions = ["GOLDBEES.NS", "BANKBEES.NS", "JUNIORBEES.NS", "PHARMABEES.NS", "MON100.NS"]

    tests = [
        # 1. All 10 ETFs - Raw Triple RSI (4 slots, 25% alloc)
        run_etf_portfolio(etf_data, all_ts, None, sl_pct=0.0, max_days=0, max_pos=4, alloc_pct=0.25, label="1. Triple RSI (All 10 ETFs, No Stop Loss)"),
        
        # 2. All 10 ETFs - 7% Stop Loss + 8-Day Time Stop
        run_etf_portfolio(etf_data, all_ts, None, sl_pct=0.07, max_days=8, max_pos=4, alloc_pct=0.25, label="2. Triple RSI (All 10 ETFs + 7% SL + 8d Time)"),

        # 3. Champion Basket (Gold, Bank, Junior, Pharma, Mon100) - Raw
        run_etf_portfolio(etf_data, all_ts, champions, sl_pct=0.0, max_days=0, max_pos=4, alloc_pct=0.25, label="3. Champion Basket (Gold, Bank, Jr, Pharma, MON100 - Raw)"),

        # 4. Champion Basket - 7% Stop Loss + 8-Day Time Stop
        run_etf_portfolio(etf_data, all_ts, champions, sl_pct=0.07, max_days=8, max_pos=4, alloc_pct=0.25, label="4. Champion Basket (+ 7% SL + 8d Time-Stop)"),

        # 5. Passive NIFTYBEES Benchmark
        run_passive_niftybees_sip()
    ]

    print(f"{'Strategy / Portfolio Model':<55} | {'Invested':<10} | {'Final Capital':<15} | {'Net Profit':<14} | {'Gain %':<7} | {'Win %':<6} | {'Max DD%'}")
    print("=" * 135)
    for r in tests:
        inv_str = f"₹{r['invested']:>8,.0f}"
        cap_str = f"₹{r['final_eq']:>12,.0f}"
        pnl_str = f"₹{r['net_profit']:>11,.0f}"
        print(f"{r['label']:<55} | {inv_str} | {cap_str} | {pnl_str} | {r['return_pct']:>5.1f}% | {r['win_rate']:>5.1f}% | {r['max_dd']:>5.1f}%")
    print("-" * 135)

    # Save ledger
    tests[3]["tdf"].to_csv("trades_champion_etf_portfolio.csv", index=False)
    print("Saved champion ETF portfolio ledger to trades_champion_etf_portfolio.csv")

if __name__ == "__main__":
    main()
