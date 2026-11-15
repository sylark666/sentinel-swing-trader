import sqlite3
import pandas as pd
import numpy as np
from golden_cross_4h_optimizer import load_4h_universe
from backtester import EngineMechanics

with sqlite3.connect('market_data.db') as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT ticker FROM market_data WHERE ticker != 'NIFTY' ORDER BY ticker ASC")
    all_syms = [r[0] for r in cursor.fetchall()]

stock_dfs, all_ts = load_4h_universe(start_date="2018-01-01", symbols=all_syms, timeframe="4H")

def test_smart_exit(
    be_threshold=15.0,        # Min run-up % to activate protection
    lock_pct=0.0,             # Profit % locked when threshold reached (0.0 = true break-even)
    partial_take_profit=0.0,  # e.g., 0.50 (sell 50% at target)
    partial_target=12.0,      # Target % for partial book
    initial_cap=100000.0,
    monthly_sip=5000.0,
    alloc_pct=0.30,
    max_positions=3,
    initial_sl_atr=2.3
):
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_positions)
    available_cash = float(initial_cap)
    total_invested = float(initial_cap)
    open_positions = {}
    closed_trades = []
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

            # Partial profit booking
            if partial_take_profit > 0 and not pos.get('partial_booked', False):
                if max_runup_pct >= partial_target:
                    # Sell partial_take_profit portion at partial target price
                    part_qty = int(pos['qty'] * partial_take_profit)
                    if part_qty > 0:
                        part_sell_price = entry * (1.0 + partial_target / 100.0)
                        part_gross_buy = part_qty * entry
                        part_gross_sell = part_qty * part_sell_price
                        part_fric = engine.calculate_friction(part_gross_buy, True) + engine.calculate_friction(part_gross_sell, False)
                        part_net_pnl = (part_gross_sell - part_gross_buy) - part_fric
                        available_cash += (part_gross_sell - engine.calculate_friction(part_gross_sell, False))
                        
                        pos['qty'] -= part_qty
                        pos['partial_booked'] = True
                        pos['booked_pnl'] = part_net_pnl
                        pos['booked_buy'] = part_gross_buy

            # Dynamic stop ratchet
            current_stop = pos['hard_stop']
            if be_threshold > 0 and max_runup_pct >= be_threshold:
                target_stop = entry * (1.0 + lock_pct / 100.0)
                current_stop = max(current_stop, target_stop)

            exit_price = None
            exit_reason = None

            # 1. Check Stop Trigger
            if initial_sl_atr > 0 and low <= current_stop:
                exit_price = current_stop
                if current_stop > entry:
                    exit_reason = "Profit Lock Triggered"
                elif current_stop >= entry:
                    exit_reason = "Break-Even Triggered"
                else:
                    exit_reason = "Initial ATR Safety Stop"

            # 2. Check Chandelier Reversal (after 2 bars)
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
                rem_net_pnl = (gross_sell - gross_buy) - fric
                
                total_trade_pnl = rem_net_pnl + pos.get('booked_pnl', 0.0)
                total_trade_buy = gross_buy + pos.get('booked_buy', 0.0)
                pnl_pct = (total_trade_pnl / total_trade_buy) * 100
                
                available_cash += (gross_sell - engine.calculate_friction(gross_sell, False))

                closed_trades.append({
                    "ticker": sym,
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "max_runup_pct": max_runup_pct,
                    "net_pnl": total_trade_pnl,
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
            if b.get('Regime_Bullish', 1) != 1: continue
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
                "peak_high": entry_price, "entry_date": str(ts),
                "partial_booked": False, "booked_pnl": 0.0, "booked_buy": 0.0
            }

    final_equity = available_cash
    for s, p in open_positions.items():
        final_equity += p['qty'] * p['entry_price']

    tdf = pd.DataFrame(closed_trades)
    net_profit = final_equity - total_invested
    win_rate = len(tdf[tdf['net_pnl'] > 0]) / len(tdf) * 100 if len(tdf) > 0 else 0
    wins = tdf[tdf['net_pnl'] > 0]['net_pnl'].sum()
    losses = abs(tdf[tdf['net_pnl'] < 0]['net_pnl'].sum())
    pf = wins / losses if losses > 0 else 999.0

    runners_10 = tdf[tdf['max_runup_pct'] >= 10.0]
    avg_pnl_runners = runners_10['pnl_pct'].mean() if len(runners_10) > 0 else 0
    neg_runners = len(runners_10[runners_10['pnl_pct'] < 0])

    return {
        "final_equity": final_equity,
        "net_profit": net_profit,
        "total_trades": len(tdf),
        "win_rate": win_rate,
        "profit_factor": pf,
        "avg_pnl_runners": avg_pnl_runners,
        "runners_turned_loss": neg_runners,
        "trades_df": tdf
    }

# Let's test a fine-grained grid of possibilities!
configs = [
    {"name": "1. Baseline (Chandelier Only)", "be": 0, "lock": 0, "part": 0, "target": 0},
    {"name": "2. Break-Even at +15% Run-up", "be": 15.0, "lock": 0.5, "part": 0, "target": 0},
    {"name": "3. Break-Even at +18% Run-up", "be": 18.0, "lock": 0.5, "part": 0, "target": 0},
    {"name": "4. Break-Even at +20% Run-up", "be": 20.0, "lock": 1.0, "part": 0, "target": 0},
    {"name": "5. Profit Lock +5% at +18% Run-up", "be": 18.0, "lock": 5.0, "part": 0, "target": 0},
    {"name": "6. Partial 50% Book at +12% + Chandelier", "be": 0, "lock": 0, "part": 0.50, "target": 12.0},
    {"name": "7. Partial 33% Book at +12% + Chandelier", "be": 0, "lock": 0, "part": 0.33, "target": 12.0},
    {"name": "8. Partial 50% Book at +15% + Chandelier", "be": 0, "lock": 0, "part": 0.50, "target": 15.0},
    {"name": "9. Partial 50% at +12% + BE on Remainder", "be": 12.0, "lock": 0.5, "part": 0.50, "target": 12.0},
    {"name": "10. Partial 33% at +12% + BE at +18%", "be": 18.0, "lock": 0.5, "part": 0.33, "target": 12.0},
]

rows = []
for c in configs:
    res = test_smart_exit(
        be_threshold=c['be'],
        lock_pct=c['lock'],
        partial_take_profit=c['part'],
        partial_target=c['target']
    )
    rows.append({
        "Exit Strategy": c['name'],
        "Final Portfolio": f"₹{res['final_equity']:,.2f}",
        "Net Profit": f"₹{res['net_profit']:,.2f}",
        "Win Rate": f"{res['win_rate']:.1f}%",
        "PF": f"{res['profit_factor']:.2f}",
        "Avg PnL on +10% Runners": f"{res['avg_pnl_runners']:.2f}%",
        "+10% Turning Negative": res['runners_turned_loss'],
        "Trades": res['total_trades']
    })
    print(f"Done: {c['name']} -> Net Profit: ₹{res['net_profit']:,.2f} | PF: {res['profit_factor']:.2f}")

out_df = pd.DataFrame(rows)
print("\n" + "="*95)
print("COMPREHENSIVE PROFIT PROTECTION & SCALING BENCHMARK")
print("="*95)
print(out_df.to_string(index=False))

