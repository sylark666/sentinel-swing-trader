"""
analyze_champion_strategy.py
Deep-dive into the high-profit swing trading engine:
TP: 2.5x ATR, SL: 2.0x ATR, No artificial BE choking, dynamic capital sizing.
Analyzes month-by-month profit, win rate, Sharpe ratio, and drawdowns.
"""

import sys
import pandas as pd
import numpy as np

from test_timeframes_and_mechanics import load_timeframe_data, backtest_engine

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def inspect_champion():
    print("Loading 1D daily momentum data (2020-2026)...")
    dfs_1d, ts_1d = load_timeframe_data("1D", "2020-01-01")

    # Test allocations: 25% (4 positions), 30% (3 positions), 33% (3 positions)
    allocations = [0.25, 0.30, 0.33]

    for alloc in allocations:
        res = backtest_engine(
            stock_dfs=dfs_1d,
            all_timestamps=ts_1d,
            timeframe="1D",
            initial_cap=100000.0,
            max_alloc_pct=alloc,
            min_alloc_pct=0.15,
            max_positions=int(1 // alloc),
            tp_mult=2.5,
            sl_mult=2.0,
            be_profit_pct=0.0,      # Natural volatility buffer
            stagnation_bars=0,      # Give trades room to reach target
            enable_rotation=False
        )

        print("\n" + "="*80)
        print(f"STRATEGY PERFORMANCE: ALLOCATION = {int(alloc*100)}% PER TRADE (MAX {int(1//alloc)} TRADES)")
        print("="*80)
        print(f"  ► Initial Capital       : ₹1,00,000")
        print(f"  ► Total Net Profit      : ₹{res['total_net']:,.2f} (+{res['roi_pct']}%)")
        print(f"  ► Average Monthly Return: +{res['avg_monthly']}% / month")
        print(f"  ► Win Rate              : {res['win_rate']}%")
        print(f"  ► Profit Factor         : {res['profit_factor']}")
        print(f"  ► Max Drawdown          : {res['max_dd']}%")
        print(f"  ► Months >= 3% Target   : {res['months_over_3pct']} / {res['total_months']} ({res['consistency_rate']}%)")
        print(f"  ► Total Trades Taken    : {res['total_trades']}")

        # Monthly return table for 30% allocation
        if alloc == 0.30:
            print("\n" + "-"*50)
            print("MONTH-BY-MONTH NET RETURN BREAKDOWN (30% ALLOC)")
            print("-"*50)
            breakdown = res['monthly_breakdown']
            target_met = 0
            profitable_months = 0
            for m, pct in breakdown.items():
                if pct >= 3.0:
                    status = "✅ TARGET MET"
                    target_met += 1
                    profitable_months += 1
                elif pct > 0:
                    status = "⚠️ PROFITABLE (<3%)"
                    profitable_months += 1
                else:
                    status = "🔴 NEGATIVE"
                print(f"   {m}: {pct:>+6.2f}%   [{status}]")

            print("\nSummary Statistics:")
            print(f"Total Months: {len(breakdown)}")
            print(f"Profitable Months: {profitable_months}/{len(breakdown)} ({profitable_months/len(breakdown)*100:.1f}%)")
            print(f"Months >= 3% Target: {target_met}/{len(breakdown)} ({target_met/len(breakdown)*100:.1f}%)")

if __name__ == "__main__":
    inspect_champion()

