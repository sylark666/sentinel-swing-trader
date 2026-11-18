import sqlite3
import pandas as pd
import numpy as np
from backtester import EngineMechanics
from test_triple_rsi_strategy import load_and_prep_triple_rsi

stock_data, all_ts = load_and_prep_triple_rsi()

# Nifty 50 Universe
NIFTY_50 = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "BHARTIARTL.NS", "SBIN.NS", "LTIM.NS", "BAJFINANCE.NS", "HINDUNILVR.NS",
    "ITC.NS", "LT.NS", "KOTAKBANK.NS", "AXISBANK.NS", "TITAN.NS",
    "TATAMOTORS.NS", "MARUTI.NS", "SUNPHARMA.NS", "TRENT.NS", "HAL.NS",
    "DIXON.NS", "ZOMATO.NS", "ADANIENT.NS", "ADANIPORTS.NS", "COALINDIA.NS",
    "NTPC.NS", "POWERGRID.NS", "ONGC.NS", "JSWSTEEL.NS", "TATASTEEL.NS",
    "HINDALCO.NS", "VEDL.NS", "BHEL.NS", "PFC.NS", "RECLTD.NS",
    "CUMMINSIND.NS", "SIEMENS.NS", "BEL.NS", "CIPLA.NS", "DRREDDY.NS"
]

def run_custom_triple_rsi(universe="all", require_green_candle=False, target_rsi=50, sl_pct=0.08):
    engine = EngineMechanics(initial_capital=100000.0, max_positions=5)
    cash = 100000.0
    total_invested = 100000.0
    positions = {}
    trades = []
    equity_curve = []
    last_month = None

    for ts in all_ts:
        cur_m = (ts.year, ts.month)
        if last_month is not None and cur_m != last_month:
            cash += 5000.0
            total_invested += 5000.0
        last_month = cur_m

        curr_eq = cash + sum(
            p["qty"] * stock_data[s][ts]["close"] if ts in stock_data[s] else p["qty"] * p["entry_px"]
            for s, p in positions.items()
        )
        equity_curve.append(curr_eq)

        # Exits
        to_close = []
        for s, p in positions.items():
            if ts not in stock_data[s]: continue
            b = stock_data[s][ts]
            close = b["close"]
            low = b["low"]
            entry = p["entry_px"]
            p["days_held"] += 1

            exit_px = None
            reason = None

            if sl_pct > 0 and low <= entry * (1.0 - sl_pct):
                exit_px = entry * (1.0 - sl_pct)
                reason = "Stop_Loss"
            elif b["rsi5"] >= target_rsi and p["days_held"] >= 1:
                exit_px = close * 0.998
                reason = f"RSI5_Cross_{target_rsi}"
            elif p["days_held"] >= 15:
                exit_px = close * 0.998
                reason = "Time_Exit_15d"

            if exit_px:
                gross_b = p["qty"] * entry
                gross_s = p["qty"] * exit_px
                fric = engine.calculate_friction(gross_b, True) + engine.calculate_friction(gross_s, False)
                net = (gross_s - gross_b) - fric
                cash += (gross_s - engine.calculate_friction(gross_s, False))
                trades.append({
                    "symbol": s,
                    "net_pnl": net,
                    "pnl_pct": ((exit_px - entry) / entry) * 100.0,
                    "reason": reason,
                    "days_held": p["days_held"]
                })
                to_close.append(s)

        for s in to_close: del positions[s]

        # Entries
        can_open = len(positions) < 5 and cash >= 10000.0
        if can_open:
            candidates = []
            target_stocks = NIFTY_50 if universe == "nifty50" else list(stock_data.keys())
            for s in target_stocks:
                if len(positions) >= 5 or s not in stock_data: break
                if s in positions or ts not in stock_data[s]: continue
                b = stock_data[s][ts]
                if b["buy_signal"]:
                    if require_green_candle and b["close"] <= b["open"]:
                        continue
                    candidates.append((s, b["close"], b["rsi5"]))

            if candidates:
                candidates.sort(key=lambda x: x[2])
                for s, c, _ in candidates:
                    if len(positions) >= 5 or cash < 10000.0: break
                    target_alloc = min(curr_eq * 0.20, cash)
                    if target_alloc < 10000.0: continue
                    entry_px = c * 1.002
                    qty = int(target_alloc // entry_px)
                    if qty <= 0: continue
                    gross_b = qty * entry_px
                    fric = engine.calculate_friction(gross_b, True)
                    if cash >= (gross_b + fric):
                        cash -= (gross_b + fric)
                        positions[s] = {
                            "entry_px": entry_px,
                            "qty": qty,
                            "days_held": 0
                        }

    final_eq = equity_curve[-1] if equity_curve else 100000.0
    tdf = pd.DataFrame(trades)
    wins = tdf[tdf["net_pnl"] > 0]
    gross_w = wins["net_pnl"].sum()
    gross_l = abs(tdf[tdf["net_pnl"] <= 0]["net_pnl"].sum())
    pf = (gross_w / gross_l) if gross_l > 0 else 999.0
    eq_s = pd.Series(equity_curve)
    peak = eq_s.cummax()
    dd_s = (eq_s - peak) / peak * 100.0
    max_dd = abs(float(dd_s.min()))
    years = 6.8
    trades_per_month = len(tdf) / (years * 12)

    return {
        "final_eq": final_eq,
        "net_profit": final_eq - total_invested,
        "win_rate": len(wins) / len(tdf) * 100 if len(tdf) > 0 else 0,
        "profit_factor": pf,
        "max_dd": max_dd,
        "trades": len(tdf),
        "trades_per_month": round(trades_per_month, 1),
        "avg_hold": round(tdf["days_held"].mean(), 1) if len(tdf) > 0 else 0
    }

print("\n" + "=" * 120)
print(f"{'Variant':<42} | {'Trades/Mo':<10} | {'Win Rate %':<11} | {'PF':<6} | {'Max DD%':<8} | {'Final Capital':<15} | {'Net Profit'}")
print("=" * 120)

configs = [
    ("1. Raw Triple RSI (All Nifty 500, SL 8%)", "all", False, 50, 0.08),
    ("2. Green Reversal Candle (Close > Open)", "all", True, 50, 0.08),
    ("3. Nifty 50 Large Caps Only (SL 8%)", "nifty50", False, 50, 0.08),
    ("4. Nifty 50 + Green Reversal Candle", "nifty50", True, 50, 0.08),
    ("5. Nifty 50 + Target RSI 65 (Let Run)", "nifty50", True, 65, 0.08),
]

for lbl, u, green, tgt, sl in configs:
    r = run_custom_triple_rsi(universe=u, require_green_candle=green, target_rsi=tgt, sl_pct=sl)
    print(f"{lbl:<42} | {r['trades_per_month']:>8.1f}/mo | {r['win_rate']:>9.1f}% | {r['profit_factor']:>4.2f} | {r['max_dd']:>6.1f}% | ₹{r['final_eq']:>13,.0f} | ₹{r['net_profit']:>13,.0f}")
print("-" * 120)
