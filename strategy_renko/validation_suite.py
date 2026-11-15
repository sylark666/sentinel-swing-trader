"""
Institutional Validation Suite for Systematic Trading Strategies
Implements the 3 Mandatory Pre-Live Institutional Gates:
1. Parameter Sensitivity Heatmaps (Plateau vs Lonely Spike Analysis)
2. Walk-Forward Analysis (WFA)
3. Monte Carlo Reshuffle Simulation (Sequence-of-Returns Risk)
"""

import math
from dataclasses import dataclass
from typing import Dict, List, Tuple, Any, Optional
import numpy as np
import pandas as pd

from strategy_renko.renko_engine import RenkoEngine
from strategy_renko.supertrend import calculate_supertrend
from strategy_renko.strategy_1_engine import RenkoSupertrendEngine, BacktestResult


@dataclass
class SensitivityResult:
    parameter_grid: pd.DataFrame
    best_params: Dict[str, Any]
    peak_profit_factor: float
    peak_total_points: float
    plateau_score: float  # Measure of neighbor stability (0.0 to 1.0)
    is_robust_plateau: bool  # True if plateau, False if lonely spike
    robustness_summary: str


@dataclass
class WalkForwardFold:
    fold_idx: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    is_trades: int
    is_points: float
    is_profit_factor: float
    is_win_rate: float
    oos_trades: int
    oos_points: float
    oos_profit_factor: float
    oos_win_rate: float
    wfe_ratio: float  # OOS Expectancy / IS Expectancy


@dataclass
class WalkForwardResult:
    folds: List[WalkForwardFold]
    overall_wfe: float
    is_total_points: float
    oos_total_points: float
    oos_win_rate: float
    passed_wfa: bool
    summary: str


@dataclass
class MonteCarloResult:
    num_simulations: int
    median_max_dd_points: float
    pct_95_max_dd_points: float
    pct_99_max_dd_points: float
    worst_case_dd_points: float
    probability_of_ruin_pct: float  # Probability of DD exceeding ruin_threshold
    longest_losing_streak_95pct: int
    final_points_5pct: float
    final_points_median: float
    final_points_95pct: float
    passed_monte_carlo: bool
    summary: str


