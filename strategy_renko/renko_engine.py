"""
Renko Brick Engine
Converts standard OHLC time-series candles into price-movement Renko bricks.
Handles classic Renko construction with 2-brick reversal logic.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
import numpy as np
import pandas as pd


@dataclass
class RenkoBrick:
    index: int
    timestamp: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    direction: int  # 1 for Green/Up, -1 for Red/Down
    brick_size: float
    source_candle_idx: int


class RenkoEngine:
    def __init__(self, brick_size: float = 25.0, use_wicks: bool = False):
        """
        :param brick_size: The price movement threshold to create a new brick.
        :param use_wicks: If True, evaluates High/Low for brick formation; if False, uses Close.
        """
        self.brick_size = float(brick_size)
        self.use_wicks = use_wicks

    def build_bricks(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Transforms a DataFrame with columns ['timestamp', 'open', 'high', 'low', 'close']
        into a DataFrame of Renko bricks.
        """
        if df.empty:
            return pd.DataFrame()

        df = df.sort_values(by="timestamp").reset_index(drop=True)
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"])

        bricks: List[RenkoBrick] = []
        b_size = self.brick_size

        first_close = df.iloc[0]["close"]
        # Anchor initial base to nearest brick multiple
        base_price = round(first_close / b_size) * b_size
        prev_close = base_price
        prev_open = base_price
        direction = 0  # 0: uninitialized, 1: up, -1: down
        brick_idx = 0

        for i, row in df.iterrows():
            ts = row["timestamp"]
            price_evals = [row["close"]] if not self.use_wicks else [row["high"], row["low"], row["close"]]

            for p in price_evals:
                if direction == 0:
                    diff = p - prev_close
                    if diff >= b_size:
                        num_bricks = int(diff // b_size)
                        for _ in range(num_bricks):
                            b_open = prev_close
                            b_close = b_open + b_size
                            bricks.append(
                                RenkoBrick(
                                    index=brick_idx,
                                    timestamp=ts,
                                    open=b_open,
                                    high=b_close,
                                    low=b_open,
                                    close=b_close,
                                    direction=1,
                                    brick_size=b_size,
                                    source_candle_idx=i,
                                )
                            )
                            prev_open = b_open
                            prev_close = b_close
                            direction = 1
                            brick_idx += 1
                    elif diff <= -b_size:
                        num_bricks = int(abs(diff) // b_size)
                        for _ in range(num_bricks):
                            b_open = prev_close
                            b_close = b_open - b_size
                            bricks.append(
                                RenkoBrick(
                                    index=brick_idx,
                                    timestamp=ts,
                                    open=b_open,
                                    high=b_open,
                                    low=b_close,
                                    close=b_close,
                                    direction=-1,
                                    brick_size=b_size,
                                    source_candle_idx=i,
                                )
                            )
                            prev_open = b_open
                            prev_close = b_close
                            direction = -1
                            brick_idx += 1

                elif direction == 1:
                    # Current trend is UP
                    # 1. Continuation: price moves up by >= 1 brick
                    if p - prev_close >= b_size:
                        num_bricks = int((p - prev_close) // b_size)
                        for _ in range(num_bricks):
                            b_open = prev_close
                            b_close = b_open + b_size
                            bricks.append(
                                RenkoBrick(
                                    index=brick_idx,
                                    timestamp=ts,
                                    open=b_open,
                                    high=b_close,
                                    low=b_open,
                                    close=b_close,
                                    direction=1,
                                    brick_size=b_size,
                                    source_candle_idx=i,
                                )
                            )
                            prev_open = b_open
                            prev_close = b_close
                            brick_idx += 1

                    # 2. Reversal: requires price to drop below prev_open - b_size (2 bricks distance from high)
                    elif p <= prev_open - b_size:
                        reversal_dist = prev_open - p
                        num_bricks = int(reversal_dist // b_size)
                        # First reversal brick starts from prev_open downwards
                        b_open = prev_open
                        b_close = b_open - b_size
                        bricks.append(
                            RenkoBrick(
                                index=brick_idx,
                                timestamp=ts,
                                open=b_open,
                                high=b_open,
                                low=b_close,
                                close=b_close,
                                direction=-1,
                                brick_size=b_size,
                                source_candle_idx=i,
                            )
                        )
                        prev_open = b_open
                        prev_close = b_close
                        direction = -1
                        brick_idx += 1

                        # Additional continuation bricks in same candle
                        for _ in range(num_bricks - 1):
                            b_open = prev_close
                            b_close = b_open - b_size
                            bricks.append(
                                RenkoBrick(
                                    index=brick_idx,
                                    timestamp=ts,
                                    open=b_open,
                                    high=b_open,
                                    low=b_close,
                                    close=b_close,
                                    direction=-1,
                                    brick_size=b_size,
                                    source_candle_idx=i,
                                )
                            )
                            prev_open = b_open
                            prev_close = b_close
                            brick_idx += 1

                elif direction == -1:
                    # Current trend is DOWN
                    # 1. Continuation: price moves down by >= 1 brick
                    if prev_close - p >= b_size:
                        num_bricks = int((prev_close - p) // b_size)
                        for _ in range(num_bricks):
                            b_open = prev_close
                            b_close = b_open - b_size
                            bricks.append(
                                RenkoBrick(
                                    index=brick_idx,
                                    timestamp=ts,
                                    open=b_open,
                                    high=b_open,
                                    low=b_close,
                                    close=b_close,
                                    direction=-1,
                                    brick_size=b_size,
                                    source_candle_idx=i,
                                )
                            )
                            prev_open = b_open
                            prev_close = b_close
                            brick_idx += 1

                    # 2. Reversal: requires price to rise above prev_open + b_size (2 bricks distance from low)
                    elif p >= prev_open + b_size:
                        reversal_dist = p - prev_open
                        num_bricks = int(reversal_dist // b_size)
                        # First reversal brick starts from prev_open upwards
                        b_open = prev_open
                        b_close = b_open + b_size
                        bricks.append(
                            RenkoBrick(
                                index=brick_idx,
                                timestamp=ts,
                                open=b_open,
                                high=b_close,
                                low=b_open,
                                close=b_close,
                                direction=1,
                                brick_size=b_size,
                                source_candle_idx=i,
                            )
                        )
                        prev_open = b_open
                        prev_close = b_close
                        direction = 1
                        brick_idx += 1

                        # Additional continuation bricks in same candle
                        for _ in range(num_bricks - 1):
                            b_open = prev_close
                            b_close = b_open + b_size
                            bricks.append(
                                RenkoBrick(
                                    index=brick_idx,
                                    timestamp=ts,
                                    open=b_open,
                                    high=b_close,
                                    low=b_open,
                                    close=b_close,
                                    direction=1,
                                    brick_size=b_size,
                                    source_candle_idx=i,
                                )
                            )
                            prev_open = b_open
                            prev_close = b_close
                            brick_idx += 1

        if not bricks:
            return pd.DataFrame()

        brick_df = pd.DataFrame([b.__dict__ for b in bricks])
        return brick_df
