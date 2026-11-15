# v3.0.0 - 2.5% Swing System Engine
import numpy as np
import pandas as pd
import logging

logger = logging.getLogger("SignalEngine")

def _calc_rsi(df, window):
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def calculate_indicators(df: pd.DataFrame, **kwargs) -> pd.DataFrame:
    """
    Computes multi-timeframe approximated indicators on a 1H base chart.
    Assuming ~6 trading hours per day.
    """
    df = df.copy()
    
    # 1. Multi-Timeframe EMA Approximations
    df['EMA1200'] = df['close'].ewm(span=1200, adjust=False).mean() # Daily 200 EMA
    df['EMA33'] = df['close'].ewm(span=33, adjust=False).mean() # 4H 50 EMA (approx 8*4?) Wait. 4H = 4 hours. Daily=6h. 4H is basically just 4 bars.
    # Actually, a 4H chart has ~1.5 bars per day. 50 periods on 4H = 200 hours = EMA200 on 1H chart!
    # Wait, let's use exact hours: 
    # 4H EMA50 = 50 * 4 = 200 hours -> EMA200
    # 4H EMA20 = 20 * 4 = 80 hours -> EMA80
    df['EMA200'] = df['close'].ewm(span=200, adjust=False).mean() # 4H EMA50
    df['EMA80'] = df['close'].ewm(span=80, adjust=False).mean() # 4H EMA20
    
    # 2. Multi-Timeframe RSI Approximations
    # Daily RSI14 = 14 * 6 = 84 hours
    df['RSI84'] = _calc_rsi(df, 84)
    # 2H RSI14 = 14 * 2 = 28 hours
    df['RSI28'] = _calc_rsi(df, 28)
    
    # 3. ATR 30-Day (180 hours)
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    df['ATR180'] = true_range.rolling(window=180).mean()
    
    # 4. Daily Anchored VWAP
    df['Date'] = df['timestamp'].dt.date
    df['Typical_Price'] = (df['high'] + df['low'] + df['close']) / 3
    df['Vol_x_TP'] = df['volume'] * df['Typical_Price']
    
    df['Cum_Vol'] = df.groupby('Date')['volume'].cumsum()
    df['Cum_Vol_x_TP'] = df.groupby('Date')['Vol_x_TP'].cumsum()
    df['VWAP'] = df['Cum_Vol_x_TP'] / df['Cum_Vol']
    df['VWAP'] = df['VWAP'].fillna(df['close'])
    
    # 5. MACD (Standard 1H)
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']
    # MACD Cross logic (MACD > Signal and prev MACD <= prev Signal)
    df['MACD_Cross'] = (df['MACD'] > df['MACD_Signal']) & (df['MACD'].shift(1) <= df['MACD_Signal'].shift(1))
    
    # 6. Volume Average (20 hours)
    df['Vol20'] = df['volume'].rolling(window=20).mean()
    df['Vol_Surge'] = df['volume'] >= (1.5 * df['Vol20'])
    
    # 7. Candlestick Patterns
    body = np.abs(df['close'] - df['open'])
    lower_wick = np.minimum(df['open'], df['close']) - df['low']
    upper_wick = df['high'] - np.maximum(df['open'], df['close'])
    avg_body = body.rolling(10).mean()
    
    # Hammer
    df['Is_Hammer'] = (lower_wick > 2 * body) & (upper_wick < body) & (body > 0.1 * avg_body)
    
    # Bullish Engulfing
    prev_close = df['close'].shift(1)
    prev_open = df['open'].shift(1)
    is_prev_red = prev_close < prev_open
    is_curr_green = df['close'] > df['open']
    engulfs_body = (df['open'] <= prev_close) & (df['close'] >= prev_open)
    df['Is_Engulfing'] = is_prev_red & is_curr_green & engulfs_body
    
    # 8. Ichimoku Kumo Cloud
    high_9 = df['high'].rolling(window=9).max()
    low_9 = df['low'].rolling(window=9).min()
    df['Tenkan'] = (high_9 + low_9) / 2
    
    high_26 = df['high'].rolling(window=26).max()
    low_26 = df['low'].rolling(window=26).min()
    df['Kijun'] = (high_26 + low_26) / 2
    
    # Shifted 26 periods into the future means the *current* cloud was formed 26 periods ago
    df['Senkou_A'] = ((df['Tenkan'] + df['Kijun']) / 2).shift(26)
    
    high_52 = df['high'].rolling(window=52).max()
    low_52 = df['low'].rolling(window=52).min()
    df['Senkou_B'] = ((high_52 + low_52) / 2).shift(26)
    
    # Drop temp columns
    df.drop(columns=['Date', 'Typical_Price', 'Vol_x_TP', 'Cum_Vol', 'Cum_Vol_x_TP'], inplace=True)
    
    return df
