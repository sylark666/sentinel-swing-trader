import pandas as pd
import numpy as np
from backtester import EngineMechanics
from test_triple_rsi_strategy import load_and_prep_triple_rsi

stock_data, all_ts = load_and_prep_triple_rsi()

def test_rsi_with_stops(sl_pct=0.06):
    engine = EngineMechanics(initial_capital=100000.0, max_positions=10)
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

            # Hard Stop Loss Check!
            if sl_pct > 0 and low <= entry * (1.0 - sl_pct):
                exit_px = entry * (1.0 - sl_pct)
                reason = "Hard_Stop_Loss"
            elif b["exit_signal"] and p["days_held"] >= 1:
                exit_px = close * 0.998
                reason = "RSI5_Cross_50"
            elif p["days_held"] >= 20:
                exit_px = close * 0.998
                reason = "Max_Days_Timeout"

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
        can_open = len(positions) < 10 and cash >= 5000.0
        if can_open:
            candidates = []
            for s, series in stock_data.items():
                if len(positions) >= 10: break
                if s in positions or ts not in series: continue
                b = series[ts]
                if b["buy_signal"]:
                    candidates.append((s, b["close"], b["rsi5"]))

            if candidates:
                candidates.sort(key=lambda x: x[2])
                for s, c, _ in candidates:
                    if len(positions) >= 10 or cash < 5000.0: break
                    target_alloc = min(curr_eq * 0.10, cash)
                    if target_alloc < 5000.0: continue
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
        "sl": sl_pct,
        "final_eq": final_eq,
        "net_profit": final_eq - total_invested,
        "win_rate": len(wins) / len(tdf) * 100 if len(tdf) > 0 else 0,
        "profit_factor": pf,
        "max_dd": max_dd,
        "trades": len(tdf),
        "trades_per_month": round(trades_per_month, 1),
        "avg_hold": round(tdf["days_held"].mean(), 1) if len(tdf) > 0 else 0
    }

print("\n" + "=" * 110)
print(f"{'SL Setting':<20} | {'Trades/Mo':<10} | {'Win Rate %':<11} | {'PF':<6} | {'Max DD%':<8} | {'Final Capital':<15} | {'Net Profit'}")
print("=" * 110)
for sl in [0.0, 0.05, 0.07, 0.10, 0.12]:
    r = test_rsi_with_stops(sl)
    lbl = f"No Stop Loss" if sl == 0.0 else f"Hard Stop -{sl*100:.0f}%"
    print(f"{lbl:<20} | {r['trades_per_month']:>8.1f}/mo | {r['win_rate']:>9.1f}% | {r['profit_factor']:>4.2f} | {r['max_dd']:>6.1f}% | ₹{r['final_eq']:>13,.0f} | ₹{r['net_profit']:>13,.0f}")
print("-" * 110)
