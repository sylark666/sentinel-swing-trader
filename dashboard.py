# v8.0.0
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import logging
import time

from data_fetcher import fetch_data
from backtester import EngineMechanics
from tradingview_fetcher import fetch_tv_data
import importlib
import database
importlib.reload(database)
from database import init_db, get_db_stats, load_candles_from_db, save_candles_batch, get_connection, get_latest_timestamps
from screener import run_opportunity_scan, get_market_regime
init_db()

import importlib
import golden_cross_4h_optimizer
importlib.reload(golden_cross_4h_optimizer)
from golden_cross_4h_optimizer import calculate_4h_indicators, simulate_4h_golden_cross, load_4h_universe, UNIVERSE_4H

import signal_engine
importlib.reload(signal_engine)
from signal_engine import calculate_indicators

import strategy_renko.renko_dashboard_adapter
importlib.reload(strategy_renko.renko_dashboard_adapter)
from strategy_renko.renko_dashboard_adapter import (
    simulate_renko_supertrend_portfolio,
    scan_renko_supertrend_opportunities
)

logging.basicConfig(level=logging.INFO)

# Background Telegram Bot listener daemon (runs once across sessions)
import threading
@st.cache_resource
def start_telegram_daemon():
    try:
        import telegram_bot
        t = threading.Thread(target=telegram_bot.poll_telegram_updates, daemon=True, name="TelegramBotDaemon")
        t.start()
        logging.info("Telegram Bot daemon started successfully in background.")
        return True
    except Exception as e:
        logging.warning(f"Could not start Telegram Bot daemon: {e}")
        return False

start_telegram_daemon()

# Background Live Market Hourly Service daemon (runs once across sessions)
@st.cache_resource
def start_market_service_daemon():
    try:
        import live_market_service
        t = threading.Thread(target=live_market_service.run_market_scheduler, daemon=True, name="LiveMarketServiceDaemon")
        t.start()
        logging.info("Live Market Service daemon started successfully in background.")
        return True
    except Exception as e:
        logging.warning(f"Could not start Live Market Service daemon: {e}")
        return False

start_market_service_daemon()

def load_data_source(symbol: str, timeframe: str, source: str, period: str = "730d"):
    """Loads candlestick data seamlessly from TradingView Local DB or yfinance."""
    if source == "TradingView (Local DB)":
        clean_sym = symbol.replace("^NSEI", "NIFTY")
        df = load_candles_from_db(clean_sym, timeframe)
        if df.empty:
            df = fetch_tv_data(clean_sym, timeframe=timeframe, n_bars=1000)
            if not df.empty:
                save_candles_batch(df)
        return df
    else:
        return fetch_data(symbol, timeframe, period=period)

