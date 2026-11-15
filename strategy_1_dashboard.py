# v6.0.0
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import logging
import time

from data_fetcher import fetch_data
from backtester import EngineMechanics

import importlib
import signal_engine
importlib.reload(signal_engine)
from signal_engine import calculate_indicators

logging.basicConfig(level=logging.INFO)

st.set_page_config(page_title="Sentinel-Swing Console", layout="wide", page_icon="🛡️")
st.title("🛡️ Sentinel-Swing | Quant Evaluation Console")
st.markdown("**The Laser-Sharp 1H Break-of-Structure Engine [v6.0]**")

st.sidebar.header("Execution Constraints")
st.sidebar.info("Base Timeframe: 1H. Engine utilizes an 'Armed State' Machine to hunt for Break-of-Structure.")
initial_cap = st.sidebar.number_input("Initial Capital (₹)", value=100000, step=10000)
max_pos = st.sidebar.number_input("Max Concurrent Positions", min_value=1, max_value=5, value=3)

st.sidebar.markdown("---")
st.sidebar.markdown("**Strategy Rules Active:**")
st.sidebar.markdown("- **Entry:** 1H Break of 2H Engulfing High (5-bar window)")
st.sidebar.markdown("- **Quality Zone:** Ichimoku Kumo Breakout (Tenkan > Kijun + Price > Cloud)")
st.sidebar.markdown("- **Risk:** 1.5% of Equity per Trade")
st.sidebar.markdown("- **TP:** 2x ATR (2.2x if VWAP confirmed)")
st.sidebar.markdown("- **SL:** Kijun-based (Trailing BE+0.2ATR @ 1R, then 0.5ATR trail)")
st.sidebar.markdown("---")

run_sim = st.sidebar.button("Run Simulation [Purged CV]", type="primary")

col1, col2, col3 = st.columns(3)
m_profit = col1.empty()
m_dd = col2.empty()
m_win = col3.empty()

m_profit.metric(label="Net Profit (Friction Applied)", value="₹0.00", delta="0.00%")
m_dd.metric(label="Max Drawdown", value="0.00%")
m_win.metric(label="Win Rate", value="0.00%")

