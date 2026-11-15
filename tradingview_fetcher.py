import os
import time
import logging
import pandas as pd
from dotenv import load_dotenv
from tvDatafeed import TvDatafeed, Interval

# Load environment variables if available
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("TradingViewFetcher")

CACHE_DIR = "market_data_cache"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

# Interval mapping dictionary
INTERVAL_MAP = {
    "1M": Interval.in_1_minute,
    "3M": Interval.in_3_minute,
    "5M": Interval.in_5_minute,
    "15M": Interval.in_15_minute,
    "30M": Interval.in_30_minute,
    "45M": Interval.in_45_minute,
    "1H": Interval.in_1_hour,
    "2H": Interval.in_2_hour,
    "3H": Interval.in_3_hour,
    "4H": Interval.in_4_hour,
    "1D": Interval.in_daily,
    "1WK": Interval.in_weekly,
    "1MTH": Interval.in_monthly
}

_tv_instance = None

def get_tv_client() -> TvDatafeed:
    """
    Initializes a singleton TvDatafeed client.
    Uses credentials from .env if provided, otherwise uses guest mode.
    """
    global _tv_instance
    if _tv_instance is not None:
        return _tv_instance

    username = os.getenv("TRADINGVIEW_USERNAME", "").strip()
    password = os.getenv("TRADINGVIEW_PASSWORD", "").strip()

    if username and password:
        logger.info(f"Connecting to TradingView with account: {username}")
        try:
            _tv_instance = TvDatafeed(username=username, password=password)
            return _tv_instance
        except Exception as e:
            logger.warning(f"Login failed ({e}). Falling back to anonymous guest mode...")
    
    logger.info("Connecting to TradingView in Anonymous Guest Mode (No credentials required).")
    _tv_instance = TvDatafeed()
    return _tv_instance

def fetch_tv_data(
    symbol: str, 
    timeframe: str = "1D", 
    exchange: str = "NSE", 
    n_bars: int = 1000, 
    use_cache: bool = True,
    max_retries: int = 3,
    retry_delay: float = 1.0
) -> pd.DataFrame:
    """
    Fetch historical data from TradingView and format to match project schema.
    Features automatic retry with exponential backoff for rate-limits and timeouts.
    
    Args:
        symbol: e.g. "RELIANCE" or "RELIANCE.NS" or "NIFTY"
        timeframe: "15M", "30M", "1H", "2H", "4H", "1D", "1WK"
        exchange: "NSE" or "BSE" (default: "NSE")
        n_bars: Number of bars (max 5000 on free accounts)
        use_cache: Whether to use local CSV cache
        max_retries: Number of retry attempts on rate limit or network error
        retry_delay: Initial backoff delay in seconds
    """
    # Clean ticker name (strip .NS suffix and map special characters to TradingView convention)
    clean_symbol = symbol.upper().replace(".NS", "").replace("^", "").replace("&", "_").replace("-", "_")
    tf_upper = timeframe.upper()

    cache_file = os.path.join(CACHE_DIR, f"TV_{clean_symbol}_{exchange}_{tf_upper}_{n_bars}.csv")

    # Check 12-hour local cache
    if use_cache and os.path.exists(cache_file):
        mtime = os.path.getmtime(cache_file)
        if (time.time() - mtime) < 43200: # 12 hours
            logger.info(f"Loading {clean_symbol} ({tf_upper}) from local cache...")
            df = pd.read_csv(cache_file)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            return df

    tv_interval = INTERVAL_MAP.get(tf_upper, Interval.in_daily)
    global _tv_instance

    raw_df = None
    for attempt in range(1, max_retries + 1):
        try:
            tv = get_tv_client()
            raw_df = tv.get_hist(symbol=clean_symbol, exchange=exchange, interval=tv_interval, n_bars=n_bars)
            if raw_df is not None and not raw_df.empty:
                break
            else:
                logger.warning(f"TradingView returned empty data for {clean_symbol} (attempt {attempt}/{max_retries})")
        except Exception as e:
            logger.warning(f"TradingView fetch exception for {clean_symbol} (attempt {attempt}/{max_retries}): {e}")
            # Reset client instance to force fresh connection on next attempt
            _tv_instance = None

        if attempt < max_retries:
            backoff = retry_delay * (2 ** (attempt - 1))  # e.g., 1.0s, 2.0s, 4.0s
            logger.info(f"Rate-limit / timeout backoff for {clean_symbol}: sleeping {backoff:.1f}s before retry...")
            time.sleep(backoff)

    if raw_df is None or raw_df.empty:
        logger.error(f"Failed to fetch {clean_symbol} from TradingView after {max_retries} attempts.")
        return pd.DataFrame()

    # Polite rate-limit buffer
    time.sleep(0.25)

    # Format to project schema
    df = raw_df.reset_index()
    df = df.rename(columns={
        'datetime': 'timestamp',
        'open': 'open',
        'high': 'high',
        'low': 'low',
        'close': 'close',
        'volume': 'volume'
    })

    df['ticker'] = f"{clean_symbol}.NS"
    df['timeframe'] = tf_upper

    final_df = df[['ticker', 'timestamp', 'timeframe', 'open', 'high', 'low', 'close', 'volume']]

    # Save to cache
    final_df.to_csv(cache_file, index=False)
    logger.info(f"Cached {len(final_df)} bars for {clean_symbol} ({tf_upper})")

    return final_df

if __name__ == "__main__":
    # Test fetch
    print("Testing TradingView Fetcher for RELIANCE (1D and 1H)...")
    df_daily = fetch_tv_data("RELIANCE", "1D", n_bars=10)
    print("\nDaily Sample:")
    print(df_daily.tail(3))

    df_1h = fetch_tv_data("RELIANCE", "1H", n_bars=10)
    print("\n1-Hour Sample:")
    print(df_1h.tail(3))

