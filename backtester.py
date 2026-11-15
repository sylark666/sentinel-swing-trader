# v1.0.7
import pandas as pd
import numpy as np
import logging
from typing import List, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("Backtester")

class PurgedCrossValidator:
    """
    Implements Purged Cross-Validation for swing trading to prevent data leakage.
    Drops (purges) all historical daily and 15-minute data bars spanning 15 days 
    immediately following the boundary of any training/testing partition.
    """
    def __init__(self, purge_days: int = 15):
        self.purge_days = purge_days

    def split(self, df: pd.DataFrame, n_splits: int = 5) -> List[Tuple[pd.DataFrame, pd.DataFrame]]:
        """
        Returns list of (train_df, test_df) ensuring strict purging between sets.
        """
        if 'timestamp' not in df.columns:
            raise ValueError("Dataframe must contain 'timestamp' column")
            
        df = df.sort_values(by='timestamp').copy()
        
        total_len = len(df)
        split_size = total_len // n_splits
        
        splits = []
        for i in range(1, n_splits):
            train_end_idx = split_size * i
            train_df = df.iloc[:train_end_idx]
            
            # Identify the boundary timestamp
            last_train_ts = train_df['timestamp'].iloc[-1]
            # Calculate the explicit purge deadline
            purge_end_ts = last_train_ts + pd.Timedelta(days=self.purge_days)
            
            # The test block drops everything within the purge window
            test_df = df[df['timestamp'] > purge_end_ts]
            splits.append((train_df, test_df))
            
            logger.debug(f"Fold {i} | Train Ends: {last_train_ts} | Purge Window Ends: {purge_end_ts} | Excluded points to prevent leakage.")
            
        return splits

class EngineMechanics:
    """
    Core backtest loop enforcing execution strictly at 09:45 AM bar mark.
    Validates exits against 15-minute granular bars incorporating the Zerodha friction model.
    """
    def __init__(self, initial_capital=100000, max_positions=3):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.max_positions = max_positions
        self.positions = []
        self.trade_log = []

    def calculate_friction(self, amount: float, is_buy: bool) -> float:
        """
        Calculates the Zerodha friction model for equity delivery.
        """
        brokerage = 0.0 # Zero for equity delivery
        stt = amount * 0.001
        txn_charge = amount * 0.0000345
        gst = (brokerage + txn_charge) * 0.18
        sebi = amount * 0.000001
        stamp = amount * 0.00015 if is_buy else 0.0
        return stt + txn_charge + gst + sebi + stamp

    def simulate_entry_0945(self, df_15m: pd.DataFrame, date: pd.Timestamp) -> float:
        """
        Extracts the synthetic entry price from the 09:45 AM 15-minute bar.
        Returns NaN if no bar exists or market was closed.
        """
        # Filter for the specific day
        day_bars = df_15m[df_15m['timestamp'].dt.date == date.date()]
        if day_bars.empty:
            return np.nan
            
        # Extract 09:45 AM Bar (Timezone aware depending on ingestion)
        # Note: 09:45 AM IST is 04:15 AM UTC
        entry_bar = day_bars[day_bars['timestamp'].dt.strftime('%H:%M') == '09:45']
        
        if not entry_bar.empty:
            return entry_bar.iloc[0]['open']
        return np.nan

    def run_backtest(self, signals_df: pd.DataFrame, df_15m: pd.DataFrame):
        """
        Deterministic loop evaluating signals and tracing exits on 15M granularity.
        """
        logger.info(f"Initiating Multi-Timeframe Backtest Engine... Initial Cap: ₹{self.initial_capital}")
        # In actual execution, iterate over timestamps, manage max_positions,
        # compute trailing SL tracking using 15m data, and record closed trades to self.trade_log.
        pass

if __name__ == "__main__":
    logger.info("Purged Cross-Validation and Engine Mechanics modules initialized [Temp 0].")
