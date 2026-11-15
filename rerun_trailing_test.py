"""
rerun_trailing_test.py
Re-runs the Trailing / Break-Even strategy under varying profit buffers
to show what happens when the stop is NOT choked prematurely.
"""

import sys
from strategy_optimizer import load_data_universe, simulate_strategy

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def test_trailing_variations():
    stock_dfs, all_timestamps = load_data_universe("2020-01-01")

    # Benchmark:
    # 1. Original premature choking at +3.5%
    # 2. Delayed break-even triggers (+5.0%, +6.0%, +7.0%, +8.0%)
    # 3. Completely unchoked (0.0% - letting the 2.5x ATR target work)
    # 4. Delayed break-even combined with Rotation Engine
    test_cases = [
        {"name": "Choked Trailing (Original)", "be": 3.5, "rot": False},
        {"name": "Delayed BE (+5.0% Trigger)", "be": 5.0, "rot": False},
        {"name": "Delayed BE (+6.0% Trigger)", "be": 6.0, "rot": False},
        {"name": "Delayed BE (+7.0% Trigger)", "be": 7.0, "rot": False},
        {"name": "Delayed BE (+8.0% Trigger)", "be": 8.0, "rot": False},
        {"name": "Unchoked (Pure 2.5x ATR)", "be": 0.0, "rot": False},
        {"name": "Unchoked + Rotation Engine", "be": 0.0, "rot": True},
        {"name": "Delayed BE (+6%) + Rotation", "be": 6.0, "rot": True},
    ]

    results = []
    for tc in test_cases:
        res = simulate_strategy(
            stock_dfs=stock_dfs,
            all_timestamps=all_timestamps,
            initial_cap=100000.0,
            max_alloc_pct=0.30,
            min_alloc_pct=0.15,
            max_positions=3,
            tp_mult=2.5,
            sl_mult=2.0,
            be_profit_trigger=tc['be'],
            stagnation_bars=20 if tc['rot'] else 0,
            enable_rotation=tc['rot'],
            strategy_name=tc['name']
        )
        results.append(res)

    print("\n" + "="*110)
    print("TRAILING STOP-LOSS SENSITIVITY TEST: IMPACT OF NOT CHOKING TRADES PREMATURELY")
    print("="*110)
    header = f"{'Strategy Configuration':<32} {'Initial Cap':<13} {'Net PnL (₹)':<16} {'Ending Capital':<17} {'WinRate':<9} {'ProfitFac':<11} {'MaxDD'}"
    print(header)
    print("-"*110)

    for r in results:
        end_cap = 100000.0 + r['total_net_pnl']
        sign = "+" if r['total_net_pnl'] >= 0 else ""
        print(
            f"{r['strategy']:<32} "
            f"₹1,00,000     "
            f"{sign}₹{r['total_net_pnl']:>10,.2f}      "
            f"₹{end_cap:>10,.2f}       "
            f"{r['win_rate']:>5.1f}%    "
            f"{r['profit_factor']:>6.2f}      "
            f"{r['max_drawdown_pct']:>5.1f}%"
        )
    print("="*110 + "\n")

if __name__ == "__main__":
    test_trailing_variations()

