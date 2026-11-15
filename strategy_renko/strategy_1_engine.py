"""
Strategy 1: AI Renko Supertrend Engine
Implements the exact 5 rules:
1. Entry on candle/brick close
2. Skip 9:15, trade from 9:20
3. Fixed SL: Nifty 25 points, Sensex 80 points
4. 1:1 -> SL to cost, hold to reverse
5. 2 losses in a row -> day over circuit breaker
"""

from dataclasses import dataclass, field
from datetime import time as dt_time, datetime
from typing import List, Dict, Optional
import numpy as np
import pandas as pd


@dataclass
class TradeRecord:
    trade_id: int
    symbol: str
    direction: int  # 1 for Long, -1 for Short
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""  # Initial_SL, Break_Even_SL, Supertrend_Reversal, EOD_Squareoff
    points: float = 0.0
    net_pnl: float = 0.0
    lot_size: int = 25
    friction: float = 0.0
    sl_moved_to_cost: bool = False
    holding_bricks: int = 0


@dataclass
class BacktestResult:
    total_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate: float
    total_points: float
    net_pnl: float
    profit_factor: float
    expectancy_points: float
    max_drawdown_points: float
    max_drawdown_pct: float
    sharpe_ratio: float
    consecutive_losses_max: int
    daily_circuit_breaker_triggers: int
    trades: List[TradeRecord]
    equity_curve: pd.DataFrame