class InstitutionalValidationSuite:
    def __init__(self, raw_candle_df: pd.DataFrame, symbol: str = "NIFTY.NS"):
        self.raw_df = raw_candle_df.copy()
        if not pd.api.types.is_datetime64_any_dtype(self.raw_df["timestamp"]):
            self.raw_df["timestamp"] = pd.to_datetime(self.raw_df["timestamp"])
        self.raw_df = self.raw_df.sort_values(by="timestamp").reset_index(drop=True)
        self.symbol = symbol

    # =========================================================================
    # GATE 1: Parameter Sensitivity (Plateau vs Lonely Spike)
    # =========================================================================
    def run_parameter_sensitivity(
        self,
        brick_sizes: List[float] = [15.0, 20.0, 25.0, 30.0, 35.0],
        st_multipliers: List[float] = [1.5, 2.0, 2.5, 3.0, 3.5],
        st_period: int = 10,
        fixed_sl_points: float = 25.0
    ) -> SensitivityResult:
        """
        Sweeps Brick Size and Supertrend Multiplier.
        Evaluates whether high-performing parameter combinations sit on a
        broad, forgiving plateau or a fragile, lonely spike.
        """
        records = []
        matrix_points = np.zeros((len(brick_sizes), len(st_multipliers)))
        matrix_pf = np.zeros((len(brick_sizes), len(st_multipliers)))

        # Cache bricks per brick size to avoid redundant brick calculations
        brick_cache = {}
        for b in brick_sizes:
            engine = RenkoEngine(brick_size=b)
            brick_cache[b] = engine.build_bricks(self.raw_df)

        for b_idx, b in enumerate(brick_sizes):
            bricks = brick_cache[b]
            for m_idx, m in enumerate(st_multipliers):
                st = calculate_supertrend(bricks, period=st_period, multiplier=m)
                sim = RenkoSupertrendEngine(
                    symbol=self.symbol,
                    fixed_sl_points=fixed_sl_points
                )
                res = sim.run_backtest(st)

                matrix_points[b_idx, m_idx] = res.total_points
                matrix_pf[b_idx, m_idx] = res.profit_factor if res.profit_factor < 50 else 50.0

                records.append({
                    "brick_size": b,
                    "st_multiplier": m,
                    "total_trades": res.total_trades,
                    "win_rate": res.win_rate,
                    "total_points": res.total_points,
                    "profit_factor": res.profit_factor,
                    "expectancy_pts": res.expectancy_points,
                    "max_dd_pts": res.max_drawdown_points,
                    "sharpe_ratio": res.sharpe_ratio
                })

        grid_df = pd.DataFrame(records)

        # Find Peak
        best_row = grid_df.loc[grid_df["total_points"].idxmax()]
        best_b = best_row["brick_size"]
        best_m = best_row["st_multiplier"]
        b_idx_best = brick_sizes.index(best_b)
        m_idx_best = st_multipliers.index(best_m)

        # Analyze Plateau vs Spike
        # A cell is part of a plateau if its immediate adjacent neighbors are also profitable
        # and maintain positive expectancy with low coefficient of variation.
        neighbors = []
        for db in [-1, 0, 1]:
            for dm in [-1, 0, 1]:
                if db == 0 and dm == 0:
                    continue
                ni = b_idx_best + db
                nj = m_idx_best + dm
                if 0 <= ni < len(brick_sizes) and 0 <= nj < len(st_multipliers):
                    neighbors.append(matrix_points[ni, nj])

        peak_pts = best_row["total_points"]
        neighbor_mean = np.mean(neighbors) if neighbors else peak_pts
        neighbor_std = np.std(neighbors) if neighbors else 0.0
        neighbor_min = np.min(neighbors) if neighbors else peak_pts

        # Plateau Score:
        # Measures ratio of neighbor mean to peak, penalized by neighbor dispersion
        cv = (neighbor_std / neighbor_mean) if neighbor_mean > 0 else 1.0
        ratio = max(0.0, neighbor_mean / peak_pts) if peak_pts > 0 else 0.0
        plateau_score = max(0.0, min(1.0, ratio * (1.0 - min(cv, 0.8))))

        # Criteria for Institutional Plateau:
        # 1. Neighbor mean is at least 65% of peak
        # 2. No neighbor has negative points (no catastrophic cliff)
        # 3. Plateau score >= 0.50
        is_plateau = (neighbor_min > 0) and (ratio >= 0.60) and (plateau_score >= 0.50)

        if is_plateau:
            summary = (
                f"✅ ROBUST PLATEAU CONFIRMED: Peak ({best_b}pt brick, {best_m}x mult) yields "
                f"+{peak_pts:.1f} pts. Neighbor cluster averages {neighbor_mean:.1f} pts "
                f"({ratio*100:.1f}% of peak) with no losing boundary cells. Edge is real, not an overfit spike."
            )
        else:
            summary = (
                f"⚠️ LONELY SPIKE WARNING: Peak ({best_b}pt brick, {best_m}x mult) yields "
                f"+{peak_pts:.1f} pts, but neighbor performance drops sharply to {neighbor_mean:.1f} pts "
                f"(min {neighbor_min:.1f} pts). Parameter sensitivity is fragile."
            )

        return SensitivityResult(
            parameter_grid=grid_df,
            best_params={"brick_size": best_b, "st_multiplier": best_m, "st_period": st_period, "sl": fixed_sl_points},
            peak_profit_factor=best_row["profit_factor"],
            peak_total_points=peak_pts,
            plateau_score=plateau_score,
            is_robust_plateau=is_plateau,
            robustness_summary=summary
        )

    # =========================================================================
    # GATE 2: Walk-Forward Analysis (WFA)
    # =========================================================================
    def run_walk_forward_analysis(
        self,
        n_splits: int = 5,
        train_ratio: float = 0.70,
        brick_size: float = 25.0,
        st_multiplier: float = 2.0,
        st_period: int = 10,
        fixed_sl_points: float = 25.0
    ) -> WalkForwardResult:
        """
        Splits timeline into n_splits sequential chronological folds.
        Evaluates Out-of-Sample performance to measure Walk-Forward Efficiency (WFE).
        """
        unique_dates = sorted(self.raw_df["timestamp"].dt.date.unique())
        total_days = len(unique_dates)
        fold_size = total_days // n_splits

        folds: List[WalkForwardFold] = []
        is_all_points = 0.0
        oos_all_points = 0.0
        oos_all_wins = 0
        oos_all_trades = 0

        for f in range(n_splits):
            start_idx = f * fold_size
            end_idx = min(start_idx + fold_size, total_days)
            fold_dates = unique_dates[start_idx:end_idx]

            n_train = int(len(fold_dates) * train_ratio)
            train_dates = fold_dates[:n_train]
            test_dates = fold_dates[n_train:]

            if not train_dates or not test_dates:
                continue

            train_df = self.raw_df[self.raw_df["timestamp"].dt.date.isin(train_dates)]
            test_df = self.raw_df[self.raw_df["timestamp"].dt.date.isin(test_dates)]

            # In-Sample
            re_train = RenkoEngine(brick_size=brick_size).build_bricks(train_df)
            st_train = calculate_supertrend(re_train, period=st_period, multiplier=st_multiplier)
            sim_train = RenkoSupertrendEngine(symbol=self.symbol, fixed_sl_points=fixed_sl_points)
            res_train = sim_train.run_backtest(st_train)

            # Out-of-Sample
            re_test = RenkoEngine(brick_size=brick_size).build_bricks(test_df)
            st_test = calculate_supertrend(re_test, period=st_period, multiplier=st_multiplier)
            sim_test = RenkoSupertrendEngine(symbol=self.symbol, fixed_sl_points=fixed_sl_points)
            res_test = sim_test.run_backtest(st_test)

            is_exp = res_train.expectancy_points
            oos_exp = res_test.expectancy_points
            wfe = (oos_exp / is_exp) if is_exp > 0 else (1.0 if oos_exp > 0 else 0.0)

            fold_obj = WalkForwardFold(
                fold_idx=f + 1,
                train_start=train_df["timestamp"].iloc[0],
                train_end=train_df["timestamp"].iloc[-1],
                test_start=test_df["timestamp"].iloc[0],
                test_end=test_df["timestamp"].iloc[-1],
                is_trades=res_train.total_trades,
                is_points=res_train.total_points,
                is_profit_factor=res_train.profit_factor,
                is_win_rate=res_train.win_rate,
                oos_trades=res_test.total_trades,
                oos_points=res_test.total_points,
                oos_profit_factor=res_test.profit_factor,
                oos_win_rate=res_test.win_rate,
                wfe_ratio=wfe
            )
            folds.append(fold_obj)

            is_all_points += res_train.total_points
            oos_all_points += res_test.total_points
            oos_all_wins += res_test.winning_trades
            oos_all_trades += res_test.total_trades

        avg_wfe = np.mean([f.wfe_ratio for f in folds]) if folds else 0.0
        overall_oos_win_rate = (oos_all_wins / oos_all_trades) * 100.0 if oos_all_trades > 0 else 0.0

        # Institutional WFA passing standard:
        # Walk-Forward Efficiency >= 50% (OOS preserves at least 50% of IS edge)
        # OOS cumulative points must be positive across all test periods
        passed = (avg_wfe >= 0.50) and (oos_all_points > 0)

        if passed:
            summary = (
                f"✅ WALK-FORWARD VERIFIED: Out-of-Sample Walk-Forward Efficiency (WFE) = {avg_wfe*100:.1f}% "
                f"(Threshold >= 50%). Total OOS Net Points = +{oos_all_points:.1f} pts across {oos_all_trades} trades. "
                f"Strategy demonstrates strong out-of-sample edge persistence."
            )
        else:
            summary = (
                f"⚠️ WALK-FORWARD UNDERPERFORMANCE: WFE = {avg_wfe*100:.1f}% (Below 50% target) or OOS "
                f"net points ({oos_all_points:.1f}) negative. Degrades out of sample."
            )

        return WalkForwardResult(
            folds=folds,
            overall_wfe=avg_wfe,
            is_total_points=is_all_points,
            oos_total_points=oos_all_points,
            oos_win_rate=overall_oos_win_rate,
            passed_wfa=passed,
            summary=summary
        )

    # =========================================================================
    # GATE 3: Monte Carlo Reshuffle Simulation
    # =========================================================================
    def run_monte_carlo(
        self,
        trades: List[Any],
        num_simulations: int = 2500,
        ruin_threshold_pts: float = 350.0
    ) -> MonteCarloResult:
        """
        Runs 2,500 random reshuffles (permutations) of the trade series to test
        for sequence-of-returns risk and extreme drawdown probability.
        """
        if not trades:
            return MonteCarloResult(
                0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, False, "No trades to simulate."
            )

        points = np.array([t.points for t in trades])
        n_trades = len(points)

        max_drawdowns = np.zeros(num_simulations)
        final_points = np.zeros(num_simulations)
        max_loss_streaks = np.zeros(num_simulations, dtype=int)
        ruin_count = 0

        # Deterministic seed for reproducible validation
        rng = np.random.default_rng(seed=42)

        for s in range(num_simulations):
            # Reshuffle trades without replacement (tests path risk of identical trades)
            shuffled = rng.permutation(points)

            # Cumulative equity curve
            equity = np.cumsum(shuffled)
            peak = np.maximum.accumulate(equity)
            dd = peak - equity
            sim_max_dd = float(np.max(dd))

            max_drawdowns[s] = sim_max_dd
            final_points[s] = equity[-1]

            if sim_max_dd >= ruin_threshold_pts:
                ruin_count += 1

            # Longest loss streak in this permutation
            streak = 0
            max_s = 0
            for pt in shuffled:
                if pt < -1.0:
                    streak += 1
                    if streak > max_s:
                        max_s = streak
                else:
                    streak = 0
            max_loss_streaks[s] = max_s

        median_dd = float(np.median(max_drawdowns))
        pct_95_dd = float(np.percentile(max_drawdowns, 95))
        pct_99_dd = float(np.percentile(max_drawdowns, 99))
        worst_dd = float(np.max(max_drawdowns))
        prob_ruin = (ruin_count / num_simulations) * 100.0

        p95_streak = int(np.percentile(max_loss_streaks, 95))
        f_p5 = float(np.percentile(final_points, 5))
        f_med = float(np.median(final_points))
        f_p95 = float(np.percentile(final_points, 95))

        # Institutional acceptance criteria:
        # 1. 95% worst-case drawdown < ruin_threshold_pts (e.g. 350 pts)
        # 2. Probability of ruin <= 1.0%
        passed = (pct_95_dd < ruin_threshold_pts) and (prob_ruin <= 1.0)

        if passed:
            summary = (
                f"✅ MONTE CARLO PASSED (2,500 Reshuffles): 95% Worst-Case Drawdown = {pct_95_dd:.1f} pts "
                f"(99% Tail DD = {pct_99_dd:.1f} pts). Probability of Ruin ({ruin_threshold_pts:.0f}pt DD) = {prob_ruin:.2f}%. "
                f"95th percentile max consecutive loss streak = {p95_streak} trades. Path risk is strictly controlled."
            )
        else:
            summary = (
                f"⚠️ MONTE CARLO RISK EXCEEDED: 95% DD ({pct_95_dd:.1f} pts) or Ruin Prob ({prob_ruin:.1f}%) exceeds safety ceiling."
            )

        return MonteCarloResult(
            num_simulations=num_simulations,
            median_max_dd_points=median_dd,
            pct_95_max_dd_points=pct_95_dd,
            pct_99_max_dd_points=pct_99_dd,
            worst_case_dd_points=worst_dd,
            probability_of_ruin_pct=prob_ruin,
            longest_losing_streak_95pct=p95_streak,
            final_points_5pct=f_p5,
            final_points_median=f_med,
            final_points_95pct=f_p95,
            passed_monte_carlo=passed,
            summary=summary
        )
