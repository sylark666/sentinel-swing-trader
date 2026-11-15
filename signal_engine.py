import numpy as np
import pandas as pd
import logging

logger = logging.getLogger("SignalEngine")

def calculate_indicators(df: pd.DataFrame, **kwargs) -> pd.DataFrame:
    """
    Computes indicators for Strategy 2: Chandelier Exit Confirmation
    Matches the exact logic of the provided Pine Script.
    """
    df = df.copy()
    
    # 1. Macro Filters
    df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['EMA200'] = df['close'].ewm(span=200, adjust=False).mean()
    df['Vol_SMA20'] = df['volume'].rolling(window=20).mean()
    df['Vol_Surge'] = (df['volume'] / df['Vol_SMA20'].replace(0, np.nan)).fillna(1.0)
    
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    
    # RSI (14)
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # Early Exit Triggers
    df['MACD_Cross_Down'] = (df['MACD'] < df['MACD_Signal']) & (df['MACD'].shift(1) >= df['MACD_Signal'].shift(1))
    df['RSI_Reversal'] = (df['RSI'] < 70) & (df['RSI'].shift(1) >= 70)
    
    # 2. Chandelier Exit (Pine Script Translation)
    # True Range
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    
    # Pine's ta.atr uses RMA (Exponential with alpha = 1/length)
    atr = true_range.ewm(alpha=1/22, adjust=False).mean()
    atr_mult = atr * 3.0
    
    highest22 = df['close'].rolling(window=22).max()
    lowest22 = df['close'].rolling(window=22).min()
    
    closes = df['close'].values
    highest = highest22.values
    lowest = lowest22.values
    atrs = atr_mult.values
    
    long_stop = np.zeros(len(df))
    short_stop = np.zeros(len(df))
    dir_arr = np.ones(len(df))
    
    for i in range(1, len(df)):
        ls = highest[i] - atrs[i]
        ss = lowest[i] + atrs[i]
        
        # nz(longStop[1], longStop)
        ls_prev = long_stop[i-1] if long_stop[i-1] != 0 else ls
        ss_prev = short_stop[i-1] if short_stop[i-1] != 0 else ss
        
        # longStop := close[1] > longStopPrev ? math.max(longStop, longStopPrev) : longStop
        if closes[i-1] > ls_prev:
            long_stop[i] = max(ls, ls_prev)
        else:
            long_stop[i] = ls
            
        # shortStop := close[1] < shortStopPrev ? math.min(shortStop, shortStopPrev) : shortStop
        if closes[i-1] < ss_prev:
            short_stop[i] = min(ss, ss_prev)
        else:
            short_stop[i] = ss
            
        dir_prev = dir_arr[i-1]
        
        # dir := close > shortStopPrev ? 1 : close < longStopPrev ? -1 : dir
        if closes[i] > ss_prev:
            dir_arr[i] = 1
        elif closes[i] < ls_prev:
            dir_arr[i] = -1
        else:
            dir_arr[i] = dir_prev
            
    df['LongStop'] = long_stop
    df['ShortStop'] = short_stop
    df['Dir'] = dir_arr
    df['Dir_Changed'] = (df['Dir'] == 1) & (df['Dir'].shift(1) == -1)
    df['ATR'] = atr
    
    return df