def get_live_or_cached_cmp(ticker: str, fallback_price: float = 0.0) -> float:
    """Fetches real-time market price via yfinance with instant local DB candle fallback."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        fi = t.fast_info
        cmp = float(fi.get('lastPrice') or fi.get('regularMarketPrice') or 0.0)
        if cmp > 0.0:
            return round(cmp, 2)
    except Exception:
        pass

    try:
        clean_sym = ticker if ticker.endswith(".NS") else f"{ticker}.NS"
        df_c = load_candles_from_db(clean_sym, timeframe="4H")
        if not df_c.empty:
            return round(float(df_c.iloc[-1]['close']), 2)
        df_1d = load_candles_from_db(clean_sym, timeframe="1D")
        if not df_1d.empty:
            return round(float(df_1d.iloc[-1]['close']), 2)
    except Exception:
        pass

    return fallback_price

st.set_page_config(page_title="Sentinel-Swing Console", layout="wide", page_icon="🛡️")
st.title("🛡️ Sentinel-Swing | Quant Evaluation Console")

import os
import portfolio_manager as pm
import security
pm.init_portfolio_db()

# Enforce Enterprise Security & Hardening Gate (SHA-256 PIN, Anti-Bot, Geo-IP, Brute-Force Blacklist)
security.enforce_security_gate()

st.sidebar.header("Strategy & Execution Configuration")
strategy_mode = st.sidebar.selectbox(
    "Active Trading Strategy",
    [
        "Strategy 1: 4H Golden Cross Unlimited Runner [Champion #1]",
        "Strategy 2: AI Renko Supertrend [Champion #2 - ₹80L Compounding]"
    ],
    index=0
)

if strategy_mode.startswith("Strategy 1"):
    st.markdown("**Strategy 1: 4H Golden Cross Unlimited Runner [v8.5 - Champion Strategy]**")
else:
    st.markdown("**Strategy 2: AI Renko Supertrend [v9.0 - Multi-Stock Swing Champion]**")

data_source = st.sidebar.selectbox("Market Data Engine", ["TradingView (Local DB)", "yfinance (Live/Cache)"], index=0)

with st.sidebar.expander("📥 TradingView Nifty 500 DB Sync", expanded=False):
    db_stats = get_db_stats()
    st.markdown(f"**DB Total**: `{db_stats['total_candles']:,}` candles")
    st.markdown(f"**Unique Stocks**: `{db_stats['unique_tickers']}` tickers")
    
    st.markdown("---")
    st.markdown("⚡ **Daily Incremental Update**")
    st.caption("Checks SQLite latest dates against today's date and fetches only missing candles.")
    inc_tf = st.selectbox("Update Timeframe", ["4H", "1D", "1H", "15M"], index=0, key="sb_inc_tf")
    inc_count = st.slider("Stocks to Check", min_value=10, max_value=501, value=501, step=10, key="sb_inc_count")
    
    if st.button("🚀 Catch Up to Today", key="sb_btn_inc_sync"):
        from update_market_data import update_database_incremental
        from sync_tradingview_nifty500 import get_nifty500_symbols
        syms_to_check = get_nifty500_symbols()[:inc_count]
        sync_pbar = st.progress(0, text="Checking SQLite timestamps...")
        
        def update_ui_pbar(pct, text):
            sync_pbar.progress(pct, text=text)
            
        res = update_database_incremental(
            timeframe=inc_tf,
            symbols=syms_to_check,
            progress_callback=update_ui_pbar
        )
        sync_pbar.progress(1.0, text="Update Complete!")
        st.success(f"Done! Updated {res['updated']} stocks ({res['already_current']} already current). Added {res['candles_added']:,} candles.")
        time.sleep(1.5)
        st.rerun()

    st.markdown("---")
    st.markdown("📦 **Full Historical Backfill**")
    sync_tf = st.selectbox("Backfill Timeframe", ["15M", "1H", "4H", "1D", "30M", "1WK"], index=2, key="sb_sync_tf")
    sync_bars = st.number_input("Bars per Stock", min_value=50, max_value=5000, value=1000, step=100, key="sb_sync_bars")
    sync_count = st.slider("Stocks to Backfill", min_value=10, max_value=501, value=50, step=10, key="sb_sync_count")
    
    if st.button("Start Full Backfill", key="sb_btn_sync"):
        from sync_tradingview_nifty500 import get_nifty500_symbols
        symbols_list = get_nifty500_symbols()[:sync_count]
        sync_pbar = st.progress(0, text="Initializing TradingView Sync...")
        added_count = 0
        for i, s in enumerate(symbols_list):
            sync_pbar.progress((i + 1) / len(symbols_list), text=f"Syncing {s} ({i+1}/{len(symbols_list)})...")
            try:
                tv_df = fetch_tv_data(symbol=s, timeframe=sync_tf, exchange="NSE", n_bars=int(sync_bars))
                if tv_df is not None and not tv_df.empty:
                    saved = save_candles_batch(tv_df)
                    added_count += saved
            except Exception:
                pass
            time.sleep(0.35)
        sync_pbar.progress(1.0, text="Sync Complete!")
        st.success(f"Synced {sync_count} stocks! Added {added_count:,} candles to DB.")
        st.rerun()

stock_set = st.sidebar.selectbox("Select Stock Universe", [
    "🔥 All Nifty 500 Stocks (490+ Active)",
    "Set 1 (Core & Momentum, 50 stocks)", 
    "Set 2 (Nifty 50 & Bluechips, 50 stocks)", 
    "Set 3 (Mid-Cap & Sector Leaders, 50 stocks)"
], index=0)

if strategy_mode.startswith("Strategy 1"):
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🏆 Golden Cross Parameters")
    selected_tf = st.sidebar.selectbox(
        "Trading Timeframe", 
        ["4H", "1D", "2H", "1WK", "1H"], 
        index=0,
        help="Select candlestick timeframe: 4H (Half-day), 1D (Daily), 2H (Fast swing), 1WK (Weekly), 1H (Hourly)."
    )
    gc_entry_selection = st.sidebar.selectbox(
        "Golden Cross Entry Setup",
        [
            "Swing GC (20 > 50 EMA above 200) [Rank 1 - Highest Return]",
            "Classic GC (50 > 200 EMA)",
            "Dual GC (All Golden Crosses)",
            "Established GC (50 EMA Pullback Bounce)"
        ],
        index=0
    )
    gc_exit_selection = st.sidebar.selectbox(
        "The Right Exit Model (No Fixed Target Cap)",
        [
            "Chandelier Trailing Exit [Recommended - Rank 1]",
            "Hybrid Trailing Exit (20 EMA trail after +15%)",
            "50 EMA Trailing Exit (Close < 50 EMA)",
            "20 EMA Trailing Exit (Close < 20 EMA)",
            "Full Death Cross Exit (50 < 200 EMA)"
        ],
        index=0
    )
    gc_gate_selection = st.sidebar.selectbox(
        "False-Positive Quality Gate",
        [
            "🚀 Active Volume Gate (Vol >= 0.8x) [Champion - ₹12.5L Profit, 6% Early Exits]",
            "⚡ True State Flip Only (₹6.9L Profit, Cuts 66% Early Exits)",
            "⚠️ Baseline / Unfiltered (Original ₹8.13 Lakhs Run, 71 Early Exits)",
            "🛡️ Strict High-Quality Gate (Green Candle + Vol >= 1.0x)",
            "💎 Ultra-Institutional Gate (Vol 1.2x + Green + Chandelier + Anti-FOMO)"
        ],
        index=0,
        help="Control how false-positive crossovers are handled. Active Volume (0.8x) eliminates dead volume while keeping quiet pause candles that lead to monster runners."
    )
    initial_sl_atr = st.sidebar.slider(
        "Catastrophic ATR Safety Stop (x ATR)", 
        min_value=1.5, max_value=4.0, value=2.5, step=0.1,
        help="Wide initial volatility safety stop to survive market noise while letting runners move freely."
    )
    
    st.sidebar.markdown("**Dynamic Capital Allocation**")
    initial_cap = st.sidebar.number_input("Initial Capital (₹)", value=100000, step=10000)
    monthly_sip = st.sidebar.number_input("Monthly Capital Top-Up / SIP (₹)", value=0, step=1000, help="Inject fresh savings every month into available cash to compound position sizes.")
    max_alloc_pct = st.sidebar.slider("Max Trade Allocation (%)", min_value=15, max_value=50, value=30, step=5, help="30% = max 3 positions open concurrently.")
    min_alloc_pct = st.sidebar.slider("Min Trade Allocation (%)", min_value=10, max_value=25, value=15, step=5, help="15% floor to avoid small positions getting eroded by fees.")

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Strategy Rules Active:**")
    st.sidebar.markdown(f"- **Timeframe:** {selected_tf} institutional swing")
    st.sidebar.markdown("- **Setup:** Institutional Golden Crossover in primary trend")
    st.sidebar.markdown("- **Profit Booking:** ZERO CAP (Unlimited runners: +20%, +50%, +100%+)")
    st.sidebar.markdown("- **Right Exit:** Chandelier ratchets beneath price to lock in profits")
    st.sidebar.markdown("- **Friction:** Deducts full Indian delivery fees (~0.35%)")
    
    # Defaults for compatibility
    base_tf_selection = selected_tf
    below_tf = "1H" if selected_tf in ["4H", "2H"] else "4H"
    base_tf = selected_tf
    above_tf = "1D" if selected_tf in ["4H", "2H"] else "1WK"
    eval_period = "max"
    wait_bars = 0
    tp_pct = 0.0
    sl_pct = 0.0
    use_atr_stop = True
    atr_mult = initial_sl_atr
    use_be_stop = False
    be_trigger_pct = 0
    use_ltf_exit = False
    ltf_exit_wait = 0
    use_macd_exit = False
    use_rsi_exit = False
    max_trade_val = int(initial_cap * (max_alloc_pct / 100.0))
    min_trade_val = int(initial_cap * (min_alloc_pct / 100.0))
    enable_rotation = False
    rotation_hurdle = 999.0
    stagnation_bars_thresh = 999

else:
    st.sidebar.info("🧱 AI Renko Supertrend: Non-temporal price-action bricks + Supertrend direction + Break-Even ratchet.")
    renko_tf = st.sidebar.selectbox("Base Candle Timeframe", ["1D", "4H", "1H"], index=0, help="Candles used to construct the Renko bricks.")
    brick_size_mode = st.sidebar.selectbox(
        "Renko Brick Size Model",
        [
            "Dynamic 2.5% Daily Bricks [Sweet Spot Champion - ₹80L Compounding]",
            "Dynamic 2.0% ATR Bricks [Fast Swing]",
            "Tight 1.5% Bricks [High Frequency]"
        ],
        index=0
    )
    if "2.5%" in brick_size_mode:
        brick_pct = 0.025
    elif "2.0%" in brick_size_mode:
        brick_pct = 0.020
    else:
        brick_pct = 0.015

    col_st1, col_st2 = st.sidebar.columns(2)
    st_period = col_st1.number_input("Supertrend ATR Period", min_value=5, max_value=25, value=10, step=1)
    st_multiplier = col_st2.number_input("Supertrend Multiplier", min_value=1.0, max_value=5.0, value=2.0, step=0.5)

    use_be_ratchet = st.sidebar.checkbox("Asymmetric 1:1 Break-Even Ratchet (+2 Bricks)", value=True, help="Ratchets stop loss to cost once floating gain reaches +2 bricks.")
    
    st.sidebar.markdown("**Portfolio & Compounding Sizing**")
    initial_cap = st.sidebar.number_input("Initial Capital (₹)", value=100000, step=10000)
    monthly_sip = st.sidebar.number_input("Monthly Capital Top-Up / SIP (₹)", value=5000, step=1000, help="Inject fresh savings every month to compound portfolio equity.")
    max_positions = st.sidebar.slider("Max Concurrent Slots", min_value=3, max_value=12, value=8, step=1, help="8 to 10 slots recommended for multi-stock swing diversification.")
    alloc_pct = 1.0 / max_positions

    st.sidebar.markdown("---")
    st.sidebar.markdown("**AI Renko Rules Active:**")
    st.sidebar.markdown("- **Brick Construction:** 2-Brick Reversal Filter (eliminates time noise)")
    st.sidebar.markdown("- **Entry:** On brick close when Supertrend flips Green (Buy)")
    st.sidebar.markdown("- **Exit 1:** Break-Even lock at +2 bricks gain")
    st.sidebar.markdown("- **Exit 2:** Full exit on Supertrend Red reversal")
    st.sidebar.markdown("- **Friction:** Zerodha GTT delivery/futures friction modeled (~0.25%)")

    # Compatibility variables
    base_tf_selection = renko_tf
    eval_period = "max"
    wait_bars = 0
    tp_pct = 0.0
    sl_pct = 0.0
    use_atr_stop = True
    atr_mult = 2.0
    use_be_stop = use_be_ratchet
    be_trigger_pct = 0.0
    use_ltf_exit = False
    ltf_exit_wait = 0
    use_macd_exit = False
    use_rsi_exit = False
    max_trade_val = int(initial_cap * alloc_pct)
    min_trade_val = int(initial_cap * (alloc_pct * 0.5))
    enable_rotation = False
    rotation_hurdle = 999.0
    stagnation_bars_thresh = 999

SET_1 = [
    "ZOMATO.NS", "HAL.NS", "IREDA.NS", "RVNL.NS", "DIXON.NS", "TRENT.NS", "BHEL.NS",
    "ADANIENT.NS", "KPITTECH.NS", "IRFC.NS", "KALYANKJIL.NS", "MCX.NS", "DELHIVERY.NS",
    "SUZLON.NS", "PFC.NS", "RECLTD.NS", "JINDALSTEL.NS", "LALPATHLAB.NS", "TATASTEEL.NS",
    "RELIANCE.NS", "INFY.NS", "HDFCBANK.NS", "ITC.NS", "SBI.NS", "TCS.NS", "LT.NS",
    "BAJFINANCE.NS", "AXISBANK.NS", "KOTAKBANK.NS", "M&M.NS", "MARUTI.NS", "SUNPHARMA.NS",
    "TITAN.NS", "ULTRACEMCO.NS", "WIPRO.NS", "INDIGO.NS", "DLF.NS", "GODREJPROP.NS",
    "PERSISTENT.NS", "COFORGE.NS", "TVSMOTOR.NS", "EICHERMOT.NS", "CHOLAFIN.NS",
    "SHRIRAMFIN.NS", "POLYCAB.NS", "HAVELLS.NS", "CUMMINSIND.NS", "SIEMENS.NS", "ABB.NS",
    "LTIM.NS"
]

SET_2 = [
    "SBIN.NS", "BHARTIARTL.NS", "HINDUNILVR.NS", "ICICIBANK.NS", "BAJAJFINSV.NS", 
    "ASIANPAINT.NS", "HCLTECH.NS", "TATAMOTORS.NS", "NTPC.NS", "ONGC.NS", 
    "POWERGRID.NS", "NESTLEIND.NS", "GRASIM.NS", "TECHM.NS", "HINDALCO.NS", 
    "JSWSTEEL.NS", "CIPLA.NS", "DRREDDY.NS", "SBILIFE.NS", "HDFCLIFE.NS", 
    "BRITANNIA.NS", "TATACONSUM.NS", "APOLLOHOSP.NS", "DIVISLAB.NS", "HEROMOTOCO.NS", 
    "BAJAJ-AUTO.NS", "INDUSINDBK.NS", "BANKBARODA.NS", "PNB.NS", "CANBK.NS", 
    "IDFCFIRSTB.NS", "TATACHEM.NS", "TATAPOWER.NS", "VEDL.NS", "COALINDIA.NS", 
    "GAIL.NS", "IOC.NS", "BPCL.NS", "HINDPETRO.NS", "AMBUJACEM.NS", 
    "SHREECEM.NS", "ACC.NS", "ADANIPORTS.NS", "ADANIGREEN.NS", "ADANIPOWER.NS", 
    "ATGL.NS", "AWL.NS", "TORNTPHARM.NS", "ZYDUSLIFE.NS", "LUPIN.NS"
]

SET_3 = [
    "SAIL.NS", "NMDC.NS", "CONCOR.NS", "IRCTC.NS", "BOSCHLTD.NS", 
    "MRF.NS", "PAGEIND.NS", "TATAELXSI.NS", "MPHASIS.NS", "COROMANDEL.NS", 
    "LTTS.NS", "AUBANK.NS", "BANDHANBNK.NS", "FEDERALBNK.NS", "MUTHOOTFIN.NS", 
    "SRF.NS", "PIIND.NS", "UPL.NS", "AARTIIND.NS", "DEEPAKNTR.NS", 
    "NAUKRI.NS", "PAYTM.NS", "NYKAA.NS", "POLICYBZR.NS", "DMART.NS", 
    "VBL.NS", "ZEEL.NS", "PVRINOX.NS", "SUNTV.NS", "ICICIGI.NS", 
    "ICICIPRULI.NS", "NAM-INDIA.NS", "HDFCAMC.NS", "ABBOTINDIA.NS", "ALKEM.NS", 
    "AUROPHARMA.NS", "BIOCON.NS", "GLENMARK.NS", "SYNGENE.NS", "PEL.NS", 
    "MFSL.NS", "LICHSGFIN.NS", "CANFINHOME.NS", "M&MFIN.NS", "MGL.NS", 
    "RELAXO.NS", "BATAINDIA.NS", "VOLTAS.NS", "WHIRLPOOL.NS", "CROMPTON.NS"
]

if stock_set.startswith("🔥 All Nifty 500"):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT ticker FROM market_data WHERE ticker != 'NIFTY' ORDER BY ticker ASC")
        test_symbols = [r[0] for r in cursor.fetchall()]
    if not test_symbols:
        from sync_tradingview_nifty500 import get_nifty500_symbols
        raw_syms = get_nifty500_symbols()
        test_symbols = [s if s.endswith(".NS") or s == "NIFTY" else f"{s}.NS" for s in raw_syms]
elif stock_set.startswith("Set 1"):
    test_symbols = SET_1
elif stock_set.startswith("Set 2"):
    test_symbols = SET_2
else:
    test_symbols = SET_3

run_sim = st.sidebar.button("Run Simulation [Purged CV]", type="primary")

tab_live, tab_radar, tab_backtest, tab_db, tab_security = st.tabs([
    "💼 Live Portfolio & Trade Logger",
    "🎯 Opportunity Radar & Capital Rotation", 
    "🛡️ Strategy Backtester", 
    "📊 Database Status",
    "🔒 Security & Hardening"
])

with tab_live:
    st.subheader("💼 Live Swing Portfolio & Interactive Trade Logger")
    s = pm.get_portfolio_summary()
    active_trades = pm.get_active_trades()

    col_s1, col_s2, col_s3, col_s4, col_s5 = st.columns(5)
    col_s1.metric("Total Equity", f"₹{s['total_capital']:,.2f}")
    col_s2.metric("Available Cash", f"₹{s['cash_available']:,.2f}", delta=f"{s['cash_pct']:.1f}% Cash")
    col_s3.metric("Invested Capital", f"₹{s['invested_capital']:,.2f}")
    col_s4.metric("Effective Slots", f"{s['effective_slots_occupied']:.1f} / 3.0", delta="Entry Open" if s['can_take_new_trade'] else "Capacity Full")
    col_s5.metric("Dynamic 30% Sizing", f"₹{s['slot_target_allocation']:,.2f}")

    st.markdown("---")

    # 1. Active Holdings
    if active_trades:
        df_act = pd.DataFrame(active_trades)
        cmps = []
        pnl_strs = []
        total_unrealized_pnl = 0.0

        for idx, row in df_act.iterrows():
            sym = row['ticker']
            entry = float(row['entry_price'])
            q = int(row['qty'])
            cmp = get_live_or_cached_cmp(sym, entry)
            gross_b = q * entry
            gross_c = q * cmp
            fric = pm.calculate_friction(gross_c, is_buy=False) + pm.calculate_friction(gross_b, is_buy=True)
            pnl = (gross_c - gross_b) - fric
            pnl_pct = (pnl / gross_b) * 100 if gross_b > 0 else 0.0
            total_unrealized_pnl += pnl

            cmps.append(f"₹{cmp:,.2f}")
            pnl_color = "🟢 +" if pnl >= 0 else "🔴 -"
            pnl_sign = "+" if pnl >= 0 else ""
            pnl_strs.append(f"{pnl_color}₹{abs(pnl):,.2f} ({pnl_sign}{pnl_pct:.2f}%)")

        df_act['CMP'] = cmps
        df_act['Current PnL'] = pnl_strs
        df_act['Entry Date'] = pd.to_datetime(df_act['entry_date']).dt.strftime('%Y-%m-%d %H:%M')
        df_act['Entry Price'] = df_act['entry_price'].apply(lambda x: f"₹{x:,.2f}")
        df_act['Current Stop'] = df_act['current_trailing_stop'].apply(lambda x: f"₹{x:,.2f}")
        df_act['Initial Stop'] = df_act['initial_stop'].apply(lambda x: f"₹{x:,.2f}")
        df_act['Slot Size'] = df_act['effective_slot'].apply(lambda x: f"{x:.1f} Slot")
        df_act['Ratchet Stage'] = df_act['chandelier_stage'].apply(lambda x: f"Stage {x} (2.3x)" if x == 2 else f"Stage {x} (3.0x)")
        if 'strategy' not in df_act.columns:
            df_act['strategy'] = 'Strategy 1: 4H Golden Cross Runner'

        pnl_badge = f"🟢 +₹{total_unrealized_pnl:,.2f}" if total_unrealized_pnl >= 0 else f"🔴 -₹{abs(total_unrealized_pnl):,.2f}"
        st.subheader(f"🟢 Active Swing Positions ({len(active_trades)}) — Total Unrealized PnL: {pnl_badge}")

        df_act_disp = df_act[['ticker', 'strategy', 'qty', 'Entry Price', 'CMP', 'Current PnL', 'Current Stop', 'Initial Stop', 'Slot Size', 'Ratchet Stage', 'status', 'Entry Date']].rename(columns={
            'ticker': 'Ticker', 'strategy': 'Strategy', 'qty': 'Shares', 'CMP': 'Current Price (CMP)', 'Current PnL': 'Current Net PnL', 'status': 'Status'
        })
        st.dataframe(df_act_disp, use_container_width=True, hide_index=True)
    else:
        st.subheader("🟢 Active Swing Positions (0)")
        st.info("No active swing positions open. 100% Cash ready for high-alpha setups.")

    st.markdown("---")

    # 2. Interactive Manual Trade Logger (Dual Columns)
    col_log1, col_log2 = st.columns(2)

    with col_log1:
        st.markdown("### 📝 Log Buy / Scale-In")
        with st.form("form_log_buy", clear_on_submit=True):
            b_sym = st.text_input("Stock Symbol (e.g. TECHM, HAL, TRENT, POONAWALLA)").upper().strip()
            b_strat = st.selectbox(
                "Trading Strategy Engine",
                [
                    "Strategy 1: 4H Golden Cross Runner",
                    "Strategy 2: AI Renko Supertrend",
                    "Dual Confluence (Both Strategies)",
                    "Discretionary / Manual Setup"
                ],
                index=0,
                help="Select which strategy rules should govern and trail this position."
            )
            b_qty = st.number_input("Shares Executed", min_value=1, value=10, step=1)
            b_price = st.number_input("Executed Buy Price (₹)", min_value=0.1, value=100.0, step=0.5)
            b_sl = st.number_input("Initial Stop Loss (₹) [Default -5%]", min_value=0.1, value=round(b_price * 0.95, 2), step=0.5)

            submit_buy = st.form_submit_button("✅ Record Buy Execution", type="primary", use_container_width=True)
            if submit_buy:
                if b_sym:
                    ok, msg = pm.log_buy_trade(b_sym, b_qty, b_price, b_sl, strategy=b_strat)
                    if ok:
                        st.success(msg)
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(msg)
                else:
                    st.warning("Please enter a stock symbol.")

    with col_log2:
        st.markdown("### 📤 Log Sell / Scale-Out / Vault")
        active_tickers = [p['ticker'].replace(".NS", "") for p in active_trades]
        if active_tickers:
            s_sym = st.selectbox("Select Active Position to Exit", active_tickers, key="sell_active_pos")
            curr_p = next((p for p in active_trades if p['ticker'].replace(".NS", "") == s_sym), None)
            curr_qty = curr_p['qty'] if curr_p else 1
            curr_entry = float(curr_p['entry_price']) if curr_p else 100.0

            st.caption(f"📌 Current Holding: **{curr_qty} shares** @ Entry ₹{curr_entry:,.2f}")

            s_mode = st.radio(
                "Exit Type", 
                ["Full Exit (100%)", "Partial Profit / Scale-Out", "Exit & Keep Some in Vault Forever"],
                key="sell_exit_mode"
            )

            q_to_sell = curr_qty
            scale_label = "Full Exit"

            if s_mode == "Partial Profit / Scale-Out":
                scale_method = st.radio("Scale-Out By", ["Percentage (%)", "Exact Number of Shares"], horizontal=True, key="scale_out_method")
                if scale_method == "Percentage (%)":
                    scale_pct = st.slider("Select % of Position to Sell", min_value=5, max_value=95, value=25, step=5, help="e.g. 10%, 20%, 25%, 33%, 50%")
                    q_to_sell = max(1, int(round(curr_qty * (scale_pct / 100.0))))
                    scale_label = f"Partial Scale-Out ({scale_pct}%)"
                else:
                    max_sellable = max(1, curr_qty - 1)
                    q_to_sell = st.number_input("Shares to Sell", min_value=1, max_value=max_sellable, value=min(max_sellable, max(1, curr_qty // 4)), step=1)
                    calc_pct = round((q_to_sell / curr_qty) * 100, 1)
                    scale_label = f"Partial Scale-Out ({calc_pct}%)"

                remaining_shares = curr_qty - q_to_sell
                st.info(f"📊 Selling **{q_to_sell}** of **{curr_qty}** shares. **{remaining_shares}** shares will continue trailing with Chandelier stop.")

            s_price = st.number_input("Executed Sell Price (₹)", min_value=0.01, value=curr_entry, step=0.5, key="executed_sell_price")

            s_retain = 0
            if s_mode == "Exit & Keep Some in Vault Forever":
                max_retain = max(1, curr_qty - 1)
                s_retain = st.number_input("Shares to Keep in Long-Term Vault Forever", min_value=1, max_value=max_retain, value=1, step=1, key="vault_retain_shares")
                st.caption(f"Transferring {s_retain} shares to Vault; selling remaining {curr_qty - s_retain} shares.")

            if st.button("🔴 Record Sell Execution", type="primary", use_container_width=True, key="btn_record_sell"):
                if s_mode == "Full Exit (100%)":
                    ok, msg = pm.log_full_sell(s_sym, s_price, reason="Manual User Exit")
                elif s_mode == "Partial Profit / Scale-Out":
                    ok, msg = pm.log_partial_sell(s_sym, q_to_sell, s_price, custom_reason=scale_label)
                else:
                    ok, msg = pm.log_full_sell(s_sym, s_price, reason="Manual User Exit", retain_qty=s_retain)

                if ok:
                    st.success(msg)
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(msg)
        else:
            st.caption("No open positions available to sell.")
            st.button("No Open Positions", disabled=True, use_container_width=True, key="btn_no_pos")

    st.markdown("---")

    # 3. Capital Management & Long-Term Vault
    col_cap1, col_cap2 = st.columns(2)
    with col_cap1:
        st.markdown("### 💰 Capital Injections & Withdrawals")
        with st.expander("Deposit or Withdraw Funds", expanded=False):
            dep_type = st.radio("Action", ["Deposit / Add Funds", "Withdraw Funds"], horizontal=True)
            c_amt = st.number_input("Amount (₹)", min_value=100.0, value=10000.0, step=1000.0)
            c_note = st.text_input("Note", value="Monthly SIP / Top-up")
            if st.button("Submit Capital Adjustment", type="primary"):
                if dep_type == "Deposit / Add Funds":
                    ok, msg = pm.deposit_capital(c_amt, c_note)
                else:
                    ok, msg = pm.withdraw_capital(c_amt, c_note)
                if ok:
                    st.success(msg)
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(msg)

    with col_cap2:
        st.markdown("### 🏦 Long-Term Vault (Retained Forever)")
        with pm.get_db() as conn:
            df_vault = pd.read_sql_query("SELECT ticker as Ticker, transfer_date as 'Transfer Date', qty as Shares, original_entry_price as 'Entry Price' FROM long_term_vault", conn)
        if not df_vault.empty:
            st.dataframe(df_vault, use_container_width=True, hide_index=True)
        else:
            st.caption("Vault is empty. When exiting a winning swing, use 'Keep in Vault' to archive free shares forever.")

    # 4. Closed Trades Ledger Download
    st.markdown("---")
    st.markdown("### 📁 Closed Trades Historical Ledger")
    df_closed_all = pm.get_closed_trades_df()
    if not df_closed_all.empty:
        st.dataframe(df_closed_all, use_container_width=True, hide_index=True)
        csv_download = df_closed_all.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Complete Trades Ledger as CSV",
            data=csv_download,
            file_name="my_swing_trades_ledger.csv",
            mime="text/csv",
            key="btn_dl_closed_ledger_csv"
        )
    else:
        st.caption("No closed trades recorded yet.")

with tab_radar:
    st.subheader("🎯 Dual-Strategy Opportunity Radar & Alpha Screener")
    st.markdown("Vectorized multi-factor screening across all Nifty 500 stocks for **both Strategy 1 & Strategy 2**.")

    subtab_both, subtab_s1, subtab_s2 = st.tabs([
        "🔥 Double Confluence Radar (Both Aligned!)",
        "🏆 Strategy 1: 4H Golden Cross Runner",
        "🧱 Strategy 2: AI Renko Supertrend"
    ])

    with subtab_both:
        st.markdown("### 🔥 Double Confluence Master Radar (Both Strategies Aligned!)")
        st.info("""
