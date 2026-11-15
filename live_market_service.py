"""
live_market_service.py
======================
The automated orchestrator that runs at the 4 NSE market checkpoints:
- 09:45 AM IST: Morning volatility bypass & swing check.
- 01:15 PM IST: 4-Hour bar completion & trailing stop adjustment (feed syncs bar completed at 13:15).
- 03:20 PM IST: Pre-market close assessment (10 mins before close for manual broker execution).
- 05:00 PM IST: Post-market close assessment, finalized Bhavcopy settlement & Daily Summary.

Integrates:
1. Incremental 4H candle sync via update_market_data.py.
2. Dual-Stage Chandelier Ratchet trailing exit engine (dual_chandelier_engine.py).
3. Swing Golden Cross scanner with Alpha Scoring (golden_cross_4h_optimizer.py).
4. Multi-channel alert dispatch (Telegram + Gmail via notifications.py).
5. Weekly Nifty 500 universe rebalance tracker.
"""

import os
import sys
import time
import logging
from datetime import datetime, time as dtime
import pandas as pd
import pytz

import portfolio_manager as pm
import dual_chandelier_engine as dce
import notifications as notif
from database import load_candles_from_db, get_connection
from update_market_data import update_database_incremental
from golden_cross_4h_optimizer import calculate_4h_indicators, UNIVERSE_4H

logger = logging.getLogger("LiveMarketService")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

IST = pytz.timezone("Asia/Kolkata")
NSE_NIFTY500_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
NIFTY500_CSV = "nifty500_symbols.csv"


def is_market_open_today() -> bool:
    """Checks if today is a weekday (Monday to Friday)."""
    now_ist = datetime.now(IST)
    return now_ist.weekday() < 5  # 0=Monday, 4=Friday


def check_nifty500_rebalance():
    """Checks for newly listed stocks in Nifty 500 and flags them for backfilling."""
    try:
        import requests
        import io
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(NSE_NIFTY500_URL, headers=headers, timeout=15)
        if res.status_code == 200:
            df_nse = pd.read_csv(io.StringIO(res.text))
            latest_symbols = set(df_nse['Symbol'].dropna().unique())

            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT DISTINCT ticker FROM market_data WHERE timeframe='4H'")
                existing = {r[0].replace(".NS", "") for r in cur.fetchall()}

            new_additions = latest_symbols - existing
            if new_additions:
                logger.info(f"Nifty 500 Rebalance: Found {len(new_additions)} new stocks: {new_additions}")
                for sym in list(new_additions)[:5]: # alert sample
                    notif.send_telegram_message(f"ℹ️ *Nifty 500 Addition:* `{sym}` added to index universe. Tracking initiated.")
    except Exception as e:
        logger.warning(f"Nifty 500 rebalance check skipped: {e}")


