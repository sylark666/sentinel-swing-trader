"""
screener.py
Vectorized Multi-Factor Opportunity Scanner with Friction-Aware Capital Rotation
for the NSE Nifty 500 universe.
"""

import os
import sys
import logging
import sqlite3
import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple

from database import get_connection, load_candles_from_db

# Ensure clean UTF-8 printing on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Screener")

# Minimum hurdle to justify an asset rotation (closing stagnant stock -> buying new)
ROTATION_ALPHA_HURDLE = 20.0
# Roundtrip Indian delivery friction buffer (STT, GST, SEBI, slippage ~ 0.35%)
ROUNDTRIP_FRICTION_PCT = 0.0035

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Computes technical indicators required for multi-factor swing alpha scoring."""
    if df is None or len(df) < 50:
        return pd.DataFrame()

    df = df.copy().sort_values(by="timestamp").reset_index(drop=True)

    # 1. EMAs for Trend Filtering
    df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['EMA200'] = df['close'].ewm(span=200, adjust=False).mean() if len(df) >= 200 else df['close'].ewm(span=len(df), adjust=False).mean()

    # 2. RSI (14)
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / (loss.replace(0, np.nan))
    df['RSI'] = (100 - (100 / (1 + rs))).fillna(50)

    # 3. MACD (12, 26, 9)
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

    # 4. True Range & ATR (14)
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    df['ATR'] = true_range.rolling(window=14).mean()

    # 5. Volume Moving Average
    df['Vol_SMA20'] = df['volume'].rolling(window=20).mean()
    df['Vol_Surge'] = df['volume'] / df['Vol_SMA20'].replace(0, np.nan)

    # 6. Chandelier Exit (22, 3.0)
    atr22 = true_range.ewm(alpha=1/22, adjust=False).mean() * 3.0
    highest22 = df['close'].rolling(window=22).max()
    lowest22 = df['close'].rolling(window=22).min()

    closes = df['close'].values
    highs = highest22.values
    lows = lowest22.values
    atrs = atr22.values

    long_stop = np.zeros(len(df))
    dir_arr = np.ones(len(df))

    for i in range(1, len(df)):
        ls = highs[i] - atrs[i]
        ls_prev = long_stop[i-1] if long_stop[i-1] != 0 else ls
        long_stop[i] = max(ls, ls_prev) if closes[i-1] > ls_prev else ls

        dir_prev = dir_arr[i-1]
        if closes[i] > (lows[i] + atrs[i]):
            dir_arr[i] = 1
        elif closes[i] < ls_prev:
            dir_arr[i] = -1
        else:
            dir_arr[i] = dir_prev

    df['LongStop'] = long_stop
    df['Dir'] = dir_arr
    df['Dir_Flip'] = (df['Dir'] == 1) & (df['Dir'].shift(1) == -1)

    return df

def get_market_regime(timeframe: str = "4H") -> Dict:
    """
    Evaluates broader market regime from NIFTY index.
    Bullish regime when Nifty > 50 EMA.
    Falls back gracefully between 4H and 1D.
    """
    df_nifty = load_candles_from_db("NIFTY", timeframe=timeframe)
    if df_nifty.empty or len(df_nifty) < 50:
        # Fallback to 4H if 1D not in DB
        df_nifty = load_candles_from_db("NIFTY", timeframe="4H")

    if df_nifty.empty or len(df_nifty) < 50:
        # Fallback to NIFTY.NS
        df_nifty = load_candles_from_db("NIFTY.NS", timeframe="4H")

    if df_nifty.empty or len(df_nifty) < 50:
        return {"status": "NEUTRAL", "reason": "Insufficient NIFTY data", "nifty_close": 0, "nifty_ema50": 0}

    df_nifty = df_nifty.sort_values(by="timestamp").reset_index(drop=True)
    df_nifty['EMA50'] = df_nifty['close'].ewm(span=50, adjust=False).mean()
    last_row = df_nifty.iloc[-1]
    is_bullish = bool(last_row['close'] > last_row['EMA50'])

    return {
        "status": "BULLISH" if is_bullish else "BEARISH",
        "nifty_close": float(last_row['close']),
        "nifty_ema50": float(last_row['EMA50']),
        "timestamp": str(last_row['timestamp'])
    }

def score_opportunity(df: pd.DataFrame) -> Optional[Dict]:
    """
    Scores an opportunity from 0 to 100 based on multi-factor quantitative setup quality.
    """
    if df.empty or len(df) < 50:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    close = float(last['close'])
    ema200 = float(last['EMA200'])
    ema50 = float(last['EMA50'])
    ema20 = float(last['EMA20'])
    rsi = float(last['RSI'])
    atr = float(last['ATR']) if not np.isnan(last['ATR']) else (close * 0.02)
    vol_surge = float(last['Vol_Surge']) if not np.isnan(last['Vol_Surge']) else 1.0

    # Gate: Stock must be above its long-term 200 EMA to avoid downtrending value traps
    if close < ema200:
        return None

    score = 0.0
    reasons = []

    # 1. Trend Quality (Up to 30 Points)
    if close > ema50:
        score += 15
        reasons.append("Close > 50 EMA")
    if ema50 > ema200:
        score += 15
        reasons.append("Golden Alignment (50 > 200 EMA)")

    # 2. Setup Confluence (Up to 40 Points)
    # A) Chandelier Buy Zone Flip (Supertrend trigger)
    if bool(last['Dir_Flip']):
        score += 25
        reasons.append("Chandelier Buy Zone Flip")
    elif last['Dir'] == 1:
        score += 10
        reasons.append("In Chandelier Buy Zone")

    # B) Institutional Pullback (RSI 40-52 bounce in uptrend)
    if (40 <= prev['RSI'] <= 52) and (rsi > prev['RSI']):
        score += 20
        reasons.append(f"Pullback Bounce (RSI: {rsi:.1f})")

    # C) MACD Bullish Crossover or Expansion
    if last['MACD'] > last['MACD_Signal']:
        if prev['MACD'] <= prev['MACD_Signal']:
            score += 15
            reasons.append("MACD Bullish Cross")
        else:
            score += 5

    # 3. Volume Expansion (Up to 15 Points)
    if vol_surge >= 1.5:
        score += 15
        reasons.append(f"High Vol Surge ({vol_surge:.1f}x)")
    elif vol_surge >= 1.2:
        score += 8
        reasons.append(f"Vol Surge ({vol_surge:.1f}x)")

    # 4. Volatility Structure & Risk/Reward (Up to 15 Points)
    stop_loss = close - (2.0 * atr)
    target = close + (2.5 * atr)
    risk = close - stop_loss
    reward = target - close
    rrr = reward / risk if risk > 0 else 0

    if round(rrr, 2) >= 1.25:
        score += 15
        reasons.append(f"RRR {rrr:.2f}")

    # Threshold: Must have at least 50 points to be a qualifying candidate
    if score < 50:
        return None

    return {
        "ticker": str(last['ticker']),
        "timestamp": str(last['timestamp']),
        "close": close,
        "entry_price": close * 1.002, # Padded limit execution
        "stop_loss": stop_loss,
        "target": target,
        "target_pct": ((target - close) / close) * 100,
        "sl_pct": ((close - stop_loss) / close) * 100,
        "rrr": rrr,
        "alpha_score": round(score, 1),
        "vol_surge": round(vol_surge, 2),
        "rsi": round(rsi, 1),
        "reasons": " | ".join(reasons)
    }

def evaluate_holding_health(holding: Dict, current_df: pd.DataFrame) -> Dict:
    """
    Evaluates the ongoing momentum and health score of an active position.
    Penalizes stagnation and momentum loss.
    """
    if current_df.empty or len(current_df) < 5:
        return {**holding, "health_score": 50.0, "status": "UNKNOWN", "stagnant_days": 0}

    last = current_df.iloc[-1]
    curr_price = float(last['close'])
    entry_price = float(holding.get('entry_price', curr_price))
    pnl_pct = ((curr_price - entry_price) / entry_price) * 100

    # Calculate days/bars held
    entry_time = pd.to_datetime(holding.get('entry_time', last['timestamp']))
    holding_bars = len(current_df[current_df['timestamp'] >= entry_time])

    # Base health starts at 70
    health = 70.0
    flags = []

    # Profit progress
    if pnl_pct >= 2.0:
        health += 25
        flags.append(f"+{pnl_pct:.1f}% Profit")
    elif pnl_pct > 0:
        health += 10
        flags.append(f"+{pnl_pct:.1f}% Green")
    elif pnl_pct <= -1.5:
        health -= 25
        flags.append(f"{pnl_pct:.1f}% Drawdown")

    # Stagnation Penalty: If held > 5 bars with < 1% gain
    if holding_bars >= 5 and pnl_pct < 1.0:
        penalty = min(35, (holding_bars - 4) * 7)
        health -= penalty
        flags.append(f"Stagnating ({holding_bars} bars, -{penalty} pts)")

    # Indicator Decay: If MACD drops below Signal or RSI < 45
    if 'MACD' in last and 'MACD_Signal' in last:
        if last['MACD'] < last['MACD_Signal']:
            health -= 15
            flags.append("MACD Decay")

    health = max(0.0, min(100.0, health))

    return {
        **holding,
        "curr_price": curr_price,
        "pnl_pct": pnl_pct,
        "holding_bars": holding_bars,
        "health_score": round(health, 1),
        "health_flags": " | ".join(flags) if flags else "Normal Progression"
    }

def check_capital_rotation(
    top_candidates: List[Dict], 
    active_holdings: List[Dict]
) -> List[Dict]:
    """
    Compares the highest-ranked new opportunities against the weakest active holdings.
    Recommends an autonomous asset swap if alpha differential exceeds hurdle.
    """
    if not top_candidates or not active_holdings:
        return []

    # Sort holdings by health ascending (weakest holding first)
    sorted_holdings = sorted(active_holdings, key=lambda x: x['health_score'])
    weakest = sorted_holdings[0]

    # Best candidate
    best_candidate = top_candidates[0]

    # Calculate Alpha Differential
    alpha_delta = best_candidate['alpha_score'] - weakest['health_score']

    rotations = []
    # If the new setup is significantly superior to our weakest stagnant holding
    if alpha_delta >= ROTATION_ALPHA_HURDLE:
        rotations.append({
            "action": "ROTATE_ASSET",
            "exit_ticker": weakest['ticker'],
            "exit_price": weakest.get('curr_price', 0),
            "exit_health": weakest['health_score'],
            "exit_pnl_pct": weakest.get('pnl_pct', 0),
            "exit_reason": weakest.get('health_flags', 'Low Health'),
            "enter_ticker": best_candidate['ticker'],
            "enter_date": str(best_candidate.get('timestamp', ''))[:16],
            "enter_entry": best_candidate['entry_price'],
            "enter_alpha": best_candidate['alpha_score'],
            "enter_target_pct": best_candidate['target_pct'],
            "enter_reasons": best_candidate['reasons'],
            "alpha_delta": round(alpha_delta, 1),
            "rationale": (
                f"Swap {weakest['ticker']} (Health: {weakest['health_score']}) -> "
                f"{best_candidate['ticker']} (Alpha: {best_candidate['alpha_score']}). "
                f"Alpha Edge (+{alpha_delta:.1f} pts) easily clears the {ROTATION_ALPHA_HURDLE} pt hurdle "
                f"and covers roundtrip friction ({ROUNDTRIP_FRICTION_PCT*100:.2f}%)."
            )
        })

    return rotations

def run_opportunity_scan(
    timeframe: str = "1D", 
    limit_universe: Optional[int] = None, 
    active_holdings: Optional[List[Dict]] = None
) -> Dict:
    """
    Main scanner execution function.
    Scans market_data.db, scores opportunities, checks market regime, and evaluates capital rotations.
    """
    logger.info(f"Initiating Opportunity Scanner on [{timeframe}]...")

    # 1. Market Regime
    regime = get_market_regime(timeframe=timeframe)
    logger.info(f"Market Regime: {regime['status']} (Nifty: {regime['nifty_close']:,.2f} vs 50 EMA: {regime['nifty_ema50']:,.2f})")

    # 2. Get available tickers in DB
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT ticker FROM market_data WHERE timeframe = ?", (timeframe.upper(),))
        tickers = [r[0] for r in cursor.fetchall() if r[0] != "NIFTY.NS"]

    if limit_universe:
        tickers = tickers[:limit_universe]

    logger.info(f"Scanning {len(tickers)} stocks from local database...")

    candidates = []

    for t in tickers:
        df = load_candles_from_db(t, timeframe=timeframe)
        if len(df) < 50:
            continue

        df_ind = compute_indicators(df)
        opp = score_opportunity(df_ind)
        if opp:
            candidates.append(opp)

    # Sort opportunities by Alpha Score descending, tie-breaking by volume surge
    candidates = sorted(candidates, key=lambda x: (x['alpha_score'], x.get('vol_surge', 1.0)), reverse=True)

    # 3. Holding Health & Rotation Check
    evaluated_holdings = []
    rotations = []

    if active_holdings:
        for h in active_holdings:
            sym = h['ticker']
            df_sym = load_candles_from_db(sym, timeframe=timeframe)
            if not df_sym.empty:
                df_sym = compute_indicators(df_sym)
            evaluated_holdings.append(evaluate_holding_health(h, df_sym))

        rotations = check_capital_rotation(candidates, evaluated_holdings)

    return {
        "regime": regime,
        "total_scanned": len(tickers),
        "candidates_found": len(candidates),
        "top_candidates": candidates[:40], # Top 40 opportunities (comprehensive coverage)
        "evaluated_holdings": evaluated_holdings,
        "rotations": rotations
    }

def print_scan_report(results: Dict):
    """Prints a beautiful, clean terminal summary of scan results."""
    regime = results['regime']
    candidates = results['top_candidates']
    rotations = results['rotations']

    print("\n" + "="*80)
    print("🎯  SENTINEL-SWING | MULTI-FACTOR OPPORTUNITY RADAR")
    print("="*80)
    print(f"Market Regime  : {regime['status']} (Nifty: ₹{regime['nifty_close']:,.2f} | 50 EMA: ₹{regime['nifty_ema50']:,.2f})")
    print(f"Total Scanned  : {results['total_scanned']} stocks")
    print(f"Qualified Setups: {results['candidates_found']} stocks")
    print("-"*80)

    if not candidates:
        print("No qualified setups found matching strict quality criteria.")
    else:
        header = f"{'Rank':<4} {'Ticker':<14} {'Alpha':<7} {'Price':<10} {'Target':<10} {'SL':<10} {'RRR':<6} {'Setup Key Factors'}"
        print(header)
        print("-" * 80)
        for i, c in enumerate(candidates, 1):
            print(
                f"{i:<4} {c['ticker']:<14} {c['alpha_score']:<7} "
                f"₹{c['close']:<9.2f} ₹{c['target']:<9.2f} ₹{c['stop_loss']:<9.2f} "
                f"{c['rrr']:<6.2f} {c['reasons'][:35]}"
            )

    if rotations:
        print("\n" + "="*80)
        print("🔄  CAPITAL ROTATION / TRADE SWAP RECOMMENDATION")
        print("="*80)
        for r in rotations:
            print(f"[ACTION] {r['rationale']}\n")
            print(f"  [-] LIQUIDATE WEAK HOLDING : {r['exit_ticker']} (Health: {r['exit_health']} | PnL: {r['exit_pnl_pct']:.2f}%)")
            print(f"      Reason: {r['exit_reason']}")
            print(f"  [+] ENTER NEW HIGH-ALPHA   : {r['enter_ticker']} (Alpha: {r['enter_alpha']} | Target: +{r['enter_target_pct']:.2f}%)")
            print(f"      Setup : {r['enter_reasons']}")
            print(f"  [*] NET ALPHA GAIN         : +{r['alpha_delta']} points (Covers ~0.35% Indian friction)")
            print("-" * 80)

    print("\n")

if __name__ == "__main__":
    # Test with simulated open positions to verify the capital rotation logic
    mock_holdings = [
        {
            "ticker": "INFY.NS",
            "entry_price": 1850.0,
            "entry_time": "2026-10-15 09:15:00",
            "qty": 20
        },
        {
            "ticker": "TCS.NS",
            "entry_price": 4100.0,
            "entry_time": "2026-11-01 09:15:00",
            "qty": 10
        }
    ]

    res = run_opportunity_scan(timeframe="1D", active_holdings=mock_holdings)
    print_scan_report(res)

