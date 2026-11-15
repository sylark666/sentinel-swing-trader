"""
Supertrend Indicator Engine for Renko Bricks and Standard Candles
Calculates classic Olivier Seban / TradingView Supertrend with Wilder's Smoothing.
"""

import numpy as np
import pandas as pd


def calculate_supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 2.0
) -> pd.DataFrame:
    """
    Computes Supertrend on a DataFrame containing ['high', 'low', 'close'].
    Returns a copy of DataFrame with columns:
    - 'atr': Average True Range
    - 'supertrend': The Supertrend line value
    - 'trend': 1 (Bullish/Green) or -1 (Bearish/Red)
    - 'signal': 1 (Buy flip), -1 (Sell flip), 0 (No change)
    """
    if df.empty or len(df) < period:
        out = df.copy()
        out["supertrend"] = np.nan
        out["trend"] = 0
        out["signal"] = 0
        return out

    df = df.copy()
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    n = len(df)

    # 1. True Range
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        )

    # 2. ATR using Wilder's RMA (alpha = 1 / period)
    atr = np.zeros(n)
    atr[period - 1] = np.mean(tr[:period])
    alpha = 1.0 / period
    for i in range(period, n):
        atr[i] = (tr[i] * alpha) + (atr[i - 1] * (1.0 - alpha))

    # Fill initial periods
    for i in range(period - 1):
        atr[i] = tr[i]

    # 3. Basic Bands
    hl2 = (high + low) / 2.0
    basic_upper = hl2 + (multiplier * atr)
    basic_lower = hl2 - (multiplier * atr)

    # 4. Final Bands & Supertrend line
    final_upper = np.zeros(n)
    final_lower = np.zeros(n)
    trend = np.zeros(n, dtype=int)
    supertrend = np.zeros(n)

    # Initial anchor
    final_upper[0] = basic_upper[0]
    final_lower[0] = basic_lower[0]
    trend[0] = 1 if close[0] >= basic_lower[0] else -1
    supertrend[0] = final_lower[0] if trend[0] == 1 else final_upper[0]

    for i in range(1, n):
        # Final Upper Band
        if (basic_upper[i] < final_upper[i - 1]) or (close[i - 1] > final_upper[i - 1]):
            final_upper[i] = basic_upper[i]
        else:
            final_upper[i] = final_upper[i - 1]

        # Final Lower Band
        if (basic_lower[i] > final_lower[i - 1]) or (close[i - 1] < final_lower[i - 1]):
            final_lower[i] = basic_lower[i]
        else:
            final_lower[i] = final_lower[i - 1]

        # Trend Flip Logic
        prev_trend = trend[i - 1]
        if prev_trend == 1:
            if close[i] < final_lower[i]:
                trend[i] = -1
                supertrend[i] = final_upper[i]
            else:
                trend[i] = 1
                supertrend[i] = final_lower[i]
        else:
            if close[i] > final_upper[i]:
                trend[i] = 1
                supertrend[i] = final_lower[i]
            else:
                trend[i] = -1
                supertrend[i] = final_upper[i]

    # 5. Signal Flips (1 for Buy flip, -1 for Sell flip)
    signal = np.zeros(n, dtype=int)
    for i in range(1, n):
        if trend[i] == 1 and trend[i - 1] == -1:
            signal[i] = 1
        elif trend[i] == -1 and trend[i - 1] == 1:
            signal[i] = -1

    df["atr"] = atr
    df["supertrend"] = supertrend
    df["trend"] = trend
    df["signal"] = signal
    return df
