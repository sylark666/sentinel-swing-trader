# v1.0.9
import pandas as pd
import logging
from datetime import datetime

logger = logging.getLogger("StateMachine")

class SentinelState:
    """
    Manages the live execution ledger and state variables for Sentinel-Swing.
    Interacts with the PostgreSQL DB (active_trades table) to enforce constraints.
    """
    def __init__(self, max_positions: int = 3, max_allocation_pct: float = 0.30):
        self.max_positions = max_positions
        self.max_alloc = max_allocation_pct
        self.active_holdings = [] # Mocked representation of DB state

    def check_allocation_availability(self, total_equity: float) -> float:
        """
        Determines the capital available for a new trade without violating
        the 30% max allocation or 3-position cap.
        """
        if len(self.active_holdings) >= self.max_positions:
            logger.warning("Constraint Block: Max positions (3) reached.")
            return 0.0
            
        return total_equity * self.max_alloc

    def synthesize_daily_candle(self, historical_1d: pd.DataFrame, live_quote: dict) -> pd.DataFrame:
        """
        Constructs a Synthetic Daily Candle at 09:45 AM.
        Because daily candles are incomplete, this appends the live quote
        as today's temporary daily candle to prevent look-ahead bias and delays.
        """
        df = historical_1d.copy()
        synthetic_ts = pd.to_datetime('today').normalize()
        
        synthetic_row = {
            'timestamp': synthetic_ts,
            'open': live_quote['open'],
            'high': live_quote['high'],
            'low': live_quote['low'],
            'close': live_quote['last_price'],
            'volume': live_quote.get('volume', 0)
        }
        
        # Append synthetic row
        df.loc[len(df)] = synthetic_row
        return df

    def calculate_execution_pad(self, trigger_price: float, is_buy: bool, pad_pct: float = 0.005) -> float:
        """
        The Padded Execution Strategy: 
        NSE discontinued SL-M. To prevent a fast price crash from blowing past a 
        limit order, this calculates a padded negative offset.
        """
        if is_buy:
            # For buy stop limit, we are willing to buy slightly higher
            return trigger_price * (1 + pad_pct)
        else:
            # For sell stop limit, we are willing to sell slightly lower
            return trigger_price * (1 - pad_pct)

    def asset_rotation_check(self, new_signal_score: float, weakest_holding_score: float) -> bool:
        """
        If system has 3 active positions but discovers a much higher-ranked signal,
        evaluate if alpha differential justifies liquidating the weakest holding.
        """
        alpha_diff = new_signal_score - weakest_holding_score
        # Requires at least a 15% arbitrary alpha jump to force a rotation
        return alpha_diff >= 15.0

    def manage_0325_trail(self, ticker: str, current_close: float, atr_22: float) -> float:
        """
        Calculates the dynamic Average True Range (ATR) trailing mechanism.
        Executes strictly at 15:25 PM IST.
        """
        # Trail floor moves up based on 3x ATR
        new_trail = current_close - (atr_22 * 3.0)
        logger.info(f"[{ticker}] 15:25 PM TSL Updated -> ₹{new_trail:.2f}")
        return new_trail

if __name__ == "__main__":
    logger.info("State Machine scaffolding initialized [Temp 0].")
