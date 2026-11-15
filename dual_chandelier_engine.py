"""
dual_chandelier_engine.py
=========================
Implements the Dual-Stage Stepped Ratchet Chandelier Trailing Exit:
- Stage 1 (Breathing Room): 3.0x ATR beneath peak high (0% to +9.9% gain).
- Stage 2 (Profit Protection): Automatically ratchets to 2.3x ATR once unrealized gain reaches >= +10.0%.
- Monotonic Ratchet: The stop loss only ever moves UP, never down.
"""

from typing import Dict, List, Tuple
from datetime import datetime
import pandas as pd
import portfolio_manager as pm

PROFIT_THRESHOLD_RATCHET = 10.0  # +10% gain triggers tighter 2.3x ATR
STAGE_1_MULT = 3.0               # Initial wide breathing room
STAGE_2_MULT = 2.3               # Tightened profit lock


def update_trailing_stops_and_check_exits(current_candle_data: Dict[str, Dict]) -> List[Dict]:
    """
    Evaluates every active swing trade against its latest completed 4H candle:
    1. Updates peak high (max_high).
    2. Checks if profit >= +10.0% to ratchet from 3.0x ATR to 2.3x ATR.
    3. Ratchets stop loss monotonically (never moves lower).
    4. Triggers exit if Low <= Stop or Close < Trailing Stop.
    
    Returns a list of exit alert dictionaries for any positions that must be closed.
    """
    active_trades = pm.get_active_trades()
    exits_to_fire = []

    if not active_trades:
        return []

    with pm.get_db() as conn:
        cursor = conn.cursor()

        for pos in active_trades:
            ticker = pos['ticker']
            if ticker not in current_candle_data:
                continue

            bar = current_candle_data[ticker]
            close = float(bar['close'])
            low = float(bar['low'])
            high = float(bar['high'])
            atr = float(bar.get('ATR', 0))

            entry_price = float(pos['entry_price'])
            max_high = max(float(pos.get('max_high', entry_price)), high)
            curr_stop = float(pos['current_trailing_stop'])
            stage = int(pos.get('chandelier_stage', 1))

            # Calculate unrealized profit % from entry
            unrealized_pnl_pct = ((close - entry_price) / entry_price) * 100.0

            # -------------------------------------------------------------
            # STEP 1: DUAL-STAGE RATCHET LOGIC
            # -------------------------------------------------------------
            if unrealized_pnl_pct >= PROFIT_THRESHOLD_RATCHET:
                stage = 2
                active_mult = STAGE_2_MULT
            else:
                active_mult = STAGE_1_MULT

            # Compute new trailing stop level
            if atr > 0:
                raw_chandelier_stop = max_high - (active_mult * atr)
                # Monotonic rule: Stop loss can ONLY ratchet UP, never lower
                new_stop = max(curr_stop, raw_chandelier_stop)
            else:
                new_stop = curr_stop

            # -------------------------------------------------------------
            # STEP 2: CHECK EXIT CONDITIONS
            # -------------------------------------------------------------
            exit_triggered = False
            exit_reason = None
            exit_price = None

            # Condition A: Hard Stop Breach (Catastrophic Risk Stop)
            if low <= pos['initial_stop']:
                exit_triggered = True
                exit_price = pos['initial_stop']
                exit_reason = "Initial ATR Catastrophic Stop Loss Hit"

            # Condition B: Trailing Chandelier Stop Breach
            elif close < new_stop or bar.get('Chandelier_Dir', 0) == -1:
                exit_triggered = True
                exit_price = close
                exit_reason = f"Chandelier Trend Reversal (Stage {stage} Stop Hit @ ₹{new_stop:,.2f})"

            if exit_triggered:
                qty = pos['qty']
                gross_buy = qty * entry_price
                gross_sell = qty * exit_price
                friction = pm.calculate_friction(gross_sell, is_buy=False) + pm.calculate_friction(gross_buy, is_buy=True)
                net_pnl = (gross_sell - gross_buy) - friction
                pnl_pct = (net_pnl / gross_buy) * 100

                entry_dt = pd.to_datetime(pos['entry_date'])
                holding_days = (datetime.now() - entry_dt).total_seconds() / 86400.0

                exits_to_fire.append({
                    "ticker": ticker,
                    "exit_price": round(exit_price, 2),
                    "exit_reason": exit_reason,
                    "pnl_pct": round(pnl_pct, 2),
                    "net_pnl_rs": round(net_pnl, 2),
                    "holding_days": round(holding_days, 1),
                    "current_qty": qty
                })
            else:
                # Update position state in database
                cursor.execute("""
                    UPDATE active_swing_trades 
                    SET max_high = ?, current_trailing_stop = ?, chandelier_stage = ?
                    WHERE ticker = ?
                """, (max_high, new_stop, stage, ticker))

        conn.commit()

    return exits_to_fire

