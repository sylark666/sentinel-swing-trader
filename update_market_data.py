"""
update_market_data.py
High-speed incremental database updater for Nifty 500 stocks via TradingView API.
Checks the latest available bar timestamp in SQLite for each stock against current market
hours, requests only the missing delta candles, and updates the database with zero duplication.
"""

import os
import sys
import time
import argparse
import logging
from datetime import datetime, time as dtime, timedelta
from typing import List, Dict, Optional, Callable
import importlib
import pandas as pd
from tqdm import tqdm

import database
if not hasattr(database, "get_latest_timestamps"):
    try:
        database = importlib.reload(database)
    except Exception:
        pass

from tradingview_fetcher import fetch_tv_data
from database import init_db, save_candles_batch, get_db_stats, get_connection

# Resilient fallback if running inside a pre-loaded daemon / Streamlit session
if hasattr(database, "get_latest_timestamps"):
    get_latest_timestamps = database.get_latest_timestamps
else:
    def get_latest_timestamps(timeframe: str, db_path: str = "market_data.db") -> Dict[str, str]:
        import sqlite3
        with sqlite3.connect(db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT ticker, MAX(timestamp) FROM market_data WHERE timeframe = ? GROUP BY ticker",
                (timeframe.upper(),)
            )
            return dict(cursor.fetchall())