def execute_checkpoint_scan(checkpoint_name: str = "Mid-Day"):
    """
    Executes the full evaluation cycle:
    1. Incremental DB update across all stocks.
    2. Exits check (Emergency stop & Trailing Chandelier).
    3. Capacity & cash check.
    4. Nifty 50 Macro Regime filter.
    5. Golden Cross screening & Alpha ranking.
    6. Alerts dispatch.
    """
    now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    logger.info(f"=== Starting {checkpoint_name} Checkpoint at {now_str} ===")

    # 1. Incremental DB update
    try:
        update_database_incremental(timeframe="4H")
    except Exception as e:
        logger.error(f"Incremental DB update error: {e}")

    # 2. Check Nifty 50 Macro Regime
    df_nifty = load_candles_from_db("NIFTY", timeframe="4H")
    if df_nifty.empty or len(df_nifty) < 50:
        regime_bullish = True
    else:
        df_nifty = df_nifty.sort_values(by="timestamp").reset_index(drop=True)
        df_nifty['EMA50'] = df_nifty['close'].ewm(span=50, adjust=False).mean()
        latest_nifty = df_nifty.iloc[-1]
        regime_bullish = bool(latest_nifty['close'] > latest_nifty['EMA50'])
        logger.info(f"Nifty 50 Regime: {'🟢 BULLISH' if regime_bullish else '🔴 BEARISH'} (Close: ₹{latest_nifty['close']:,.1f} vs 50 EMA: ₹{latest_nifty['EMA50']:,.1f})")

    # 3. Load latest completed candle for active holdings and evaluate EXITS
    active_trades = pm.get_active_trades()
    current_bars = {}

    for pos in active_trades:
        sym = pos['ticker']
        df_sym = load_candles_from_db(sym, timeframe="4H")
        if not df_sym.empty and len(df_sym) >= 50:
            df_feat = calculate_4h_indicators(df_sym, chandelier_mult=3.0)
            if not df_feat.empty:
                current_bars[sym] = df_feat.iloc[-1].to_dict()

    # Run Dual-Stage Chandelier Exit Engine
    exits = dce.update_trailing_stops_and_check_exits(current_bars)
    for ex in exits:
        logger.info(f"Firing Exit Alert for {ex['ticker']}: {ex['exit_reason']}")
        notif.dispatch_exit_alert(
            ticker=ex['ticker'],
            exit_price=ex['exit_price'],
            reason=ex['exit_reason'],
            pnl_pct=ex['pnl_pct'],
            net_pnl_rs=ex['net_pnl_rs'],
            holding_days=ex['holding_days'],
            current_qty=ex['current_qty']
        )

    # 4. Check Capacity & Cash Availability
    summary = pm.get_portfolio_summary()
    if not summary['can_take_new_trade']:
        logger.info(f"Capacity full ({summary['effective_slots_occupied']:.1f}/3.0 slots) or insufficient cash (₹{summary['cash_available']:,.2f}). Skipping entry scan.")
        return

    # If Nifty regime is Bearish, protect capital and skip new longs
    if not regime_bullish:
        logger.info("Nifty 50 is Bearish. New swing entries halted to preserve capital.")
        return

    # 5. Scan Universe for Swing Golden Cross Breakouts
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT ticker FROM market_data WHERE timeframe='4H' AND ticker != 'NIFTY' AND ticker != 'NIFTY.NS'")
        universe = [r[0] for r in cur.fetchall()]

    candidates = []
    active_syms = {p['ticker'] for p in active_trades}

    for sym in universe:
        if sym in active_syms:
            continue

        raw_df = load_candles_from_db(sym, timeframe="4H")
        if raw_df.empty or len(raw_df) < 50:
            continue

        feat_df = calculate_4h_indicators(raw_df, chandelier_mult=3.0)
        if feat_df.empty:
            continue

        b = feat_df.iloc[-1].to_dict()
        c = float(b['close'])
        atr = float(b.get('ATR', 0))

        # Swing GC Trigger: 20 EMA > 50 EMA above 200 EMA + Active Volume >= 0.8x
        is_gc = b.get('GC_20_50', False) and (c > b.get('EMA200', 0))
        vol_surge = float(b.get('Vol_Surge', 1.0))

        if is_gc and vol_surge >= 0.8:
            alpha = 70.0 + 30.0
            reasons = ["20/50 Golden Cross above 200 EMA"]

            if vol_surge >= 1.3:
                alpha += 15.0
                reasons.append(f"High Vol Surge ({vol_surge:.1f}x)")
            else:
                reasons.append(f"Active Vol ({vol_surge:.1f}x)")

            if b.get('Chandelier_Dir', 0) == 1:
                alpha += 10.0
                reasons.append("Chandelier Buy Zone Aligned")

            hard_sl = round(c - (2.3 * atr), 2) if atr > 0 else round(c * 0.95, 2)
            candidates.append({
                "ticker": sym,
                "price": c,
                "atr": atr,
                "stop_loss": hard_sl,
                "alpha_score": alpha,
                "reasons": " | ".join(reasons)
            })

    # Sort descending by Alpha Score
    candidates.sort(key=lambda x: x['alpha_score'], reverse=True)

    # Filter high-conviction options (Alpha >= 110.0, or top candidates)
    high_alpha_options = [c for c in candidates if c['alpha_score'] >= 110.0]
    display_candidates = high_alpha_options if high_alpha_options else candidates[:3]

    available_slots = int(summary['effective_slots_available'])

    if display_candidates and available_slots > 0:
        # Send an introductory advisory dispatch
        num_options = min(len(display_candidates), 5)
        intro_msg = (
            f"🎯 *SCANNER ALERT: {num_options} High-Alpha Setup(s) Detected!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"• Available Slots: *{available_slots} open slot(s)*\n"
            f"• Target Allocation per Slot: *₹{summary['slot_target_allocation']:,.2f}*\n"
            f"• Available Cash: *₹{summary['cash_available']:,.2f}*\n\n"
            f"👉 *Review the top options below and choose which one(s) you wish to execute:*"
        )
        notif.send_telegram_message(intro_msg)

        # Dispatch alert cards for up to top 5 high-conviction options
        for cand in display_candidates[:5]:
            sug_alloc = summary['slot_target_allocation']
            sug_qty = int(sug_alloc // cand['price'])
            if sug_qty <= 0:
                continue

            logger.info(f"Dispatching Multi-Choice Option: {cand['ticker']} (Alpha {cand['alpha_score']})")
            notif.dispatch_buy_alert(
                ticker=cand['ticker'],
                signal_price=cand['price'],
                suggested_qty=sug_qty,
                stop_loss=cand['stop_loss'],
                alpha_score=cand['alpha_score'],
                reasons=cand['reasons'],
                available_cash=summary['cash_available'],
                suggested_alloc=sug_alloc
            )


def run_market_scheduler():
    """
    Main loop that triggers the 4 market checkpoints during trading days:
    - 09:45 AM IST: Morning volatility bypass & swing check.
    - 01:20 PM IST: 4-Hour bar completion (13:15 bar with 12-15m feed latency buffer) & trailing stop adjustment.
    - 03:20 PM IST: Pre-market close assessment (10 mins before close for order execution).
    - 05:00 PM IST: Post-market close assessment, finalized Bhavcopy settlement & Daily Summary.
    """
    logger.info("Live Market Service running in background. Waiting for 4 market checkpoints...")

    checkpoints_fired_today = set()
    current_date = datetime.now(IST).date()

    while True:
        now_ist = datetime.now(IST)
        today = now_ist.date()

        # Reset checkpoints on a new day
        if today != current_date:
            checkpoints_fired_today.clear()
            current_date = today
            if now_ist.weekday() == 0:  # Monday
                check_nifty500_rebalance()

        if is_market_open_today():
            curr_time = now_ist.time()

            # Checkpoint 1: 09:45 AM (Morning volatility bypass & swing check)
            if dtime(9, 45) <= curr_time <= dtime(9, 58) and "CP1" not in checkpoints_fired_today:
                execute_checkpoint_scan("Checkpoint 1 (09:45 AM Volatility Bypass & Swing Check)")
                checkpoints_fired_today.add("CP1")

            # Checkpoint 2: 01:20 PM (01:15 PM 4H Bar completion with feed delay buffer)
            elif dtime(13, 15) <= curr_time <= dtime(13, 30) and "CP2" not in checkpoints_fired_today:
                execute_checkpoint_scan("Checkpoint 2 (01:15 PM 4H Bar Completion & Trailing Stop Adjustment)")
                checkpoints_fired_today.add("CP2")

            # Checkpoint 3: 03:20 PM (Pre-market close assessment before 03:30 closing bell)
            elif dtime(15, 20) <= curr_time <= dtime(15, 30) and "CP3" not in checkpoints_fired_today:
                execute_checkpoint_scan("Checkpoint 3 (03:20 PM Pre-Market Close Assessment)")
                checkpoints_fired_today.add("CP3")

            # Checkpoint 4: 05:00 PM (Post-market close assessment & Daily Summary)
            elif dtime(17, 0) <= curr_time <= dtime(17, 20) and "CP4" not in checkpoints_fired_today:
                execute_checkpoint_scan("Checkpoint 4 (05:00 PM Post-Market Close Assessment)")
                
                # Dispatch comprehensive end-of-day summary
                summary = pm.get_portfolio_summary()
                active = pm.get_active_trades()
                df_closed = pm.get_closed_trades_df()
                csv_path = "trades_ledger_export.csv"
                df_closed.to_csv(csv_path, index=False)
                notif.dispatch_daily_summary(summary, active, csv_attachment=csv_path)
                
                checkpoints_fired_today.add("CP4")

        time.sleep(30)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", action="store_true", help="Run a checkpoint scan immediately for testing")
    args = parser.parse_args()

    if args.now:
        execute_checkpoint_scan("Manual Test Scan")
    else:
        run_market_scheduler()

