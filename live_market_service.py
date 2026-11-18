"""
live_market_service.py
======================
Automated market orchestrator for Sentinel-Swing Trader:
- Runs every 1 hour during active NSE trading hours (09:15 AM to 03:30 PM IST):
  • 10:15 AM IST (Checkpoint 1)
  • 11:15 AM IST (Checkpoint 2)
  • 12:15 PM IST (Checkpoint 3)
  • 01:15 PM IST (Checkpoint 4 - 4H bar sync)
  • 02:15 PM IST (Checkpoint 5)
  • 03:15 PM IST (Checkpoint 6 - Pre-close 15 mins before market close)
- Final post-market closing checkpoint:
  • 05:30 PM IST (Checkpoint 7 - Post-close 5:30 PM settlement & EOD summary)

Core Notification Rules:
1. TELEGRAM: Every hour (and at 5:30 PM), dispatches the live status of all open swing trades:
   "• Entry Price: , Current Price: , Estimated Net PnL: , Holding Duration: , Open Shares: "
2. GMAIL: Sends email ONLY if action needs to be taken (Buy Signal or Exit/Stop-loss Hit).
   Zero hourly status spam to Gmail.
"""

import os
import sys
import time
import logging
from datetime import datetime, time as dtime
from typing import Dict, List, Tuple, Optional
import pandas as pd
import pytz
import yfinance as yf

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


def is_market_open_today() -> bool:
    """Checks if today is a weekday (Monday to Friday)."""
    now_ist = datetime.now(IST)
    return now_ist.weekday() < 5  # 0=Monday, 4=Friday


def check_nifty500_rebalance():
    """Checks for newly listed stocks in Nifty 500 and flags them for tracking."""
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
                logger.info(f"Nifty 500 Rebalance: Found {len(new_additions)} new stocks.")
                for sym in list(new_additions)[:5]:
                    notif.send_telegram_message(f"ℹ️ *Nifty 500 Addition:* `{sym}` added to index universe. Tracking initiated.")
    except Exception as e:
        logger.warning(f"Nifty 500 rebalance check skipped: {e}")


def fetch_live_quotes(tickers: List[str]) -> Dict[str, Dict]:
    """
    Fetches real-time price, day high, day low, and previous close for a list of tickers.
    Uses yfinance fast_info for instant execution with fallback to history.
    """
    quotes = {}
    if not tickers:
        return quotes

    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            fi = t.fast_info
            cmp = float(fi.get('lastPrice') or fi.get('regularMarketPrice') or 0.0)
            high = float(fi.get('dayHigh') or cmp)
            low = float(fi.get('dayLow') or cmp)
            prev_close = float(fi.get('previousClose') or fi.get('regularMarketPreviousClose') or cmp)

            # Fallback to 1-day history if fast_info is blank
            if cmp == 0.0:
                hist = t.history(period="1d")
                if not hist.empty:
                    last_row = hist.iloc[-1]
                    cmp = float(last_row['Close'])
                    high = float(last_row['High'])
                    low = float(last_row['Low'])
                    prev_close = cmp

            if cmp > 0.0:
                quotes[ticker] = {
                    "cmp": round(cmp, 2),
                    "high": round(high, 2),
                    "low": round(low, 2),
                    "prev_close": round(prev_close, 2)
                }
        except Exception as e:
            logger.warning(f"Error fetching live quote for {ticker}: {e}")

    return quotes