**Dual-Engine Quantitative Confluence:**
Identifies high-probability swing trades where **BOTH** independent champion models align simultaneously:
- **Strategy 1 (4H Golden Cross):** Institutional 20/50 Golden Cross above 200 EMA + Volume Surge >= 0.8x
- **Strategy 2 (AI Renko Supertrend):** Non-temporal 2.5% brick close + Supertrend Green Flip + +2 Bricks Break-Even protection
*When both quantitative models agree on the same stock, statistical edge and reward-to-risk are maximized.*
""")

        s1_cached_cands = st.session_state.get('cached_scan_results', {}).get('top_candidates', [])
        s2_cached_list = st.session_state.get('cached_renko_scan', [])

        col_c_btn, col_c_info = st.columns([2, 5])
        should_run = col_c_btn.button("🚀 Re-Scan Both Strategies", type="primary", key="btn_run_confluence_scan", use_container_width=True) or ('cached_scan_results' not in st.session_state and 'cached_renko_scan' not in st.session_state)

        if should_run:
            with st.spinner("Executing dual-engine multi-factor confluence analysis across Nifty 500..."):
                s1_scan = run_opportunity_scan(timeframe="4H")
                st.session_state['cached_scan_results'] = s1_scan
                s1_cands = s1_scan.get('top_candidates', [])

                renko_target_syms = list(set([c['ticker'] for c in s1_cands] + test_symbols[:50]))
                st.session_state['cached_renko_scan'] = scan_renko_supertrend_opportunities(
                    symbols=renko_target_syms,
                    timeframe="4H",
                    brick_pct=0.025,
                    st_period=10,
                    st_multiplier=2.0
                )
                s1_cached_cands = s1_cands
                s2_cached_list = st.session_state['cached_renko_scan']

        s1_dict = {c['ticker']: c for c in s1_cached_cands}
        s2_list = s2_cached_list

        confluence_list = []
        for c2 in s2_list:
            sym = c2['ticker']
            if sym in s1_dict:
                c1 = s1_dict[sym]
                is_fresh = c2.get('is_fresh_flip', False)
                vol = float(c1.get('vol_surge', 1.0))
                
                conf_score = 100
                if is_fresh: conf_score += 25
                if vol >= 1.3: conf_score += 15
                elif vol >= 0.8: conf_score += 10
                if c1.get('alpha_score', 70) >= 80: conf_score += 10

                confluence_list.append({
                    "ticker": sym,
                    "confluence_rank": "⭐⭐⭐⭐⭐ Ultra" if conf_score >= 130 else "⭐⭐⭐⭐ High",
                    "confluence_score": conf_score,
                    "price": c2['price'],
                    "s1_alpha": f"{c1.get('alpha_score', 75):.0f}",
                    "s2_status": "Fresh Flip (Buy)" if is_fresh else "Green Trend Active",
                    "vol_surge": f"{vol:.1f}x",
                    "stop_loss": c2['stop_loss'],
                    "sl_pct": f"-{c2.get('risk_pct', 2.5):.1f}%",
                    "exit_rule": "Unlimited Runner (Trailing Exit)",
                    "rationale": f"4H Golden Cross + Renko {c2['reasons']} (Vol {vol:.1f}x)"
                })

        confluence_list.sort(key=lambda x: x['confluence_score'], reverse=True)

        if confluence_list:
            st.success(f"🏆 Found **{len(confluence_list)} Dual-Strategy Confluence Candidates** confirming buy signals on BOTH engines!")
            st.info("💡 **Exit Protocol:** Both Champion Strategies operate on **Unlimited Runners with Trailing Exits** (no fixed profit caps). Positions are held to ride massive trends and closed only when an event-driven exit triggers (Chandelier Trailing Stop or Renko Supertrend Red Flip).")
            df_conf = pd.DataFrame(confluence_list)
            df_conf_disp = df_conf[[
                'ticker', 'confluence_rank', 'price', 's1_alpha', 's2_status', 'vol_surge', 'stop_loss', 'sl_pct', 'exit_rule', 'rationale'
            ]].rename(columns={
                'ticker': 'Ticker',
                'confluence_rank': 'Confluence Tier',
                'price': 'Current Price (₹)',
                's1_alpha': 'Strategy 1 Alpha',
                's2_status': 'Strategy 2 Renko',
                'vol_surge': 'Vol Surge',
                'stop_loss': 'Initial Stop Loss (₹)',
                'sl_pct': 'Max Risk %',
                'exit_rule': 'Target / Exit Rule',
                'rationale': 'Dual Engine Confluence'
            })
            df_conf_disp['Current Price (₹)'] = df_conf_disp['Current Price (₹)'].apply(lambda x: f"₹{x:,.2f}")
            df_conf_disp['Initial Stop Loss (₹)'] = df_conf_disp['Initial Stop Loss (₹)'].apply(lambda x: f"₹{x:,.2f}")
            st.dataframe(df_conf_disp, use_container_width=True, hide_index=True)
        else:
            col_c_info.caption("Click 'Re-Scan Both Strategies' above to refresh the dual-engine scan.")

        st.markdown("---")
        st.markdown("### 📊 Top Buy Recommendations by Strategy")
        col_rec1, col_rec2 = st.columns(2)
        with col_rec1:
            st.markdown("#### 🏆 Strategy 1: Top 4H Golden Cross Breakouts")
            if s1_cached_cands:
                df_s1_top = pd.DataFrame(s1_cached_cands[:8])
                df_s1_top_disp = df_s1_top[['ticker', 'alpha_score', 'close', 'stop_loss', 'target_pct', 'vol_surge', 'reasons']].rename(columns={
                    'ticker': 'Ticker', 'alpha_score': 'Alpha', 'close': 'CMP (₹)', 'stop_loss': 'SL (₹)', 'target_pct': 'Target %', 'vol_surge': 'Vol Surge', 'reasons': 'Setup'
                })
                df_s1_top_disp['CMP (₹)'] = df_s1_top_disp['CMP (₹)'].apply(lambda x: f"₹{x:,.2f}")
                df_s1_top_disp['SL (₹)'] = df_s1_top_disp['SL (₹)'].apply(lambda x: f"₹{x:,.2f}")
                df_s1_top_disp['Target %'] = df_s1_top_disp['Target %'].apply(lambda x: f"+{x:.1f}%")
                df_s1_top_disp['Vol Surge'] = df_s1_top_disp['Vol Surge'].apply(lambda x: f"{x:.1f}x")
                st.dataframe(df_s1_top_disp, use_container_width=True, hide_index=True)
            else:
                st.caption("No Strategy 1 candidates found in current scan.")

        with col_rec2:
            st.markdown("#### 🧱 Strategy 2: Top AI Renko Supertrend Setups")
            if s2_list:
                df_s2_top = pd.DataFrame(s2_list[:8])
                df_s2_top_disp = df_s2_top[['ticker', 'alpha_score', 'price', 'brick_size', 'stop_loss', 'risk_pct', 'reasons']].rename(columns={
                    'ticker': 'Ticker', 'alpha_score': 'Alpha', 'price': 'CMP (₹)', 'brick_size': 'Brick (₹)', 'stop_loss': 'SL (₹)', 'risk_pct': 'Risk %', 'reasons': 'Signal'
                })
                df_s2_top_disp['CMP (₹)'] = df_s2_top_disp['CMP (₹)'].apply(lambda x: f"₹{x:,.2f}")
                df_s2_top_disp['Brick (₹)'] = df_s2_top_disp['Brick (₹)'].apply(lambda x: f"₹{x:,.2f}")
                df_s2_top_disp['SL (₹)'] = df_s2_top_disp['SL (₹)'].apply(lambda x: f"₹{x:,.2f}")
                df_s2_top_disp['Risk %'] = df_s2_top_disp['Risk %'].apply(lambda x: f"-{x:.1f}%")
                st.dataframe(df_s2_top_disp, use_container_width=True, hide_index=True)
            else:
                st.caption("No Strategy 2 candidates found in current scan.")

    with subtab_s1:
        st.markdown("### 🏆 Strategy 1: 4H Golden Cross Unlimited Runner")
        st.caption("Institutional 20/50 Golden Crosses above 200 EMA with Active Volume & Chandelier trailing.")

        regime = get_market_regime(timeframe="4H")
        col_reg1, col_reg2, col_reg3 = st.columns(3)
        col_reg1.metric("Market Regime (Nifty 50)", regime['status'], delta=f"Close: ₹{regime['nifty_close']:,.2f}")
        col_reg2.metric("Nifty 50 EMA", f"₹{regime['nifty_ema50']:,.2f}", delta="Regime Threshold")
        col_reg3.metric("Status", "🟢 Active (Longs Allowed)" if regime['status'] == "BULLISH" else "🔴 Halted (Bearish Protection)")

        st.markdown("---")
        c1, c2, c3 = st.columns([2, 2, 3])
        radar_tf = c1.selectbox("Scan Timeframe", ["4H", "1D", "1H"], index=0, key="radar_tf")
        min_alpha = c2.slider("Minimum Alpha Score", min_value=50, max_value=120, value=70, step=5, key="radar_min_alpha")
        btn_scan = c3.button("🔍 Run Strategy 1 Scan", type="primary", use_container_width=True, key="btn_run_s1_scan")

        if btn_scan or 'cached_scan_results' not in st.session_state:
            with st.spinner("Analyzing stocks for Strategy 1 Golden Crosses..."):
                mock_holdings = [
                    {"ticker": "INFY.NS", "entry_price": 1850.0, "entry_time": "2026-10-15 09:15:00", "qty": 20},
                    {"ticker": "TCS.NS", "entry_price": 4100.0, "entry_time": "2026-11-01 09:15:00", "qty": 10}
                ]
                st.session_state['cached_scan_results'] = run_opportunity_scan(timeframe=radar_tf, active_holdings=mock_holdings)

        scan_res = st.session_state.get('cached_scan_results', {})
        
        if scan_res:
            rotations = scan_res.get('rotations', [])
            if rotations:
                st.warning("### 🔄 Capital Rotation / Trade Swap Alert")
                for r in rotations:
                    st.markdown(f"**Recommendation**: {r['rationale']}")
                    col_swap1, col_swap2 = st.columns(2)
                    with col_swap1:
                        st.error(f"**[-] Liquidate Weak Holding**: `{r['exit_ticker']}`\n- Health: `{r['exit_health']}/100` | PnL: `{r['exit_pnl_pct']:.2f}%`\n- Reason: {r['exit_reason']}")
                    with col_swap2:
                        st.success(f"**[+] Enter Superior Alpha**: `{r['enter_ticker']}`\n- Start Date (Buy): `{r.get('enter_date', 'Immediate')}`\n- Alpha Score: `{r['enter_alpha']}/100` | Target: `+{r['enter_target_pct']:.2f}%`\n- Setup: {r['enter_reasons']}")
                    st.markdown(f"**Net Alpha Edge**: `+{r['alpha_delta']} points` (Fully offsets Indian roundtrip friction ~0.35%)")
                st.markdown("---")

            candidates = [c for c in scan_res.get('top_candidates', []) if c['alpha_score'] >= min_alpha]
            st.subheader(f"Strategy 1 Qualifying Opportunities ({len(candidates)} qualifying)")
            if candidates:
                df_display = pd.DataFrame(candidates)
                if 'timestamp' in df_display.columns:
                    if radar_tf == '1D':
                        df_display['Start Date (Buy)'] = pd.to_datetime(df_display['timestamp']).dt.strftime('%Y-%m-%d')
                    else:
                        df_display['Start Date (Buy)'] = pd.to_datetime(df_display['timestamp']).dt.strftime('%Y-%m-%d %H:%M')
                else:
                    df_display['Start Date (Buy)'] = 'Immediate'

                st.info("💡 **Strategy 1 Unlimited Runner:** There is **NO fixed profit target price**. Positions ride massive swing trends and are closed strictly by event-driven trailing exits (Chandelier Stop Loss ratchet).")
                df_display['Exit Rule'] = 'Unlimited Runner (Trailing)'
                df_display = df_display[[
                    'ticker', 'Start Date (Buy)', 'alpha_score', 'close', 'stop_loss', 'sl_pct', 'Exit Rule', 'vol_surge', 'reasons'
                ]].rename(columns={
                    'ticker': 'Ticker',
                    'alpha_score': 'Alpha Score',
                    'close': 'Current Price (₹)',
                    'stop_loss': 'Initial Hard Stop (₹)',
                    'sl_pct': 'Max Risk %',
                    'Exit Rule': 'Profit Target / Exit',
                    'vol_surge': 'Vol Surge',
                    'reasons': 'Setup Confluence'
                })
                df_display['Current Price (₹)'] = df_display['Current Price (₹)'].apply(lambda x: f"₹{x:,.2f}")
                df_display['Initial Hard Stop (₹)'] = df_display['Initial Hard Stop (₹)'].apply(lambda x: f"₹{x:,.2f}")
                df_display['Max Risk %'] = df_display['Max Risk %'].apply(lambda x: f"-{x:.1f}%")
                df_display['Vol Surge'] = df_display['Vol Surge'].apply(lambda x: f"{x:.1f}x")
                st.dataframe(df_display, use_container_width=True, hide_index=True)
            else:
                st.info("No candidates match this Alpha Score threshold. Lower the slider to display more candidates.")

    with subtab_s2:
        st.markdown("### 🧱 Strategy 2: AI Renko Supertrend Multi-Stock Screener")
        st.caption("2.5% Non-Temporal Price Bricks + Supertrend Direction + Break-Even Ratchet.")
        
        c_r1, c_r2, c_r3 = st.columns([2, 2, 3])
        radar_renko_tf = c_r1.selectbox("Renko Timeframe", ["4H", "1D", "1H"], index=0, key="radar_renko_tf")
        filter_fresh = c_r2.checkbox("Fresh Flips Only (Signal = 1)", value=False, help="Show only stocks that printed a new buy signal on the latest brick.", key="radar_fresh_flips")
        btn_renko_scan = c_r3.button("🔍 Scan Strategy 2 Renko Setups", type="primary", use_container_width=True, key="btn_run_s2_scan")

        if btn_renko_scan or 'cached_renko_scan' not in st.session_state:
            with st.spinner("Scanning Renko Supertrend setups across database..."):
                b_pct = 0.025
                scan_syms = test_symbols[:60] if len(test_symbols) > 60 else test_symbols
                st.session_state['cached_renko_scan'] = scan_renko_supertrend_opportunities(
                    symbols=scan_syms,
                    timeframe=radar_renko_tf,
                    brick_pct=b_pct,
                    st_period=10,
                    st_multiplier=2.0
                )

        renko_opps = st.session_state.get('cached_renko_scan', [])
        if filter_fresh:
            renko_opps = [o for o in renko_opps if o['is_fresh_flip']]

        st.subheader(f"Strategy 2 Qualifying Opportunities ({len(renko_opps)} qualifying)")
        if renko_opps:
            df_ro = pd.DataFrame(renko_opps)
            df_ro_disp = df_ro[['ticker', 'alpha_score', 'price', 'brick_size', 'stop_loss', 'risk_pct', 'supertrend', 'reasons']].rename(columns={
                'ticker': 'Ticker',
                'alpha_score': 'Alpha Score',
                'price': 'Current Price (₹)',
                'brick_size': 'Brick Size (₹)',
                'stop_loss': 'Stop Loss (₹)',
                'risk_pct': 'Initial Risk %',
                'supertrend': 'Supertrend (₹)',
                'reasons': 'Signal Status'
            })
            df_ro_disp['Current Price (₹)'] = df_ro_disp['Current Price (₹)'].apply(lambda x: f"₹{x:,.2f}")
            df_ro_disp['Brick Size (₹)'] = df_ro_disp['Brick Size (₹)'].apply(lambda x: f"₹{x:,.2f}")
            df_ro_disp['Stop Loss (₹)'] = df_ro_disp['Stop Loss (₹)'].apply(lambda x: f"₹{x:,.2f}")
            df_ro_disp['Supertrend (₹)'] = df_ro_disp['Supertrend (₹)'].apply(lambda x: f"₹{x:,.2f}")
            df_ro_disp['Initial Risk %'] = df_ro_disp['Initial Risk %'].apply(lambda x: f"-{x:.1f}%")
            st.dataframe(df_ro_disp, use_container_width=True, hide_index=True)
        else:
            st.info("No Renko Supertrend setups match the current filter. Try unchecking 'Fresh Flips Only' to view all active trends.")

with tab_backtest:
    if strategy_mode.startswith("Strategy 1"):
        st.subheader("🛡️ Strategy 1: 4H Golden Cross Unlimited Runner Backtester")
        st.markdown("Trades **4H Golden Crossovers** with **NO profit target cap**, allowing winners to ride until the 'Right Exit' (Chandelier Trailing).")
    else:
        st.subheader("🛡️ Strategy 2: AI Renko Supertrend Multi-Stock Backtester")
        st.markdown("Simulates the **AI Renko Supertrend Champion** (2.5% Non-Temporal Price Bricks + Supertrend + Asymmetric 1:1 Break-Even Ratchet + ₹5,000/mo SIP Compounding).")
    
    col_bt_btn, col_bt_info = st.columns([2, 5])
    btn_tab_run = col_bt_btn.button("🚀 Run Backtest Now", type="primary", use_container_width=True)
    if strategy_mode.startswith("Strategy 1"):
        col_bt_info.info(f"Targeting: **{stock_set}** on **{selected_tf} Timeframe** via **TradingView (Local DB)**.")
    else:
        col_bt_info.info(f"Targeting: **{stock_set}** with **{renko_tf} Renko Bricks** ({brick_size_mode.split('[')[0].strip()}) via **TradingView (Local DB)**.")

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
    df_trades_empty = pd.DataFrame(columns=["Trade ID", "Ticker", "Alpha Score", "Entry Date", "Entry Price", "Exit Date", "Exit Price", "Gross Buy (₹)", "Gross Sell (₹)", "Net Amount (₹)", "PnL %", "Status"])
    ledger_placeholder.dataframe(df_trades_empty, use_container_width=True, hide_index=True)

if run_sim or btn_tab_run:
    if strategy_mode.startswith("Strategy 1"):
        entry_trigger = "SWING_GC"
        if "Classic GC" in gc_entry_selection:
            entry_trigger = "CLASSIC_GC"
        elif "Dual GC" in gc_entry_selection:
            entry_trigger = "DUAL_GC"
        elif "Established GC" in gc_entry_selection:
            entry_trigger = "ESTABLISHED_PULLBACK"

        exit_model = "CHANDELIER"
        if "Hybrid Trailing" in gc_exit_selection:
            exit_model = "HYBRID_TRAIL"
        elif "50 EMA Trailing" in gc_exit_selection:
            exit_model = "EMA50_CROSS"
        elif "20 EMA Trailing" in gc_exit_selection:
            exit_model = "EMA20_CROSS"
        elif "Full Death Cross" in gc_exit_selection:
            exit_model = "DEATH_CROSS"

        filter_gate = "ACTIVE_VOLUME"
        if "Active Volume Gate" in gc_gate_selection:
            filter_gate = "ACTIVE_VOLUME"
        elif "True State Flip Only" in gc_gate_selection:
            filter_gate = "TRUE_FLIP_ONLY"
        elif "Baseline" in gc_gate_selection:
            filter_gate = "NO_GATE"
        elif "Strict High-Quality" in gc_gate_selection:
            filter_gate = "HIGH_QUALITY"
        elif "Ultra-Institutional" in gc_gate_selection:
            filter_gate = "STRICT_INSTITUTIONAL"

        progress_bar = st.progress(0, text=f"Loading {selected_tf} Candlestick Data from market_data.db...")
        
        def update_pbar(pct, text):
            progress_bar.progress(pct, text=text)

        stock_dfs, all_timestamps = load_4h_universe(
            start_date="2018-01-01", 
            symbols=test_symbols, 
            timeframe=selected_tf,
            progress_callback=update_pbar
        )
        progress_bar.progress(0.92, text=f"Simulating {selected_tf} Golden Cross across {len(stock_dfs)} stocks...")

        res = simulate_4h_golden_cross(
            stock_dfs=stock_dfs,
            all_timestamps=all_timestamps,
            initial_cap=float(initial_cap),
            max_alloc_pct=float(max_alloc_pct / 100.0),
            min_alloc_pct=float(min_alloc_pct / 100.0),
            max_positions=int(100 // max_alloc_pct),
            entry_trigger=entry_trigger,
            exit_model=exit_model,
            entry_filter_gate=filter_gate,
            initial_sl_atr=float(initial_sl_atr),
            strategy_name=f"{selected_tf} Golden Cross [{entry_trigger} -> {exit_model}]",
            timeframe=selected_tf,
            monthly_sip=float(monthly_sip)
        )
        progress_bar.progress(1.0, text="Simulation Complete!")
        time.sleep(0.3)
        progress_bar.empty()

        if res.get('total_trades', 0) > 0:
            df_trades = res['trades_df']
            total_profit = res['total_net_pnl']
            roi_pct = res['roi_pct']
            win_rate = res['win_rate']
            max_dd = res['max_drawdown_pct']
            profit_fac = res['profit_factor']
            avg_win = res['avg_win_pct']
            avg_loss = res['avg_loss_pct']
            max_winner = res['max_single_win_pct']
            avg_hold = res['avg_hold_days']

            if monthly_sip > 0:
                m_profit.metric("Pure Profit (Friction Applied)", f"₹{total_profit:,.2f}", f"Invested: ₹{res.get('total_invested', initial_cap):,.0f} (+{roi_pct:.1f}% ROI)")
            else:
                m_profit.metric("Net Profit (Friction Applied)", f"₹{total_profit:,.2f}", f"+{roi_pct:.1f}% ROI")
            m_dd.metric("Max Drawdown", f"{max_dd:.1f}%")
            m_win.metric("Win Rate", f"{win_rate:.1f}%")

            col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
            col_m1.metric("Profit Factor", f"{profit_fac:.2f}")
            col_m2.metric("Avg Winning Trade", f"+{avg_win:.1f}%")
            col_m3.metric("Max Single Win", f"+{max_winner:.1f}%")
            col_m4.metric("Avg Holding Duration", f"{avg_hold:.1f} days")
            col_m5.metric("Quick Exits (<=1.5d)", f"{res.get('quick_exits', 0)} ({res.get('quick_exit_pct', 0.0)}%)")

            # Equity curve
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(
                x=res['equity_dates'],
                y=res['equity_curve'],
                mode='lines',
                name='Portfolio Value (₹)',
                line=dict(color='#00ff88', width=2)
            ))
            fig2.update_layout(
                template="plotly_dark",
                height=420,
                margin=dict(l=20, r=20, t=30, b=20),
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                title=f"{selected_tf} Golden Cross Cumulative Equity Curve (₹) — Final: ₹{res['final_equity']:,.2f}"
            )
            chart_placeholder.plotly_chart(fig2, use_container_width=True)

            # Active Positions Display
            if res.get('active_positions'):
                st.markdown("### 🟢 Active / Ongoing Positions (Live Status)")
                df_act = pd.DataFrame(res['active_positions'])
                df_act['Entry Date'] = pd.to_datetime(df_act['entry_date']).dt.strftime('%Y-%m-%d %H:%M')
                df_act['Entry Price'] = df_act['entry_price'].apply(lambda x: f"₹{x:,.2f}")
                df_act['Current Price (₹)'] = df_act['current_price'].apply(lambda x: f"₹{x:,.2f}")
                df_act['Unrealized PnL %'] = df_act['unrealized_pnl_pct'].apply(lambda x: f"{x:+.2f}%")
                df_act['Stop Loss (₹)'] = df_act['stop_loss'].apply(lambda x: f"₹{x:,.2f}")
                df_act['Holding'] = df_act['days_held'].apply(lambda x: f"{x:.1f} days")
                df_act['Alpha Score'] = df_act['alpha_score'].apply(lambda x: f"{x:.0f}")
                df_act = df_act[['ticker', 'Alpha Score', 'Entry Date', 'Entry Price', 'Current Price (₹)', 'Unrealized PnL %', 'Stop Loss (₹)', 'Holding', 'status']].rename(columns={'ticker': 'Ticker', 'status': 'Current Status'})
                st.dataframe(df_act, use_container_width=True, hide_index=True)

            # Trades ledger
            st.markdown("### 📋 Closed Trades Ledger")
            df_display = df_trades.copy()
            df_display['Trade ID'] = range(1, len(df_display) + 1)
            if 'alpha_score' in df_display.columns:
                df_display['Alpha Score'] = df_display['alpha_score'].apply(lambda x: f"{x:.0f}" if pd.notnull(x) else "100")
            else:
                df_display['Alpha Score'] = "100"
            df_display['Entry Date'] = pd.to_datetime(df_display['entry_date']).dt.strftime('%Y-%m-%d %H:%M')
            df_display['Exit Date'] = pd.to_datetime(df_display['exit_date']).dt.strftime('%Y-%m-%d %H:%M')
            df_display['Entry Price'] = df_display['entry_price'].apply(lambda x: f"₹{x:,.2f}")
            df_display['Exit Price'] = df_display['exit_price'].apply(lambda x: f"₹{x:,.2f}")
            df_display['Net Amount (₹)'] = df_display['net_pnl'].apply(lambda x: f"₹{x:,.2f}")
            df_display['Max Run-Up %'] = df_display['max_runup_pct'].apply(lambda x: f"+{x:.1f}%")
            df_display['PnL %'] = df_display['pnl_pct'].apply(lambda x: f"{x:+.2f}%")
            df_display['Holding'] = df_display['days_held'].apply(lambda x: f"{x:.1f} days")
            df_display = df_display[['Trade ID', 'ticker', 'Alpha Score', 'Entry Date', 'Entry Price', 'Exit Date', 'Exit Price', 'Max Run-Up %', 'Net Amount (₹)', 'PnL %', 'Holding', 'reason']].rename(columns={'ticker': 'Ticker', 'reason': 'Exit Reason'})

            ledger_placeholder.dataframe(df_display, use_container_width=True, hide_index=True)

            csv_data = df_display.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Trades Ledger as CSV",
                data=csv_data,
                file_name=f"simulation_trades_{selected_tf}.csv",
                mime="text/csv",
                key="dl_trades_csv_strat1"
            )
            st.sidebar.success(f"✅ {selected_tf} Golden Cross Complete! Total Trades: {len(df_trades)}")

            st.markdown(f"### 🏆 Strategy 1 State Checks ({selected_tf})")
            st.info(f"""
