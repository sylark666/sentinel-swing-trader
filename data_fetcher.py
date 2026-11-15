# v1.0.20
import yfinance as yf
import pandas as pd
import time
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("DataFetcher")

CACHE_DIR = "market_data_cache"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

def fetch_data(symbol: str, timeframe: str, period: str = "3y") -> pd.DataFrame:
    """
    Fetch historical data via yfinance (or local cache) and dynamically resample if necessary.
    Supports 15M, 1H, 2H, 4H, and 1D target timeframes.
    """
    tf_upper = timeframe.upper()
    
    # Define cache file path
    cache_file = os.path.join(CACHE_DIR, f"{symbol}_{tf_upper}_{period}.csv")
    
    # Check if a fresh cache exists (less than 12 hours old)
    if os.path.exists(cache_file):
        mtime = os.path.getmtime(cache_file)
        if (time.time() - mtime) < 43200:  # 12 hours
            logger.info(f"Loading {symbol} ({tf_upper}) from local cache...")
            df = pd.read_csv(cache_file)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            return df

    needs_resample = False
    resample_rule = None
    
    # yfinance does not natively support 2h or 4h. 
    # We fetch 1h data (max 730 days) and resample it mathematically.
    if tf_upper in ["2H", "4H"]:
        interval = "1h"
        needs_resample = True
        resample_rule = "2h" if tf_upper == "2H" else "4h"
    elif tf_upper == "1H":
        interval = "1h"
    elif tf_upper == "30M":
        interval = "30m"
        period = "60d" # yfinance limit
    elif tf_upper == "15M":
        interval = "15m"
        period = "60d" # yfinance limit
    elif tf_upper == "1WK":
        interval = "1wk"
    else:
        interval = "1d"
        
    logger.info(f"Fetching {interval} data for {symbol} from yfinance (Target: {tf_upper})")
    
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval)
    
    # Deterministic sleep to ensure API rate limit compliance
    time.sleep(0.35)
    
    if df.empty:
        logger.warning(f"No data returned for {symbol}")
        return pd.DataFrame()
        
    df.reset_index(inplace=True)
    
    # Normalize datetime columns to Indian Standard Time (IST)
    if 'Datetime' in df.columns:
        # Intraday data is provided in UTC, convert to IST and strip tz for clean merging
        df['timestamp'] = pd.to_datetime(df['Datetime'], utc=True).dt.tz_convert('Asia/Kolkata').dt.tz_localize(None)
    elif 'Date' in df.columns:
        # Daily data comes with weird NY midnights. Force it to exact Indian Market Close (15:30)
        df['timestamp'] = pd.to_datetime(df['Date']).dt.tz_localize(None).dt.normalize() + pd.Timedelta(hours=15, minutes=30)
        
    # Map to schema definitions
    rename_cols = {
        'Open': 'open',
        'High': 'high',
        'Low': 'low',
        'Close': 'close',
        'Volume': 'volume'
    }
    df = df.rename(columns=rename_cols)
    
    if needs_resample:
        # Convert index for mathematical resampling
        df.set_index('timestamp', inplace=True)
        # origin='start' ensures bars align to the opening print rather than 00:00 midnight UTC
        df = df.resample(resample_rule, origin='start').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
        df.reset_index(inplace=True)
        
    df['ticker'] = symbol
    df['timeframe'] = tf_upper
    
    # Select final columns 
    final_df = df[['ticker', 'timestamp', 'timeframe', 'open', 'high', 'low', 'close', 'volume']]
    
    # Save to cache
    final_df.to_csv(cache_file, index=False)
    
    return final_df

if __name__ == "__main__":
    # Test fetch functionality
    test_symbol = "RELIANCE.NS"
    logger.info(f"Running unit test fetch for {test_symbol}...")
    
    df_4h = fetch_data(test_symbol, "4H")
    print("\nIntraday (4H) Sample:")
    print(df_4h.tail(2))