def check_active_positions_and_report_status(checkpoint_title: str) -> List[Dict]:
    """
    Evaluates every active swing trade against real-time market quotes:
    1. Fetches live CMP, day's high, and day's low.
    2. Updates peak high (max_high) in database.
    3. Checks Stop Loss breach:
       - Hard Catastrophic Stop (day_low <= initial_stop or cmp <= initial_stop)
       - Trailing Chandelier Stop (cmp < current_trailing_stop)
       If breached: Dispatches Actionable Exit Alert to TELEGRAM AND GMAIL!
    4. Compiles live status of all active trades in the exact requested format:
       • Entry Price: , Current Price: , Estimated Net PnL: , Holding Duration:  Open Shares: 
    5. Sends hourly status to TELEGRAM ONLY (Zero email!).
    """
    active_trades = pm.get_active_trades()
    summary = pm.get_portfolio_summary()
    now = datetime.now()

    if not active_trades:
        # No active positions open
        msg = (
            f"🔔 *HOURLY TRADE STATUS UPDATE* ({checkpoint_title})\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"ℹ️ *No open swing trades active.*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💼 *Cash Available:* ₹{summary['cash_available']:,.2f} ({summary['cash_pct']:.1f}%)\n"
            f"🎯 *Slots Available:* {summary['effective_slots_available']:.1f} / 3.0 Open"
        )
        notif.send_telegram_message(msg)
        return []

    # Fetch live quotes for all active holdings
    tickers = [t['ticker'] for t in active_trades]
    quotes = fetch_live_quotes(tickers)

    trade_cards = []
    exits_triggered = []

    for pos in active_trades:
        ticker = pos['ticker']
        clean_ticker = ticker.replace(".NS", "")
        entry_price = float(pos['entry_price'])
        qty = int(pos['qty'])
        initial_stop = float(pos['initial_stop'])
        trailing_stop = float(pos['current_trailing_stop'])
        max_high = float(pos.get('max_high', entry_price))
        stage = int(pos.get('chandelier_stage', 1))

        quote = quotes.get(ticker, {})
        cmp = quote.get('cmp', entry_price)
        day_high = quote.get('high', cmp)
        day_low = quote.get('low', cmp)

        # Update max_high in database if new peak reached
        if day_high > max_high:
            max_high = day_high
            with pm.get_db() as conn:
                conn.cursor().execute(
                    "UPDATE active_swing_trades SET max_high = ? WHERE ticker = ?",
                    (max_high, ticker)
                )
                conn.commit()

        # Calculate PnL & holding duration
        pnl_pct = ((cmp - entry_price) / entry_price) * 100.0
        gross_buy = qty * entry_price
        gross_sell = qty * cmp
        friction = pm.calculate_friction(gross_sell, is_buy=False) + pm.calculate_friction(gross_buy, is_buy=True)
        net_pnl_rs = (gross_sell - gross_buy) - friction

        try:
            entry_dt = pd.to_datetime(pos['entry_date'])
            holding_days = (now - entry_dt).total_seconds() / 86400.0
        except Exception:
            holding_days = 0.0

        # Check Stop Loss / Exit Breaches
        exit_triggered = False
        exit_reason = None
        exit_price = None

        if day_low <= initial_stop or cmp <= initial_stop:
            exit_triggered = True
            exit_price = initial_stop
            exit_reason = f"Initial Hard Stop Loss Hit @ ₹{initial_stop:,.2f}"
        elif cmp < trailing_stop:
            exit_triggered = True
            exit_price = cmp
            exit_reason = f"Trailing Chandelier Stop Hit @ ₹{trailing_stop:,.2f} (Stage {stage})"

        if exit_triggered:
            # ACTION REQUIRED: Send to Telegram AND Gmail!
            logger.warning(f"🚨 EXIT TRIGGERED for {ticker}: {exit_reason} (CMP: ₹{cmp:,.2f}, Stop: ₹{exit_price:,.2f})")
            notif.dispatch_exit_alert(
                ticker=ticker,
                exit_price=round(exit_price, 2),
                reason=exit_reason,
                pnl_pct=round(pnl_pct, 2),
                net_pnl_rs=round(net_pnl_rs, 2),
                holding_days=round(holding_days, 1),
                current_qty=qty
            )
            exits_triggered.append({
                "ticker": ticker,
                "exit_price": exit_price,
                "reason": exit_reason,
                "net_pnl_rs": net_pnl_rs,
                "pnl_pct": pnl_pct
            })
        else:
            # Position is healthy -> format hourly status card in user's exact requested structure
            pnl_color = "🟢" if net_pnl_rs >= 0 else "🔴"
            pnl_sign = "+" if net_pnl_rs >= 0 else ""
            card = (
                f"📈 *{clean_ticker}*\n"
                f"• Entry Price: ₹{entry_price:,.2f}\n"
                f"• Current Price: ₹{cmp:,.2f}\n"
                f"• Estimated Net PnL: {pnl_color} {pnl_sign}{pnl_pct:.2f}% ({pnl_sign}₹{net_pnl_rs:,.2f})\n"
                f"• Holding Duration: {holding_days:.1f} days\n"
                f"• Open Shares: {qty}\n"
                f"• Trailing Stop: ₹{trailing_stop:,.2f} (Stage {stage})"
            )
            trade_cards.append(card)

    # Dispatch hourly status message to TELEGRAM ONLY (Zero email!)
    if trade_cards:
        cards_text = "\n\n".join(trade_cards)
        full_msg = (
            f"🔔 *HOURLY TRADE STATUS UPDATE* ({checkpoint_title})\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{cards_text}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💼 *Cash Available:* ₹{summary['cash_available']:,.2f} ({summary['cash_pct']:.1f}%)\n"
            f"🎯 *Slots Available:* {summary['effective_slots_available']:.1f} / 3.0 Open"
        )
        notif.send_telegram_message(full_msg)

    return exits_triggered


