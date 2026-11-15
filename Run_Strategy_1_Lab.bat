@echo off
title Quant Lab - Strategy 1 (AI Renko Supertrend)
cls
echo ===============================================================================
echo        AUTONOMOUS QUANT LAB - STRATEGY #1 (AI RENKO SUPERTREND)
echo ===============================================================================
echo.
echo Running full institutional validation pipeline:
echo 1. Baseline Strategy 1 Backtest (Strict 5 Rules)
echo 2. Gate 1: Parameter Sensitivity Heatmap (Plateau vs Lonely Spike)
echo 3. Gate 2: 5-Fold Walk-Forward Analysis (WFA)
echo 4. Gate 3: Monte Carlo Reshuffle Simulation (2,500 iterations)
echo.
python run_strategy_1_lab.py
echo.
echo ===============================================================================
echo Pipeline completed. Press any key to exit.
pause >nul