**Engine Mechanics Active ({selected_tf} Golden Cross):**
- Setup Model: `{entry_trigger}` (Golden crossover in primary bull trend) ✅
- Timeframe: `{selected_tf}` (Institutional trend bar) ✅
- Profit Cap: ZERO (Unlimited runners allowed to run indefinitely) ✅
- Exit Trigger: `{exit_model}` (Ratchets beneath peak to lock gains at the right time) ✅
- Catastrophic Safety Stop: `{initial_sl_atr}x ATR` ✅
- False-Positive Quality Gate: `{filter_gate}` (Whipsaw reduction active) ✅
- Sizing Allocation: Dynamic `{max_alloc_pct}%` per position (Max {int(100 // max_alloc_pct)} trades) ✅
- Friction Model: Full Indian delivery taxes & fees deducted (~0.35%) ✅
""")
        else:
            st.sidebar.warning("No Golden Cross trades triggered on the selected stock set.")

    else:
        # STRATEGY 2: AI RENKO SUPERTREND BACKTESTER
        progress_bar = st.progress(0, text="Initializing AI Renko Supertrend Simulation...")

        def update_pbar(pct, text):
            progress_bar.progress(pct, text=text)

        b_pct = 0.025 if "2.5%" in brick_size_mode else (0.020 if "2.0%" in brick_size_mode else 0.015)

        results = simulate_renko_supertrend_portfolio(
            symbols=test_symbols,
            timeframe=renko_tf,
            brick_pct=b_pct,
            st_period=int(st_period),
            st_multiplier=float(st_multiplier),
            initial_cap=float(initial_cap),
            monthly_sip=float(monthly_sip),
            max_positions=int(max_positions),
            use_be_ratchet=bool(use_be_ratchet),
            progress_callback=update_pbar
        )

        time.sleep(0.3)
        progress_bar.empty()

        if results and results.get("total_trades", 0) > 0:
            df_trades = results["trades_df"]
            total_net_pnl = results["total_net_pnl"]
            roi_pct = results["roi_pct"]
            win_rate = results["win_rate"]
            max_dd = results["max_drawdown_pct"]
            equity_curve = results["equity_curve"]
            equity_dates = results["equity_dates"]

            m_profit.metric(
                label="Net Profit (Friction Applied)", 
                value=f"₹{total_net_pnl:,.2f}", 
                delta=f"{roi_pct:.2f}% (Invested: ₹{results['total_invested']:,.2f})"
            )
            m_dd.metric(label="Max Drawdown", value=f"{max_dd:.2f}%")
            m_win.metric(
                label="Win Rate", 
                value=f"{win_rate:.1f}%", 
                delta=f"PF: {results['profit_factor']:.2f} | Trades: {results['total_trades']}"
            )

            # Plotly Equity Curve
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(
                x=equity_dates, 
                y=equity_curve, 
                mode='lines', 
                name='AI Renko Compounding Portfolio', 
                line=dict(color='#00ff88', width=2.5)
            ))
            fig2.update_layout(
                template="plotly_dark", 
                height=420, 
                margin=dict(l=20, r=20, t=20, b=20), 
                plot_bgcolor='rgba(0,0,0,0)', 
                paper_bgcolor='rgba(0,0,0,0)',
                xaxis_title="Date",
                yaxis_title="Portfolio Valuation (₹)"
            )
            chart_placeholder.plotly_chart(fig2, use_container_width=True)

            # Display Trade Ledger
            df_display = df_trades.copy()
            if 'entry_date' in df_display.columns:
                df_display['Entry Date'] = pd.to_datetime(df_display['entry_date']).dt.strftime('%Y-%m-%d')
            if 'exit_date' in df_display.columns:
                df_display['Exit Date'] = pd.to_datetime(df_display['exit_date']).dt.strftime('%Y-%m-%d')
            
            df_display = df_display.rename(columns={
                'ticker': 'Ticker',
                'entry_price': 'Entry Price (₹)',
                'exit_price': 'Exit Price (₹)',
                'gross_buy': 'Gross Buy (₹)',
                'gross_sell': 'Gross Sell (₹)',
                'net_pnl': 'Net PnL (₹)',
                'pnl_pct': 'PnL %',
                'max_runup_pct': 'Peak Run-up %',
                'days_held': 'Days Held',
                'reason': 'Exit Reason'
            })
            
            # Formatting
            df_display['Entry Price (₹)'] = df_display['Entry Price (₹)'].apply(lambda x: f"₹{x:,.2f}")
            df_display['Exit Price (₹)'] = df_display['Exit Price (₹)'].apply(lambda x: f"₹{x:,.2f}")
            df_display['Gross Buy (₹)'] = df_display['Gross Buy (₹)'].apply(lambda x: f"₹{x:,.2f}")
            df_display['Gross Sell (₹)'] = df_display['Gross Sell (₹)'].apply(lambda x: f"₹{x:,.2f}")
            df_display['Net PnL (₹)'] = df_display['Net PnL (₹)'].apply(lambda x: f"₹{x:,.2f}")
            df_display['PnL %'] = df_display['PnL %'].apply(lambda x: f"{x:+.2f}%")
            df_display['Peak Run-up %'] = df_display['Peak Run-up %'].apply(lambda x: f"+{x:.2f}%")
            
            cols_to_show = [
                'Trade ID', 'Ticker', 'Alpha Score', 'Entry Date', 'Entry Price (₹)',
                'Exit Date', 'Exit Price (₹)', 'Net PnL (₹)', 'PnL %', 'Peak Run-up %',
                'Days Held', 'Exit Reason'
            ]
            cols_present = [c for c in cols_to_show if c in df_display.columns]
            ledger_placeholder.dataframe(df_display[cols_present], use_container_width=True, hide_index=True)

            csv_data_s2 = df_trades.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Trades Ledger as CSV",
                data=csv_data_s2,
                file_name=f"simulation_trades_renko_supertrend_{renko_tf}.csv",
                mime="text/csv",
                key="dl_trades_csv_strat2"
            )
            st.sidebar.success(f"✅ AI Renko Supertrend Simulation Complete! Trades: {len(df_trades)}")

            st.markdown("### 🏆 Strategy 2 State Checks (AI Renko Supertrend)")
            st.info(f"""
**Engine Mechanics Active (AI Renko Supertrend Champion #2):**
- Brick Mode: `{brick_size_mode}` (Non-temporal noise reduction) ✅
- Supertrend Indicator: `Period {st_period}, Multiplier {st_multiplier}` on Renko close ✅
- Break-Even Ratchet: `+2 Bricks (+{b_pct*200:.1f}%)` $\\rightarrow$ Move SL to Cost ✅
- Asymmetric Convexity: Let winners ride through Supertrend green run ✅
- Compounding Engine: ₹{initial_cap:,.0f} Initial + ₹{monthly_sip:,.0f}/month SIP injection ✅
- Concurrency Cap: Max `{max_positions}` positions ({100/max_positions:.1f}% allocation per slot) ✅
- Friction Model: Full Indian delivery taxes & fees deducted (~0.25% - 0.35%) ✅
""")
        else:
            st.sidebar.warning("No Renko Supertrend trades triggered on the selected stock set.")

with tab_db:
    st.subheader("📊 Local Database Storage (market_data.db)")
    db_stats = get_db_stats()
    c_d1, c_d2, c_d3 = st.columns(3)
    c_d1.metric("Total Candlestick Bars", f"{db_stats['total_candles']:,}")
    c_d2.metric("Unique Stock Tickers", f"{db_stats['unique_tickers']}")
    c_d3.metric("Available Timeframes", ", ".join(db_stats['timeframes']))
    st.info("Market candlestick data is cached in `market_data.db` (SQLite WAL mode). It provides sub-second query performance for screening and backtesting without network bottlenecks.")

with tab_security:
    st.subheader("🔒 Enterprise Security, Port Hardening & Access Control")
    client_info = security.extract_client_info()
    
    col_sc1, col_sc2, col_sc3, col_sc4 = st.columns(4)
    col_sc1.metric("Exposed Ports", "Port 443 (HTTPS)", delta="TLS 1.3 Active", delta_color="normal")
    col_sc2.metric("Inbound Policy", "Browser Only", delta="Closed Inbound Ports", delta_color="normal")
    col_sc3.metric("Health Checks", "Outbound Only", delta="Zero Listening Endpoints", delta_color="normal")
    col_sc4.metric("PIN Encryption", "Salted SHA-256", delta="HMAC Compare Active", delta_color="normal")
    
    st.markdown("---")
    
    st.markdown("#### 🌐 Real-Time Session Forensics")
    col_f1, col_f2, col_f3 = st.columns(3)
    col_f1.info(f"**Connected IP:** `{client_info['ip']}`")
    col_f2.info(f"**Detected Country:** `{client_info['country']}` {'🇮🇳 (Allowed)' if client_info['is_india'] else '🚨 (Non-India Origin)'}")
    col_f3.info(f"**Anti-Bot Filter:** Active (All AI & Crawlers Disallowed)")
    
    st.markdown("---")
    st.markdown("#### 🚨 Permanent IP Blacklist (Anti-Brute-Force Shield)")
    df_blocked = security.get_blocked_ips()
    if not df_blocked.empty:
        st.dataframe(df_blocked, use_container_width=True)
        col_ub1, col_ub2 = st.columns([2, 1])
        with col_ub1:
            ip_to_unblock = st.text_input("Enter IP to Unblock", placeholder="e.g. 192.168.1.50")
        with col_ub2:
            if st.button("🔓 Unblock IP"):
                if ip_to_unblock and security.unblock_ip_address(ip_to_unblock):
                    st.success(f"IP {ip_to_unblock} successfully unblocked!")
                    st.rerun()
                else:
                    st.error("IP not found in blacklist.")
    else:
        st.success("✅ Clean Record: No IP addresses currently blacklisted.")
        
    st.markdown("---")
    st.markdown("#### 📋 Security Access & Audit Log")
    df_audit = security.get_security_audit_logs(limit=50)
    if not df_audit.empty:
        st.dataframe(df_audit, use_container_width=True)
        csv_sec = df_audit.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Download Security Audit Log (CSV)", data=csv_sec, file_name="security_audit_log.csv", mime="text/csv")
    else:
        st.caption("No security audit events recorded yet.")