def scan_for_swing_entries(summary: Dict):
    """
    Scans for high-conviction Swing Golden Cross breakout setups.
    Only fires if:
    1. Effective slots are available (>= 1.0) and sufficient cash in hand.
    2. Nifty 50 Macro Regime is Bullish (close > 50 EMA).
    If candidate found: Dispatches Actionable Buy Alert to TELEGRAM AND GMAIL!
    """
    if not summary['can_take_new_trade']:
        logger.info(f"Capacity full ({summary['effective_slots_occupied']:.1f}/3.0 slots) or cash low. Skipping entry scan.")
        return

    # Check Nifty 50 Macro Regime
    df_nifty = load_candles_from_db("NIFTY", timeframe="4H")
    if df_nifty.empty or len(df_nifty) < 50:
        regime_bullish = True
    else:
        df_nifty = df_nifty.sort_values(by="timestamp").reset_index(drop=True)
        df_nifty['EMA50'] = df_nifty['close'].ewm(span=50, adjust=False).mean()
        latest_nifty = df_nifty.iloc[-1]
        regime_bullish = bool(latest_nifty['close'] > latest_nifty['EMA50'])
        logger.info(f"Nifty 50 Regime: {'🟢 BULLISH' if regime_bullish else '🔴 BEARISH'} (Close: ₹{latest_nifty['close']:,.1f} vs 50 EMA: ₹{latest_nifty['EMA50']:,.1f})")

    if not regime_bullish:
        logger.info("Nifty 50 is Bearish. New swing entries halted to preserve capital.")
        return

    # Scan Universe for Swing Golden Cross Breakouts
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT ticker FROM market_data WHERE timeframe='4H' AND ticker != 'NIFTY' AND ticker != 'NIFTY.NS'")
        universe = [r[0] for r in cur.fetchall()]

    if not universe:
        logger.info("Universe empty in local DB. Skipping scanner.")
        return

    candidates = []
    active_syms = {p['ticker'] for p in pm.get_active_trades()}

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

    candidates.sort(key=lambda x: x['alpha_score'], reverse=True)
    high_alpha_options = [c for c in candidates if c['alpha_score'] >= 110.0]
    display_candidates = high_alpha_options if high_alpha_options else candidates[:3]

    available_slots = int(summary['effective_slots_available'])

    if display_candidates and available_slots > 0:
        # Introductory Telegram dispatch
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

        # ACTION REQUIRED: Dispatches Buy Alert to Telegram AND Gmail!
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