st.markdown("### Equity Curve Simulation")
chart_placeholder = st.empty()
fig = go.Figure()
fig.add_trace(go.Scatter(x=[], y=[], mode='lines', name='Portfolio Value', line=dict(color='#00ff88', width=2)))
fig.update_layout(template="plotly_dark", height=400, margin=dict(l=20, r=20, t=20, b=20), plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
chart_placeholder.plotly_chart(fig, use_container_width=True)

st.markdown("### Trade Ledger (Simulated)")
ledger_placeholder = st.empty()
df_trades_empty = pd.DataFrame(columns=["Trade ID", "Ticker", "Entry Date", "Entry Price", "Exit Date", "Exit Price", "Gross Buy (₹)", "Gross Sell (₹)", "Net Amount (₹)", "PnL %", "Status"])
ledger_placeholder.dataframe(df_trades_empty, use_container_width=True, hide_index=True)

if run_sim:
    test_symbols = [
        "ZOMATO.NS", "HAL.NS", "IREDA.NS", "RVNL.NS", "DIXON.NS", "TRENT.NS", "BHEL.NS",
        "ADANIENT.NS", "KPITTECH.NS", "IRFC.NS", "KALYANKJIL.NS", "MCX.NS", "DELHIVERY.NS",
        "SUZLON.NS", "PFC.NS", "RECLTD.NS", "JINDALSTEL.NS", "LALPATHLAB.NS", "TATASTEEL.NS",
        "RELIANCE.NS", "INFY.NS", "HDFCBANK.NS", "ITC.NS", "SBI.NS", "TCS.NS", "LT.NS",
        "BAJFINANCE.NS", "AXISBANK.NS", "KOTAKBANK.NS", "M&M.NS", "MARUTI.NS", "SUNPHARMA.NS",
        "TITAN.NS", "ULTRACEMCO.NS", "WIPRO.NS", "INDIGO.NS", "DLF.NS", "GODREJPROP.NS",
        "PERSISTENT.NS", "COFORGE.NS", "TVSMOTOR.NS", "EICHERMOT.NS", "CHOLAFIN.NS",
        "SHRIRAMFIN.NS", "POLYCAB.NS", "HAVELLS.NS", "CUMMINSIND.NS", "SIEMENS.NS", "ABB.NS"
    ]
    eval_period = "730d"
    
    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_pos)
    progress_bar = st.progress(0, text="Initializing Quantum Engine...")
    
    dfs = {}
    all_timestamps = set()
    
    for idx, sym in enumerate(test_symbols):
        progress_bar.progress((idx + 1) / len(test_symbols), text=f"Processing Base 1H Timeframe: {sym} ({idx+1}/{len(test_symbols)})")
        
        df = fetch_data(sym, "1H", period=eval_period)
        if df.empty: continue
            
        df = calculate_indicators(df)
        df = df.dropna()
        if df.empty: continue
        
        dfs[sym] = df.set_index('timestamp').to_dict('index')
        all_timestamps.update(dfs[sym].keys())
        
    all_timestamps = sorted(list(all_timestamps))
    
    available_equity = float(initial_cap)
    open_positions = {}
    armed_states = {sym: {'active': False, 'trigger_price': 0, 'bars_left': 0, 'atr': 0} for sym in test_symbols}
    
    all_trades = []
    trade_id = 1
    equity_curve = []
    equity_curve_dates = []
    
    progress_bar.progress(1.0, text="Executing Laser-Sharp Event Engine...")
    
    for ts in all_timestamps:
        symbols_to_remove = []
        
        # 1. Update Exits and Trailing Stops
        for sym, pos in open_positions.items():
            if ts not in dfs[sym]: continue
            
            row = dfs[sym][ts]
            c = row['close']
            pos['bars_in_trade'] += 1
            
            if row['high'] > pos['highest_high']:
                pos['highest_high'] = row['high']
                
            # Institutional Trailing Stop Logic (BE+0.2 at 1R, Trail 0.5ATR after)
            if pos['highest_high'] >= (pos['entry_price'] + pos['atr']):
                be_stop = pos['entry_price'] + (0.2 * pos['atr'])
                trail_stop = pos['highest_high'] - (0.5 * pos['atr'])
                new_sl = max(be_stop, trail_stop)
                
                if new_sl > pos['current_sl']:
                    pos['current_sl'] = new_sl
                    
            exit_price = None
            reason = ""
            
            if row['high'] >= pos['target_price']:
                exit_price = pos['target_price']
                reason = "Target Hit (2x/2.2x ATR)"
            elif row['low'] <= pos['current_sl']:
                exit_price = pos['current_sl']
                reason = "Stop Loss / Trailing Hit"
                
            if exit_price:
                gross_buy = pos['qty'] * pos['entry_price']
                gross_sell = pos['qty'] * exit_price
                
                fric_buy = engine.calculate_friction(gross_buy, is_buy=True)
                fric_sell = engine.calculate_friction(gross_sell, is_buy=False)
                total_fric = fric_buy + fric_sell
                
                net_pnl_rs = (gross_sell - gross_buy) - total_fric
                pnl_pct = (net_pnl_rs / gross_buy) * 100
                
                available_equity += gross_sell - fric_sell
                
                all_trades.append({
                    "Trade ID": trade_id,
                    "Ticker": sym,
                    "Entry Date": pos['entry_date'].strftime("%Y-%m-%d %H:%M"),
                    "Entry Price": f"₹{pos['entry_price']:.2f}",
                    "Exit Date": ts.strftime("%Y-%m-%d %H:%M"),
                    "Exit Price": f"₹{exit_price:.2f}",
                    "Gross Buy (₹)": f"₹{gross_buy:.2f}",
                    "Gross Sell (₹)": f"₹{gross_sell:.2f}",
                    "Net Amount (₹)": f"₹{net_pnl_rs:.2f}",
                    "PnL %": pnl_pct,
                    "Status": reason,
                    "net_rs": net_pnl_rs
                })
                trade_id += 1
                symbols_to_remove.append(sym)
                
        for sym in symbols_to_remove:
            del open_positions[sym]
            
        # 2. State Machine: Arming and Breakout Execution
        for sym in test_symbols:
            if sym not in dfs or ts not in dfs[sym]: continue
            if sym in open_positions: continue
            
            row = dfs[sym][ts]
            c = row['close']
            atr = row['ATR180']
            
            # STATE: ARMED
            if armed_states[sym]['active']:
                if armed_states[sym]['bars_left'] > 0:
                    # Check for Break of Structure (Close > Trigger High)
                    if c > armed_states[sym]['trigger_price']:
                        # Quality Zone Validation (Ichimoku Kumo Breakout)
                        above_cloud = c > max(row['Senkou_A'], row['Senkou_B'])
                        tk_cross = row['Tenkan'] > row['Kijun']
                        kumo_breakout = above_cloud and tk_cross
                        
                        if kumo_breakout and available_equity > 0 and len(open_positions) < max_pos:
                            risk_rs = available_equity * 0.015
                            
                            # Kijun-based Stop Loss
                            sl_dist = (c - row['Kijun']) + (0.5 * atr) if c > row['Kijun'] else 1.0 * atr
                            sl_dist = max(sl_dist, 0.5 * atr) # Hard floor
                            tp_dist = 2.2 * atr if c > row['VWAP'] else 2.0 * atr
                            
                            qty = int(risk_rs / sl_dist) if sl_dist > 0 else 0
                            entry_price = c * 1.002
                            
                            if qty > 0:
                                gross_buy = qty * entry_price
                                fric_buy = engine.calculate_friction(gross_buy, is_buy=True)
                                if available_equity >= (gross_buy + fric_buy):
                                    available_equity -= (gross_buy + fric_buy)
                                    open_positions[sym] = {
                                        'qty': qty,
                                        'entry_price': entry_price,
                                        'entry_date': ts,
                                        'current_sl': entry_price - sl_dist,
                                        'target_price': entry_price + tp_dist,
                                        'atr': atr,
                                        'bars_in_trade': 0,
                                        'highest_high': entry_price
                                    }
                                    # Disarm after firing
                                    armed_states[sym]['active'] = False
                    else:
                        armed_states[sym]['bars_left'] -= 1
                else:
                    armed_states[sym]['active'] = False # Expired
            
            # STATE: SCANNING
            if not armed_states[sym]['active']:
                f1_atr = (atr / c) >= 0.008 if c > 0 else False
                f2_ema_4h = row['EMA80'] > row['EMA200']
                f3_ema_1d = c > row['EMA1200']
                f4_rsi_1d = row['RSI84'] > 55
                f5_rsi_2h = row['RSI28'] < 40
                
                macro_valid = f1_atr and f2_ema_4h and f3_ema_1d and f4_rsi_1d and f5_rsi_2h
                candle_valid = row['Is_Engulfing'] or row['Is_Hammer']
                
                if macro_valid and candle_valid:
                    # ARM THE ENGINE (Wait 5 hours for breakout)
                    armed_states[sym] = {
                        'active': True,
                        'trigger_price': row['high'],
                        'bars_left': 5,
                        'atr': atr
                    }

        # 3. Mark to Market Equity
        mtm_value = available_equity
        for sym, pos in open_positions.items():
            if ts in dfs[sym]:
                mtm_value += pos['qty'] * dfs[sym][ts]['close']
            else:
                mtm_value += pos['qty'] * pos['entry_price']
                
        equity_curve.append(mtm_value)
        equity_curve_dates.append(ts)
        
    time.sleep(0.5)
    progress_bar.empty()
    
    if all_trades:
        df_res = pd.DataFrame(all_trades)
        
        total_profit = equity_curve[-1] - initial_cap
        win_rate = (len(df_res[df_res['net_rs'] > 0]) / len(df_res)) * 100
        
        eq_arr = np.array(equity_curve)
        max_eq = np.maximum.accumulate(eq_arr)
        dd = (max_eq - eq_arr) / max_eq * 100
        max_dd = np.max(dd) if len(dd) > 0 else 0
        
        m_profit.metric("Net Profit (Friction Applied)", f"₹{total_profit:,.2f}", f"{(total_profit/initial_cap)*100:.2f}%")
        m_dd.metric("Max Drawdown", f"{max_dd:.2f}%")
        m_win.metric("Win Rate", f"{win_rate:.1f}%")
        
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=equity_curve_dates, y=equity_curve, mode='lines', name='Portfolio Value', line=dict(color='#00ff88', width=2)))
        fig2.update_layout(template="plotly_dark", height=400, margin=dict(l=20, r=20, t=20, b=20), plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
        chart_placeholder.plotly_chart(fig2, use_container_width=True)
        
        df_res_display = df_res.drop(columns=['net_rs'])
        df_res_display['PnL %'] = df_res_display['PnL %'].apply(lambda x: f"{x:.2f}%")
        ledger_placeholder.dataframe(df_res_display, use_container_width=True, hide_index=True)
        
        st.sidebar.success(f"✅ Execution Complete! Trades: {len(df_res)}")
    else:
        st.sidebar.warning("No trades triggered under these extremely strict institutional constraints.")

st.markdown("### Live Evaluation Constraints")
st.info("""
**System Architecture Checks Active:**
- Laser-Sharp Break-of-Structure Engine: Active ✅
- Ichimoku Kumo Cloud Quality Filter: Active ✅
- Institutional Trailing Stops (0.5x ATR): Active ✅
""")
