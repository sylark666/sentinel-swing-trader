"""
Autonomous Quant Lab: Strategy 1 (AI Renko Supertrend) Master Execution & Institutional Validation Runner

Executes:
1. Baseline Backtest (Rule 1-5 validation)
2. Gate 1: Parameter Sensitivity Analysis (Plateau vs Lonely Spike)
3. Gate 2: Walk-Forward Analysis (WFA)
4. Gate 3: Monte Carlo Reshuffle Simulation (2,500 paths)
"""

import sys
import os
import pandas as pd
import numpy as np

# Ensure root directory is on Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategy_renko.renko_engine import RenkoEngine
from strategy_renko.supertrend import calculate_supertrend
from strategy_renko.strategy_1_engine import RenkoSupertrendEngine
from strategy_renko.validation_suite import InstitutionalValidationSuite


def run_strategy_1_lab():
    print("=" * 80)
    print("🏛️ AUTONOMOUS QUANT LAB: STRATEGY #1 (AI RENKO SUPERTREND)")
    print("=" * 80)

    # 1. Load Data
    data_path = "market_data_cache/TV_NIFTY_NSE_15M_5000.csv"
    if not os.path.exists(data_path):
        print(f"Error: Data file {data_path} not found.")
        return

    print(f"Loading historical NIFTY intraday candles from {data_path}...")
    df = pd.read_csv(data_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values(by="timestamp").reset_index(drop=True)
    print(f"Dataset Loaded: {len(df)} candles spanning {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]} ({df['timestamp'].dt.date.nunique()} trading days)")

    # 2. Baseline Renko & Supertrend Construction
    print("\n" + "-" * 80)
    print("STEP 1: BASELINE RENKO & SUPERTREND CALCULATION")
    print("-" * 80)
    brick_size = 25.0
    st_period = 10
    st_multiplier = 2.0
    sl_points = 25.0

    print(f"Parameters: Brick Size={brick_size} pts | Supertrend Period={st_period} | Multiplier={st_multiplier}x | Fixed SL={sl_points} pts")
    renko_engine = RenkoEngine(brick_size=brick_size)
    bricks = renko_engine.build_bricks(df)
    st_bricks = calculate_supertrend(bricks, period=st_period, multiplier=st_multiplier)
    print(f"Bricks Formed: {len(st_bricks)} bricks from {len(df)} 15M candles.")

    # 3. Strategy 1 Simulation (Strict 5 Rules)
    print("\n" + "-" * 80)
    print("STEP 2: STRATEGY 1 DETERMINISTIC EXECUTION (RULES 1-5)")
    print("-" * 80)
    sim = RenkoSupertrendEngine(
        symbol="NIFTY.NS",
        fixed_sl_points=sl_points,
        lot_size=25,
        max_daily_losses=2,
        allow_short=True
    )
    result = sim.run_backtest(st_bricks)

    print(f"Total Completed Trades:      {result.total_trades}")
    print(f"Winning Trades:               {result.winning_trades} ({result.win_rate:.1f}%)")
    print(f"Losing Trades:                {result.losing_trades} ({(result.losing_trades / result.total_trades)*100:.1f}%)")
    print(f"Break-Even Trades (1:1 saved):{result.breakeven_trades} ({(result.breakeven_trades / result.total_trades)*100:.1f}%)")
    print(f"Total Cumulative Points:      +{result.total_points:.1f} Nifty points")
    print(f"Net Realized PnL (1 Lot 25):  ₹{result.net_pnl:,.2f} (After all taxes, STT & slippage)")
    print(f"Profit Factor:                {result.profit_factor:.2f}")
    print(f"Expectancy:                   +{result.expectancy_points:.2f} points/trade")
    print(f"Maximum Drawdown:             {result.max_drawdown_points:.1f} points ({result.max_drawdown_pct:.1f}%)")
    print(f"Annualized Sharpe Ratio:      {result.sharpe_ratio:.2f}")
    print(f"Max Consecutive Loss Streak:  {result.consecutive_losses_max} trades")
    print(f"Circuit Breaker Triggers:     {result.daily_circuit_breaker_triggers} days halted after 2 losses")

    # Breakdown by Exit Reason
    exit_counts = pd.Series([t.exit_reason for t in result.trades]).value_counts()
    print("\nTrade Exit Distribution:")
    for reason, count in exit_counts.items():
        pct = (count / result.total_trades) * 100
        print(f"  • {reason:<22}: {count:>4} trades ({pct:>5.1f}%)")

    # Sample Trades Table
    print("\nRecent 5 Executed Trades Sample:")
    sample_trades = result.trades[-5:]
    print(f"{'ID':<4} | {'Dir':<5} | {'Entry Time':<19} | {'Entry Px':<9} | {'Exit Time':<19} | {'Exit Px':<9} | {'Pts':<8} | {'Exit Reason'}")
    print("-" * 110)
    for t in sample_trades:
        dir_str = "LONG" if t.direction == 1 else "SHORT"
        print(f"{t.trade_id:<4} | {dir_str:<5} | {str(t.entry_time)[:19]:<19} | {t.entry_price:<9.2f} | {str(t.exit_time)[:19]:<19} | {t.exit_price:<9.2f} | {t.points:>+8.1f} | {t.exit_reason}")

    # =========================================================================
    # INSTITUTIONAL VALIDATION GATES (THE 3 TESTS BEFORE LIVE)
    # =========================================================================
    print("\n" + "=" * 80)
    print("🏛️ COMMENCING THE 3 INSTITUTIONAL VALIDATION GATES")
    print("=" * 80)

    val_suite = InstitutionalValidationSuite(df, symbol="NIFTY.NS")

    # GATE 1: Parameter Sensitivity (Plateau vs Lonely Spike)
    print("\n>>> GATE 1: PARAMETER SENSITIVITY MATRIX (PLATEAU VS LONELY SPIKE) <<<")
    brick_sweep = [15.0, 20.0, 25.0, 30.0, 35.0]
    mult_sweep = [1.5, 2.0, 2.5, 3.0, 3.5]
    print(f"Sweeping Brick Sizes {brick_sweep} x Multipliers {mult_sweep}...")
    sens_res = val_suite.run_parameter_sensitivity(
        brick_sizes=brick_sweep,
        st_multipliers=mult_sweep,
        st_period=10,
        fixed_sl_points=25.0
    )

    # Display 2D Pivot Heatmap Table
    pivot_points = sens_res.parameter_grid.pivot(index="brick_size", columns="st_multiplier", values="total_points")
    pivot_pf = sens_res.parameter_grid.pivot(index="brick_size", columns="st_multiplier", values="profit_factor")

    print("\n[HEATMAP 1] Total Net Points Matrix (Brick Size vs Supertrend Multiplier):")
    print(pivot_points.to_string())

    print("\n[HEATMAP 2] Profit Factor Matrix:")
    print(pivot_pf.round(2).to_string())

    print(f"\nPlateau Analysis Result:")
    print(f"  • Best Parameters:      Brick={sens_res.best_params['brick_size']} pts, Multiplier={sens_res.best_params['st_multiplier']}x")
    print(f"  • Peak Total Points:    +{sens_res.peak_total_points:.1f} pts (PF: {sens_res.peak_profit_factor:.2f})")
    print(f"  • Plateau Score:        {sens_res.plateau_score:.2f} / 1.00")
    print(f"  • Plateau Status:       {'✅ ROBUST PLATEAU' if sens_res.is_robust_plateau else '❌ LONELY SPIKE'}")
    print(f"  • Assessment:           {sens_res.robustness_summary}")

    # GATE 2: Walk-Forward Analysis (WFA)
    print("\n>>> GATE 2: WALK-FORWARD ANALYSIS (5 CHRONOLOGICAL FOLDS, 70% IS / 30% OOS) <<<")
    wfa_res = val_suite.run_walk_forward_analysis(
        n_splits=5,
        train_ratio=0.70,
        brick_size=brick_size,
        st_multiplier=st_multiplier,
        st_period=st_period,
        fixed_sl_points=sl_points
    )

    print(f"{'Fold':<4} | {'Test Period':<23} | {'IS Pts':<10} | {'IS PF':<7} | {'OOS Pts':<10} | {'OOS PF':<7} | {'OOS Win%':<8} | {'WFE'}")
    print("-" * 95)
    for f in wfa_res.folds:
        test_range = f"{str(f.test_start.date())} to {str(f.test_end.date())}"
        print(f"#{f.fold_idx:<3} | {test_range:<23} | {f.is_points:>+10.1f} | {f.is_profit_factor:>7.2f} | {f.oos_points:>+10.1f} | {f.oos_profit_factor:>7.2f} | {f.oos_win_rate:>7.1f}% | {f.wfe_ratio*100:>5.1f}%")

    print(f"\nWalk-Forward Summary:")
    print(f"  • Overall Walk-Forward Efficiency (WFE): {wfa_res.overall_wfe*100:.1f}%")
    print(f"  • Cumulative In-Sample Points:           +{wfa_res.is_total_points:.1f} pts")
    print(f"  • Cumulative Out-of-Sample Points:       +{wfa_res.oos_total_points:.1f} pts")
    print(f"  • Out-of-Sample Win Rate:                {wfa_res.oos_win_rate:.1f}%")
    print(f"  • WFA Status:                            {'✅ PASSED (WFE >= 50%)' if wfa_res.passed_wfa else '❌ FAILED'}")
    print(f"  • Assessment:                            {wfa_res.summary}")

    # GATE 3: Monte Carlo Reshuffle Simulation
    print("\n>>> GATE 3: MONTE CARLO SIMULATION (2,500 RESHUFFLES) <<<")
    mc_res = val_suite.run_monte_carlo(
        trades=result.trades,
        num_simulations=2500,
        ruin_threshold_pts=350.0
    )

    print(f"Monte Carlo Risk Distributions (2,500 Trade Sequence Permutations):")
    print(f"  • Median Maximum Drawdown:               {mc_res.median_max_dd_points:.1f} points")
    print(f"  • 95% Confidence Level Max Drawdown:     {mc_res.pct_95_max_dd_points:.1f} points")
    print(f"  • 99% Tail Risk Max Drawdown:            {mc_res.pct_99_max_dd_points:.1f} points")
    print(f"  • Worst-Case Observed Drawdown:          {mc_res.worst_case_dd_points:.1f} points")
    print(f"  • Probability of Ruin (DD >= 350 pts):   {mc_res.probability_of_ruin_pct:.2f}%")
    print(f"  • 95% Max Consecutive Loss Streak:       {mc_res.longest_losing_streak_95pct} trades")
    print(f"  • Final Points Distribution:             5th% = +{mc_res.final_points_5pct:.1f} pts | Median = +{mc_res.final_points_median:.1f} pts | 95th% = +{mc_res.final_points_95pct:.1f} pts")
    print(f"  • Monte Carlo Status:                    {'✅ PASSED (VaR95 < 350pts, Ruin <= 1%)' if mc_res.passed_monte_carlo else '❌ FAILED'}")
    print(f"  • Assessment:                            {mc_res.summary}")

    # FINAL INSTITUTIONAL VERDICT
    print("\n" + "=" * 80)
    print("🏆 STRATEGY 1 INSTITUTIONAL SCORECARD & VERDICT")
    print("=" * 80)
    all_passed = sens_res.is_robust_plateau and wfa_res.passed_wfa and mc_res.passed_monte_carlo
    print(f"  Gate 1 (Parameter Sensitivity Plateau): {'✅ PASSED' if sens_res.is_robust_plateau else '❌ FAILED'}")
    print(f"  Gate 2 (Walk-Forward Efficiency):       {'✅ PASSED' if wfa_res.passed_wfa else '❌ FAILED'}")
    print(f"  Gate 3 (Monte Carlo Risk of Ruin):       {'✅ PASSED' if mc_res.passed_monte_carlo else '❌ FAILED'}")
    print("-" * 80)
    if all_passed:
        print("🎉 OVERALL VERDICT: STRATEGY 1 OFFICIALLY VALIDATED & CLEARED FOR QUANT LAB DEPLOYMENT!")
    else:
        print("⚠️ OVERALL VERDICT: STRATEGY REQUIRES FINE-TUNING ON FAILED GATES.")
    print("=" * 80)

    # Save summary artifact / CSVs
    sens_res.parameter_grid.to_csv("strategy_renko/sensitivity_matrix.csv", index=False)
    print("\nSaved sensitivity matrix to strategy_renko/sensitivity_matrix.csv")

    return result, sens_res, wfa_res, mc_res


if __name__ == "__main__":
    run_strategy_1_lab()