from sync_tradingview_nifty500 import get_nifty500_symbols

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("update_market_data.log", mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("DBUpdater")

def get_latest_expected_session(now: Optional[datetime] = None) -> datetime.date:
    """
    Returns the most recent trading date on NSE that has completed or is in progress.
    Markets open Mon-Fri (weekdays 0-4) 09:15 to 15:30 IST.
    """
    if now is None:
        now = datetime.now()

    curr_date = now.date()
    weekday = now.weekday()

    if weekday == 5:  # Saturday -> Friday
        return curr_date - timedelta(days=1)
    elif weekday == 6:  # Sunday -> Friday
        return curr_date - timedelta(days=2)
    elif weekday == 0 and now.time() < dtime(9, 15):  # Monday morning before 9:15 -> Friday
        return curr_date - timedelta(days=3)
    elif now.time() < dtime(9, 15):  # Tue-Fri morning before 9:15 -> previous day
        return curr_date - timedelta(days=1)
    else:
        return curr_date

def is_stock_up_to_date(
    last_ts_str: Optional[str], 
    timeframe: str, 
    expected_session: datetime.date,
    now: Optional[datetime] = None
) -> bool:
    """
    Determines if the stock's latest bar in DB is already current with the market.
    """
    if not last_ts_str:
        return False

    if now is None:
        now = datetime.now()

    last_dt = pd.to_datetime(last_ts_str)
    last_date = last_dt.date()
    tf_upper = timeframe.upper()

    # If the bar date is before the expected trading session date, it's outdated
    if last_date < expected_session:
        return False

    # If bar date is the expected session date, check timeframe specifics
    if last_date == expected_session:
        # If market is still open, we might need intraday updates, but after 15:30 session is complete
        is_after_close = (now.date() > expected_session) or (now.date() == expected_session and now.time() >= dtime(15, 30))
        
        if tf_upper == "1D":
            return True
        elif tf_upper == "4H":
            # 4H candles on NSE: 09:15:00 and 13:15:00
            if is_after_close:
                return last_dt.hour >= 13
            elif now.time() >= dtime(13, 15):
                return last_dt.hour >= 13
            else:
                return last_dt.hour >= 9
        elif tf_upper == "1H":
            if is_after_close:
                return last_dt.hour >= 15
            else:
                return last_dt.hour >= (now.hour - 1)
        elif tf_upper == "15M":
            if is_after_close:
                return last_dt.hour == 15 and last_dt.minute >= 15
            else:
                return (now - last_dt).total_seconds() < 1800  # within 30 mins
        else:
            return True

    return True

def compute_required_bars(last_ts_str: Optional[str], timeframe: str, expected_session: datetime.date) -> int:
    """Computes minimal number of bars to fetch from TradingView to bridge the gap."""
    if not last_ts_str:
        return 5000  # Brand new ticker -> full historical backfill

    last_dt = pd.to_datetime(last_ts_str)
    days_gap = max(1, (expected_session - last_dt.date()).days)
    tf_upper = timeframe.upper()

    if tf_upper == "1D":
        return min(5000, max(5, days_gap + 3))
    elif tf_upper == "4H":
        return min(5000, max(10, days_gap * 2 + 6))
    elif tf_upper == "2H":
        return min(5000, max(15, days_gap * 4 + 8))
    elif tf_upper == "1H":
        return min(5000, max(20, days_gap * 7 + 12))
    elif tf_upper == "30M":
        return min(5000, max(30, days_gap * 13 + 15))
    elif tf_upper == "15M":
        return min(5000, max(50, days_gap * 25 + 30))
    else:
        return min(5000, max(10, days_gap * 3 + 5))

def update_database_incremental(
    timeframe: str = "4H",
    symbols: Optional[List[str]] = None,
    force: bool = False,
    limit: Optional[int] = None,
    delay: float = 0.35,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> Dict:
    """
    Performs an intelligent incremental sync for the given timeframe.
    """
    init_db()
    tf_upper = timeframe.upper()

    if symbols is None:
        all_symbols = get_nifty500_symbols()
    else:
        all_symbols = [s.replace(".NS", "") for s in symbols]

    if limit:
        all_symbols = all_symbols[:limit]

    now = datetime.now()
    expected_session = get_latest_expected_session(now)

    # 1. Fetch latest timestamp for all tickers in DB in a single bulk query (~0.5s)
    db_timestamps = get_latest_timestamps(tf_upper)

    # 2. Categorize stocks into UP_TO_DATE vs NEEDS_UPDATE
    to_update = []
    already_current = []

    for sym in all_symbols:
        full_ticker = f"{sym}.NS"
        last_ts = db_timestamps.get(full_ticker)
        
        if not force and is_stock_up_to_date(last_ts, tf_upper, expected_session, now):
            already_current.append(sym)
        else:
            bars_needed = compute_required_bars(last_ts, tf_upper, expected_session)
            to_update.append((sym, last_ts, bars_needed))

    total_universe = len(all_symbols)
    print("\n" + "="*75)
    print(f"SENTINEL-SWING | INCREMENTAL DB UPDATE [{tf_upper}]")
    print("="*75)
    print(f"Current Date/Time      : {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Latest Market Session  : {expected_session.strftime('%Y-%m-%d')}")
    print(f"Total Universe Size    : {total_universe} stocks")
    print(f"Already Up-to-Date     : {len(already_current)} stocks (Skipped - No API call needed)")
    print(f"Stocks Requiring Update: {len(to_update)} stocks")
    print("="*75 + "\n")

    if not to_update:
        print(f"[SUCCESS] All {total_universe} stocks are 100% up-to-date with latest session ({expected_session})!")
        if progress_callback:
            progress_callback(1.0, "All stocks already up-to-date!")
        stats = get_db_stats()
        return {
            "timeframe": tf_upper,
            "total_stocks": total_universe,
            "already_current": len(already_current),
            "updated": 0,
            "failed": 0,
            "candles_added": 0,
            "total_db_candles": stats["total_candles"]
        }

    updated_count = 0
    failed_count = 0
    total_new_candles = 0
    failed_items = []
    start_time = time.time()

    pbar = tqdm(to_update, desc=f"Updating [{tf_upper}]", unit="stock", ncols=95)

    for i, (sym, last_ts, bars_needed) in enumerate(pbar):
        clean_sym = sym.upper().replace(".NS", "")
        pbar.set_postfix_str(f"Fetching {clean_sym} ({bars_needed}b)...")

        if progress_callback:
            pct = (i + 1) / len(to_update)
            progress_callback(pct, f"Updating {clean_sym} ({i+1}/{len(to_update)})...")

        try:
            # Fetch minimal needed bars from TradingView with automatic per-request retry
            df = fetch_tv_data(clean_sym, timeframe=tf_upper, exchange="NSE", n_bars=bars_needed, use_cache=False)

            if df is not None and not df.empty:
                df['dt'] = pd.to_datetime(df['timestamp'])
                
                # Check how many bars are strictly newer than the DB's previous timestamp
                if last_ts:
                    last_dt = pd.to_datetime(last_ts)
                    new_bars = df[df['dt'] > last_dt]
                    # Also include the boundary bar (>= last_dt) to refresh open/high/low/close of in-progress candle
                    save_slice = df[df['dt'] >= last_dt]
                else:
                    new_bars = df
                    save_slice = df

                num_new = len(new_bars)
                if not save_slice.empty:
                    save_candles_batch(save_slice)

                total_new_candles += num_new
                updated_count += 1
                latest_fetched = df['dt'].max().strftime('%Y-%m-%d %H:%M')
                pbar.set_postfix_str(f"[OK] {clean_sym} (+{num_new}b -> {latest_fetched})")
            else:
                failed_count += 1
                failed_items.append((sym, last_ts, bars_needed))
                logger.warning(f"No data returned from TradingView for {clean_sym}")
                pbar.set_postfix_str(f"[WARN] {clean_sym} (queued for retry)")

        except Exception as e:
            failed_count += 1
            failed_items.append((sym, last_ts, bars_needed))
            logger.error(f"Error updating {clean_sym}: {e}")
            pbar.set_postfix_str(f"[ERR] {clean_sym}")

        time.sleep(delay)

    # -------------------------------------------------------------
    # TIER 2: AUTOMATIC RETRY RECOVERY SWEEP FOR RATE-LIMITED STOCKS
    # -------------------------------------------------------------
    if failed_items:
        print(f"\n🔄 Running Tier-2 Recovery Sweep on {len(failed_items)} failed/rate-limited stocks in 3 seconds...")
        time.sleep(3.0)
        recovery_pbar = tqdm(failed_items, desc=f"Retrying [{tf_upper}]", unit="stock", ncols=95)
        for sym, last_ts, bars_needed in recovery_pbar:
            clean_sym = sym.upper().replace(".NS", "")
            recovery_pbar.set_postfix_str(f"Retrying {clean_sym}...")
            try:
                # Use max_retries=3 with generous backoff
                df = fetch_tv_data(clean_sym, timeframe=tf_upper, exchange="NSE", n_bars=bars_needed, use_cache=False, max_retries=3, retry_delay=1.5)
                if df is not None and not df.empty:
                    df['dt'] = pd.to_datetime(df['timestamp'])
                    if last_ts:
                        last_dt = pd.to_datetime(last_ts)
                        new_bars = df[df['dt'] > last_dt]
                        save_slice = df[df['dt'] >= last_dt]
                    else:
                        new_bars = df
                        save_slice = df
                    num_new = len(new_bars)
                    if not save_slice.empty:
                        save_candles_batch(save_slice)
                    total_new_candles += num_new
                    updated_count += 1
                    failed_count -= 1
                    latest_fetched = df['dt'].max().strftime('%Y-%m-%d %H:%M')
                    recovery_pbar.set_postfix_str(f"[RECOVERED] {clean_sym} (+{num_new}b -> {latest_fetched})")
                else:
                    recovery_pbar.set_postfix_str(f"[EXHAUSTED] {clean_sym}")
            except Exception as e:
                logger.error(f"Retry failed for {clean_sym}: {e}")
                recovery_pbar.set_postfix_str(f"[ERR] {clean_sym}")
            time.sleep(delay * 1.5)

    elapsed = time.time() - start_time
    stats = get_db_stats()

    print("\n" + "="*75)
    print(f"INCREMENTAL UPDATE SUMMARY [{tf_upper}]")
    print("="*75)
    print(f"Time Taken            : {elapsed:.1f}s ({elapsed/60:.2f} mins)")
    print(f"Stocks Already Current: {len(already_current)}")
    print(f"Stocks Updated        : {updated_count}")
    print(f"Failed / Unavailable  : {failed_count}")
    print(f"New Candles Added     : {total_new_candles:,}")
    print(f"Current DB Total      : {stats['total_candles']:,} candles across {stats['unique_tickers']} tickers.")
    print("="*75 + "\n")

    return {
        "timeframe": tf_upper,
        "total_stocks": total_universe,
        "already_current": len(already_current),
        "updated": updated_count,
        "failed": failed_count,
        "candles_added": total_new_candles,
        "elapsed_seconds": elapsed,
        "total_db_candles": stats["total_candles"]
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Incrementally update Nifty 500 SQLite DB from TradingView.")
    parser.add_argument("--timeframe", "-t", type=str, default="4H", help="Timeframe (e.g. 4H, 1D, 1H, 15M, or 'all')")
    parser.add_argument("--limit", "-l", type=int, default=None, help="Limit number of stocks for quick test")
    parser.add_argument("--force", "-f", action="store_true", help="Force check all stocks even if timestamp matches latest session")
    parser.add_argument("--delay", "-d", type=float, default=0.35, help="Polite delay between requests in seconds")
    parser.add_argument("--symbols", "-s", type=str, default=None, help="Comma-separated symbols to update (e.g. RELIANCE,TCS,INFY)")

    args = parser.parse_args()

    sym_list = [s.strip() for s in args.symbols.split(",")] if args.symbols else None

    if args.timeframe.lower() == "all":
        target_tfs = ["4H", "1D"]
    else:
        target_tfs = [t.strip().upper() for t in args.timeframe.split(",")]

    for tf in target_tfs:
        update_database_incremental(
            timeframe=tf,
            symbols=sym_list,
            force=args.force,
            limit=args.limit,
            delay=args.delay
        )

