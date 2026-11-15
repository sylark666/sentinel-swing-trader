import sqlite3
import pandas as pd
import numpy as np
from golden_cross_4h_optimizer import load_4h_universe
from backtester import EngineMechanics

# Load universe from market_data.db
with sqlite3.connect('market_data.db') as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT ticker FROM market_data WHERE ticker != 'NIFTY' ORDER BY ticker ASC")
    all_syms = [r[0] for r in cursor.fetchall()]

stock_dfs, all_ts = load_4h_universe(start_date="2018-01-01", symbols=all_syms, timeframe="4H")

def simulate_with_exit_rules(
    exit_enhancement="BASELINE", # "BASELINE", "BREAK_EVEN_AT_10", "TRAIL_LOCK_10", "HYBRID_TIGHT_TRAIL", "TIERED_PROFIT_LOCK"
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

            # Track peak price and max run-up
            if high > pos.get('peak_high', entry):
                pos['peak_high'] = high
            peak = pos.get('peak_high', entry)
            max_runup_pct = ((peak - entry) / entry) * 100

            if bar.get('Chandelier_Dir', 0) == 1:
                pos['saw_chandelier_bullish'] = True

            exit_price = None
            exit_reason = None

            # --- EXIT LOGIC ---
            # Active hard stop (dynamic if break-even / trailing applied)
            current_stop = pos['hard_stop']

            if exit_enhancement == "BREAK_EVEN_AT_10":
                # Once run-up reaches +10%, move stop to entry price + 0.5% buffer
                if max_runup_pct >= 10.0:
                    current_stop = max(current_stop, entry * 1.005)

            elif exit_enhancement == "TIERED_PROFIT_LOCK":
                # Tier 1: at +10% run-up -> move stop to break-even (+1%)
                # Tier 2: at +15% run-up -> lock in +7%
                # Tier 3: at +20% run-up -> lock in +12%
                # Tier 4: at +30% run-up -> trail by 12% from peak
                if max_runup_pct >= 30.0:
                    current_stop = max(current_stop, peak * 0.88)
                elif max_runup_pct >= 20.0:
                    current_stop = max(current_stop, entry * 1.12)
                elif max_runup_pct >= 15.0:
                    current_stop = max(current_stop, entry * 1.07)
                elif max_runup_pct >= 10.0:
                    current_stop = max(current_stop, entry * 1.01)

            elif exit_enhancement == "EMA20_AFTER_10":
                # Once run-up reaches +10%, if 4H candle closes below 20 EMA, take profit!
                if max_runup_pct >= 10.0 and close < bar['EMA20']:
                    exit_price = close
                    exit_reason = "Profit Lock (Close < 20 EMA at +10% Run-up)"

            elif exit_enhancement == "TIGHT_CHANDELIER_AFTER_10":
                # Once run-up reaches +10%, trail by Peak - 1.5 ATR (tighter Chandelier)
                if max_runup_pct >= 10.0:
                    tighter_stop = peak - (1.5 * bar['ATR'])
                    current_stop = max(current_stop, tighter_stop)

            # 1. Check Stop Trigger
            if exit_price is None and initial_sl_atr > 0 and low <= current_stop:
                exit_price = current_stop
                if current_stop > entry:
                    exit_reason = "Trailing Profit Lock Triggered"
                elif current_stop == entry * 1.005:
                    exit_reason = "Break-Even Safety Triggered"
                else:
                    exit_reason = "Initial ATR Safety Stop"

            # 2. Check Chandelier Reversal (after 2 bars)
            elif exit_price is None and pos['bars_held'] >= 2:
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
                "peak_high": entry_price, "entry_date": str(ts)
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

    # Specifically analyze the 10%+ runup trades that ended <= 5% in baseline
    runners_10 = tdf[tdf['max_runup_pct'] >= 10.0]
    avg_pnl_runners = runners_10['pnl_pct'].mean() if len(runners_10) > 0 else 0
    neg_runners = len(runners_10[runners_10['pnl_pct'] < 0])

    return {
        "model": exit_enhancement,
        "final_equity": final_equity,
        "net_profit": net_profit,
        "total_trades": len(tdf),
        "win_rate": win_rate,
        "profit_factor": pf,
        "runners_count": len(runners_10),
        "avg_pnl_runners": avg_pnl_runners,
        "runners_turned_loss": neg_runners,
        "trades_df": tdf
    }

models = [
    "BASELINE",
    "BREAK_EVEN_AT_10",
    "TIERED_PROFIT_LOCK",
    "EMA20_AFTER_10",
    "TIGHT_CHANDELIER_AFTER_10"
]

results = []
dfs = {}
for m in models:
    print(f"Testing model: {m}...")
    res = simulate_with_exit_rules(exit_enhancement=m)
    results.append(res)
    dfs[m] = res['trades_df']

res_df = pd.DataFrame([{
    "Model": r['model'],
    "Final Equity (₹)": f"₹{r['final_equity']:,.2f}",
    "Net Profit (₹)": f"₹{r['net_profit']:,.2f}",
    "Win Rate %": f"{r['win_rate']:.1f}%",
    "Profit Factor": f"{r['profit_factor']:.2f}",
    "Avg PnL on +10% Runners": f"{r['avg_pnl_runners']:.2f}%",
    "+10% Runners Turning Negative": r['runners_turned_loss'],
    "Total Trades": r['total_trades']
} for r in results])

print("\n" + "="*85)
print("COMPARATIVE EXIT MECHANICS BENCHMARK (4H GOLDEN CROSS + SIP)")
print("="*85)
print(res_df.to_string(index=False))

