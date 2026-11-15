"""
sync_tradingview_nifty500.py
Bulk syncs Nifty 500 candlestick data from TradingView into the local SQLite database.
Features a live progress bar, auto-resuming, rate-limiting, and error tolerance.
"""

import os
import sys
import time
import argparse
import logging
from typing import List
import pandas as pd
from tqdm import tqdm

from tradingview_fetcher import fetch_tv_data
from database import init_db, save_candles_batch, get_connection, get_db_stats

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
        logging.FileHandler("sync_tradingview.log", mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("TVSync")

NIFTY500_CSV = "nifty500_symbols.csv"

def get_nifty500_symbols() -> List[str]:
    """Retrieves the list of Nifty 500 symbols from local CSV or downloads if missing."""
    if not os.path.exists(NIFTY500_CSV):
        logger.info("Downloading official Nifty 500 symbol list from NSE India...")
        import requests
        import io
        headers = {"User-Agent": "Mozilla/5.0"}
        url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
        res = requests.get(url, headers=headers, timeout=15)
        df = pd.read_csv(io.StringIO(res.text))
        df.to_csv(NIFTY500_CSV, index=False)
    
    df = pd.read_csv(NIFTY500_CSV)
    symbols = df['Symbol'].dropna().unique().tolist()
    # Add NIFTY index itself for macro regime filtering
    if "NIFTY" not in symbols:
        symbols.insert(0, "NIFTY")
    return symbols

def get_already_synced_symbols(timeframe: str) -> set:
    """Checks the database for symbols that already have data for this timeframe."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT ticker FROM market_data WHERE timeframe = ?", (timeframe.upper(),))
        rows = cursor.fetchall()
        # Strip .NS to match raw symbols
        return {r[0].replace(".NS", "") for r in rows}

def sync_nifty500(
    timeframe: str = "1D", 
    bars: int = 500, 
    limit: int = None, 
    skip_existing: bool = True,
    delay: float = 0.35
):
    init_db()
    all_symbols = get_nifty500_symbols()
    
    if limit:
        all_symbols = all_symbols[:limit]

    already_synced = get_already_synced_symbols(timeframe) if skip_existing else set()
    to_process = [s for s in all_symbols if s not in already_synced]

    print("\n" + "="*70)
    print("SENTINEL-SWING | TRADINGVIEW NIFTY 500 DATA SYNC")
    print(f"Target Timeframe  : {timeframe.upper()}")
    print(f"Candles per Stock : {bars} bars")
    print(f"Total Universe    : {len(all_symbols)} stocks")
    print(f"Already Cached    : {len(already_synced)} stocks (Skipped)")
    print(f"Queue to Sync     : {len(to_process)} stocks")
    print("="*70 + "\n")

    if not to_process:
        print("[DONE] All target symbols are already up-to-date in the local database!")
        stats = get_db_stats()
        print(f"Database Stats: {stats['total_candles']:,} candles across {stats['unique_tickers']} tickers.\n")
        return

    success_count = 0
    fail_count = 0
    total_candles_added = 0

    # Initialize tqdm progress bar
    pbar = tqdm(to_process, desc=f"Syncing [{timeframe.upper()}]", unit="stock", ncols=90)

    for symbol in pbar:
        pbar.set_postfix_str(f"Fetching {symbol}...")
        try:
            # Fetch data from TradingView
            df = fetch_tv_data(symbol=symbol, timeframe=timeframe, exchange="NSE", n_bars=bars, use_cache=True)
            
            if df is not None and not df.empty:
                saved = save_candles_batch(df)
                success_count += 1
                total_candles_added += saved
                pbar.set_postfix_str(f"[OK] {symbol} (+{saved})")
            else:
                fail_count += 1
                pbar.set_postfix_str(f"[WARN] {symbol}")
                logger.warning(f"Failed to fetch data for symbol: {symbol}")

        except Exception as e:
            fail_count += 1
            logger.error(f"Error syncing {symbol}: {e}")
            pbar.set_postfix_str(f"[ERR] {symbol}")

        time.sleep(delay)

    print("\n" + "="*70)
    print("TRADINGVIEW SYNC RUN COMPLETED")
    print(f"Successfully Synced : {success_count} stocks")
    print(f"Failed / Unavailable: {fail_count} stocks")
    print(f"Candles Added to DB : {total_candles_added:,}")
    
    db_stats = get_db_stats()
    print(f"Current DB Total    : {db_stats['total_candles']:,} candles across {db_stats['unique_tickers']} tickers.")
    print("="*70 + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync Nifty 500 stocks from TradingView to local SQLite DB.")
    parser.add_argument("--timeframe", "-t", type=str, default="1D", help="Timeframe (e.g. 1D, 4H, 1H, 15M)")
    parser.add_argument("--bars", "-b", type=int, default=500, help="Number of historical candles per stock (max 5000)")
    parser.add_argument("--limit", "-l", type=int, default=None, help="Limit number of stocks to sync for quick tests")
    parser.add_argument("--force", action="store_true", help="Force re-sync even if symbol already exists in DB")
    parser.add_argument("--delay", type=float, default=0.35, help="Polite delay between requests in seconds")

    args = parser.parse_args()
    sync_nifty500(
        timeframe=args.timeframe,
        bars=args.bars,
        limit=args.limit,
        skip_existing=not args.force,
        delay=args.delay
    )