class RenkoSupertrendEngine:
    def __init__(
        self,
        symbol: str = "NIFTY.NS",
        fixed_sl_points: float = 25.0,
        lot_size: int = 25,
        start_time: dt_time = dt_time(9, 20),
        cutoff_entry_time: dt_time = dt_time(15, 0),
        squareoff_time: Optional[dt_time] = dt_time(15, 20),
        max_daily_losses: int = 2,
        allow_short: bool = True,
        slippage_points: float = 0.5,
        brokerage_per_order: float = 20.0
    ):
        """
        :param fixed_sl_points: Fixed initial SL in points (Rule 3: 25 for Nifty)
        :param start_time: Trade execution time gate (Rule 2: Skip 9:15, trade from 9:20)
        :param max_daily_losses: Max consecutive losses in a day before circuit breaker (Rule 5: 2 losses)
        :param squareoff_time: Time to square off open intraday positions (None for positional carry)
        """
        self.symbol = symbol
        self.fixed_sl_points = fixed_sl_points
        self.lot_size = lot_size
        self.start_time = start_time
        self.cutoff_entry_time = cutoff_entry_time
        self.squareoff_time = squareoff_time
        self.max_daily_losses = max_daily_losses
        self.allow_short = allow_short
        self.slippage_points = slippage_points
        self.brokerage_per_order = brokerage_per_order

    def _calc_friction(self, price_in: float, price_out: float) -> float:
        """
        Derivatives friction model for Indian Index Futures:
        - Flat ₹20 per trade brokerage (Zerodha/Groww)
        - STT: 0.02% on sell side
        - Exchange charges: 0.00173%
        - GST: 18% on (brokerage + exchange txn charges)
        - Stamp duty: 0.002% on buy side
        - SEBI turnover: 0.0001%
        """
        turnover_in = price_in * self.lot_size
        turnover_out = price_out * self.lot_size
        
        brokerage = self.brokerage_per_order * 2.0  # Buy + Sell
        stt = turnover_out * 0.0002
        exchange_charges = (turnover_in + turnover_out) * 0.0000173
        gst = (brokerage + exchange_charges) * 0.18
        stamp_duty = turnover_in * 0.00002
        sebi = (turnover_in + turnover_out) * 0.000001

        total_friction = brokerage + stt + exchange_charges + gst + stamp_duty + sebi
        return total_friction

    def run_backtest(self, renko_supertrend_df: pd.DataFrame) -> BacktestResult:
        """
        Executes backtest adhering strictly to Strategy 1 rules.
        renko_supertrend_df must have: ['timestamp', 'open', 'high', 'low', 'close', 'signal', 'trend']
        """
        if renko_supertrend_df.empty:
            return BacktestResult(
                0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, [], pd.DataFrame()
            )

        df = renko_supertrend_df.reset_index(drop=True).copy()
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"])

        trades: List[TradeRecord] = []
        trade_id = 1

        active_trade: Optional[TradeRecord] = None
        current_sl = 0.0
        sl_moved_to_cost = False

        # State tracking for daily circuit breaker
        current_date = None
        daily_losses = 0
        circuit_breaker_active = False
        circuit_breaker_triggers = 0
        max_consecutive_losses = 0
        current_consecutive_losses = 0

        for i, row in df.iterrows():
            ts = row["timestamp"]
            date_val = ts.date()
            time_val = ts.time()

            # 1. Reset daily circuit breaker on new day
            if date_val != current_date:
                current_date = date_val
                daily_losses = 0
                circuit_breaker_active = False

            # 2. Manage Active Trade if one is open
            if active_trade is not None:
                active_trade.holding_bricks += 1
                exit_occurred = False
                exit_price = None
                exit_reason = ""

                # EOD Square-off check
                if self.squareoff_time and time_val >= self.squareoff_time:
                    exit_occurred = True
                    exit_price = row["close"]
                    exit_reason = "EOD_Squareoff"

                if not exit_occurred:
                    if active_trade.direction == 1:
                        # LONG POSITION CHECKS
                        # A. Check 1:1 Profit Ratchet to Cost
                        if not sl_moved_to_cost:
                            if row["high"] >= active_trade.entry_price + self.fixed_sl_points:
                                sl_moved_to_cost = True
                                current_sl = active_trade.entry_price
                                active_trade.sl_moved_to_cost = True

                        # B. Check Stop Loss Hit
                        if row["low"] <= current_sl:
                            exit_occurred = True
                            exit_price = current_sl - self.slippage_points
                            exit_reason = "Break_Even_SL" if sl_moved_to_cost else "Initial_SL"

                        # C. Check Supertrend Opposite Reversal (Hold to reverse)
                        elif row["signal"] == -1:
                            exit_occurred = True
                            exit_price = row["close"] - self.slippage_points
                            exit_reason = "Supertrend_Reversal"

                    elif active_trade.direction == -1:
                        # SHORT POSITION CHECKS
                        # A. Check 1:1 Profit Ratchet to Cost
                        if not sl_moved_to_cost:
                            if row["low"] <= active_trade.entry_price - self.fixed_sl_points:
                                sl_moved_to_cost = True
                                current_sl = active_trade.entry_price
                                active_trade.sl_moved_to_cost = True

                        # B. Check Stop Loss Hit
                        if row["high"] >= current_sl:
                            exit_occurred = True
                            exit_price = current_sl + self.slippage_points
                            exit_reason = "Break_Even_SL" if sl_moved_to_cost else "Initial_SL"

                        # C. Check Supertrend Opposite Reversal (Hold to reverse)
                        elif row["signal"] == 1:
                            exit_occurred = True
                            exit_price = row["close"] + self.slippage_points
                            exit_reason = "Supertrend_Reversal"

                # Process Exit
                if exit_occurred:
                    active_trade.exit_time = ts
                    active_trade.exit_price = exit_price
                    active_trade.exit_reason = exit_reason

                    if active_trade.direction == 1:
                        pts = exit_price - active_trade.entry_price
                    else:
                        pts = active_trade.entry_price - exit_price

                    active_trade.points = pts
                    friction = self._calc_friction(active_trade.entry_price, exit_price)
                    active_trade.friction = friction
                    active_trade.net_pnl = (pts * self.lot_size) - friction

                    # Update consecutive loss counters
                    if pts < -1.0:  # Real loss
                        daily_losses += 1
                        current_consecutive_losses += 1
                        if current_consecutive_losses > max_consecutive_losses:
                            max_consecutive_losses = current_consecutive_losses
                        if daily_losses >= self.max_daily_losses:
                            circuit_breaker_active = True
                            circuit_breaker_triggers += 1
                    elif pts > 1.0:  # Win
                        daily_losses = 0
                        current_consecutive_losses = 0

                    trades.append(active_trade)
                    active_trade = None
                    sl_moved_to_cost = False

            # 3. Check for New Entry Trigger
            # Check conditions:
            # - No active trade
            # - Not halted by circuit breaker
            # - Time is >= start_time (Rule 2: Skip 9:15, trade from 9:20)
            # - Time is <= cutoff_entry_time
            if (
                active_trade is None
                and not circuit_breaker_active
                and time_val >= self.start_time
                and time_val <= self.cutoff_entry_time
            ):
                sig = row["signal"]
                if sig == 1:
                    # LONG Entry
                    entry_p = row["close"] + self.slippage_points
                    active_trade = TradeRecord(
                        trade_id=trade_id,
                        symbol=self.symbol,
                        direction=1,
                        entry_time=ts,
                        entry_price=entry_p,
                        lot_size=self.lot_size
                    )
                    trade_id += 1
                    current_sl = entry_p - self.fixed_sl_points
                    sl_moved_to_cost = False

                elif sig == -1 and self.allow_short:
                    # SHORT Entry
                    entry_p = row["close"] - self.slippage_points
                    active_trade = TradeRecord(
                        trade_id=trade_id,
                        symbol=self.symbol,
                        direction=-1,
                        entry_time=ts,
                        entry_price=entry_p,
                        lot_size=self.lot_size
                    )
                    trade_id += 1
                    current_sl = entry_p + self.fixed_sl_points
                    sl_moved_to_cost = False

        # Close any lingering trade at backtest end
        if active_trade is not None:
            last_row = df.iloc[-1]
            active_trade.exit_time = last_row["timestamp"]
            active_trade.exit_price = last_row["close"]
            active_trade.exit_reason = "Backtest_End"
            if active_trade.direction == 1:
                pts = active_trade.exit_price - active_trade.entry_price
            else:
                pts = active_trade.entry_price - active_trade.exit_price
            active_trade.points = pts
            active_trade.friction = self._calc_friction(active_trade.entry_price, active_trade.exit_price)
            active_trade.net_pnl = (pts * self.lot_size) - active_trade.friction
            trades.append(active_trade)

        # 4. Compute Metrics
        return self._compute_summary(trades, max_consecutive_losses, circuit_breaker_triggers)

    def _compute_summary(
        self,
        trades: List[TradeRecord],
        max_consecutive_losses: int,
        circuit_breaker_triggers: int
    ) -> BacktestResult:
        if not trades:
            return BacktestResult(
                0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, [], pd.DataFrame()
            )

        total_trades = len(trades)
        wins = [t for t in trades if t.points > 1.0]
        losses = [t for t in trades if t.points < -1.0]
        bes = [t for t in trades if abs(t.points) <= 1.0]

        win_rate = (len(wins) / total_trades) * 100.0 if total_trades > 0 else 0.0
        total_points = sum(t.points for t in trades)
        net_pnl = sum(t.net_pnl for t in trades)

        gross_profit_pts = sum(t.points for t in wins)
        gross_loss_pts = abs(sum(t.points for t in losses))
        profit_factor = (gross_profit_pts / gross_loss_pts) if gross_loss_pts > 0 else 999.0

        expectancy_points = total_points / total_trades if total_trades > 0 else 0.0

        # Equity Curve and Drawdown
        pnl_series = [t.points for t in trades]
        cum_points = np.cumsum(pnl_series)
        peak = np.maximum.accumulate(cum_points)
        drawdowns = peak - cum_points
        max_dd_pts = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0
        max_dd_pct = (max_dd_pts / np.max(peak)) * 100.0 if np.max(peak) > 0 else 0.0

        # Sharpe Ratio (annualized ~ 250 trading days)
        if len(pnl_series) > 1 and np.std(pnl_series) > 0:
            trades_per_year = (total_trades / 203) * 250  # scaled to ~250 trading days
            sharpe_ratio = (np.mean(pnl_series) / np.std(pnl_series)) * np.sqrt(trades_per_year)
        else:
            sharpe_ratio = 0.0

        equity_df = pd.DataFrame({
            "trade_id": [t.trade_id for t in trades],
            "exit_time": [t.exit_time for t in trades],
            "points": [t.points for t in trades],
            "cum_points": cum_points,
            "net_pnl": [t.net_pnl for t in trades],
            "cum_pnl": np.cumsum([t.net_pnl for t in trades]),
            "drawdown_points": drawdowns
        })

        return BacktestResult(
            total_trades=total_trades,
            winning_trades=len(wins),
            losing_trades=len(losses),
            breakeven_trades=len(bes),
            win_rate=win_rate,
            total_points=total_points,
            net_pnl=net_pnl,
            profit_factor=profit_factor,
            expectancy_points=expectancy_points,
            max_drawdown_points=max_dd_pts,
            max_drawdown_pct=max_dd_pct,
            sharpe_ratio=sharpe_ratio,
            consecutive_losses_max=max_consecutive_losses,
            daily_circuit_breaker_triggers=circuit_breaker_triggers,
            trades=trades,
            equity_curve=equity_df
        )