def execute_hourly_checkpoint(checkpoint_title: str):
    """
    Executes a complete hourly checkpoint cycle:
    1. Evaluates active positions, updates stops, checks stop-loss breaches.
    2. Sends live status of all open trades to Telegram in exact requested format.
       (Zero email sent unless a stop loss was hit!).
    3. If slots are open, scans for high-alpha entries.
       (Sends email ONLY if a buy signal is generated!).
    4. At 5:30 PM (Checkpoint 7), exports closed trades CSV and sends to Telegram.
    """
    now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    logger.info(f"=== Starting {checkpoint_title} at {now_str} ===")

    # Step 1: Active holdings evaluation and Telegram hourly status
    check_active_positions_and_report_status(checkpoint_title)

    # Step 2: Incremental DB sync & Entry scanning (during active market hours)
    try:
        update_database_incremental(timeframe="4H")
    except Exception as e:
        logger.warning(f"Incremental DB update skipped/failed: {e}")

    summary = pm.get_portfolio_summary()
    scan_for_swing_entries(summary)

    # Step 3: End of day 5:30 PM settlement & CSV attachment
    if "5:30 PM" in checkpoint_title or "17:30" in checkpoint_title:
        df_closed = pm.get_closed_trades_df()
        csv_path = "trades_ledger_export.csv"
        df_closed.to_csv(csv_path, index=False)
        notif.send_telegram_document(csv_path, caption="📁 End-of-Day Closed Trades Ledger (CSV)")


def run_market_scheduler():
    """
    Main loop that triggers the hourly checkpoints during trading days:
    - 10:15 AM IST (Checkpoint 1 - 1h after open)
    - 11:15 AM IST (Checkpoint 2 - 2h after open)
    - 12:15 PM IST (Checkpoint 3 - 3h after open)
    - 01:15 PM IST (Checkpoint 4 - 4H bar sync)
    - 02:15 PM IST (Checkpoint 5 - 5h after open)
    - 03:15 PM IST (Checkpoint 6 - Pre-close 15 mins before 3:30 close)
    - 05:30 PM IST (Checkpoint 7 - Post-close 5:30 PM wrap-up & CSV ledger)
    """
    logger.info("Live Market Service running in background. Waiting for hourly checkpoints...")

    checkpoints_fired_today = set()
    current_date = datetime.now(IST).date()

    SCHEDULE = [
        ("CP1", dtime(10, 15), dtime(10, 25), "10:15 AM IST (1h Post-Open Checkpoint)"),
        ("CP2", dtime(11, 15), dtime(11, 25), "11:15 AM IST (Mid-Morning Checkpoint)"),
        ("CP3", dtime(12, 15), dtime(12, 25), "12:15 PM IST (Noon Checkpoint)"),
        ("CP4", dtime(13, 15), dtime(13, 25), "01:15 PM IST (4H Bar Sync Checkpoint)"),
        ("CP5", dtime(14, 15), dtime(14, 25), "02:15 PM IST (Mid-Afternoon Checkpoint)"),
        ("CP6", dtime(15, 15), dtime(15, 28), "03:15 PM IST (Pre-Close Final Call Checkpoint)"),
        ("CP7", dtime(17, 30), dtime(17, 45), "05:30 PM IST (Post-Market Close Settlement)")
    ]

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

            for cp_id, start_t, end_t, label in SCHEDULE:
                if start_t <= curr_time <= end_t and cp_id not in checkpoints_fired_today:
                    execute_hourly_checkpoint(label)
                    checkpoints_fired_today.add(cp_id)

        time.sleep(30)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sentinel-Swing Live Market Service")
    parser.add_argument("--now", action="store_true", help="Execute a full checkpoint scan immediately")
    parser.add_argument("--status", action="store_true", help="Dispatch hourly status report to Telegram immediately")
    args = parser.parse_args()

    if args.status:
        now_title = datetime.now(IST).strftime("%I:%M %p IST")
        check_active_positions_and_report_status(f"Live Check @ {now_title}")
    elif args.now:
        now_title = datetime.now(IST).strftime("%I:%M %p IST")
        execute_hourly_checkpoint(f"Manual Checkpoint @ {now_title}")
    else:
        run_market_scheduler()
