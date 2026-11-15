"""
simulate_strategy_1_with_original_conditions.py

Simulates Strategy 1 (AI Renko Supertrend) under the exact conditions of our original strategy:
Condition Set:
- Initial Capital: ₹1,00,000
- Max Concurrent Positions: 3
- Capital Allocation per Trade: 30% of portfolio equity (~₹30,000)
- Zerodha Indian Equity Delivery Friction Model (STT, Stamp Duty, GST, Exchange charges)
- Benchmark comparison against Original 4H Swing Golden Cross Strategy

Also evaluates Strategy 1 directly on NIFTY 50 starting with ₹1,00,000 capital.
"""

import sys
import os
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, time as dt_time
from tqdm import tqdm

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from strategy_renko.renko_engine import RenkoEngine
from strategy_renko.supertrend import calculate_supertrend
from strategy_renko.strategy_1_engine import RenkoSupertrendEngine
from backtester import EngineMechanics


def simulate_nifty_with_100k():
    """
    Simulates Strategy 1 on NIFTY 50 starting with exact ₹100,000 initial capital.
    Evaluates:
    A. Fixed 1 Lot (Conservative Margin ~₹25k-₹100k)
    B. Dynamic Compounding (1 Lot per ₹75,000 of equity)
    """
    data_path = "market_data_cache/TV_NIFTY_NSE_15M_5000.csv"
    if not os.path.exists(data_path):
        return None

    df = pd.read_csv(data_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values(by="timestamp").reset_index(drop=True)

    re = RenkoEngine(brick_size=25.0)
    bricks = re.build_bricks(df)
    st = calculate_supertrend(bricks, period=10, multiplier=2.0)

    sim = RenkoSupertrendEngine(symbol="NIFTY.NS", fixed_sl_points=25.0, lot_size=25)
    res = sim.run_backtest(st)

    initial_cap = 100000.0

    # Fixed 1 Lot Progression
    equity_fixed = [initial_cap]
    for t in res.trades:
        equity_fixed.append(equity_fixed[-1] + t.net_pnl)

    peak_fixed = np.maximum.accumulate(equity_fixed)
    dd_fixed = peak_fixed - equity_fixed
    max_dd_fixed_rs = float(np.max(dd_fixed))
    max_dd_fixed_pct = (max_dd_fixed_rs / np.max(peak_fixed)) * 100.0

    final_cap_fixed = equity_fixed[-1]
    net_profit_fixed = final_cap_fixed - initial_cap
    roi_fixed = (net_profit_fixed / initial_cap) * 100.0

    # Dynamic Compounding Sizing (1 Lot per ₹75,000 capital, min 1 lot)
    compounded_trades = []
    compounded_equity = [initial_cap]
    curr_cap = initial_cap

    for t in res.trades:
        lots = max(1, int(curr_cap // 75000.0))
        pts = t.points
        # Scale friction for lots
        friction = t.friction * lots
        pnl = (pts * 25 * lots) - friction
        curr_cap += pnl
        compounded_equity.append(curr_cap)
        compounded_trades.append({
            "trade_id": t.trade_id,
            "lots": lots,
            "points": pts,
            "pnl": pnl,
            "equity": curr_cap
        })

    peak_comp = np.maximum.accumulate(compounded_equity)
    dd_comp = peak_comp - compounded_equity
    max_dd_comp_rs = float(np.max(dd_comp))
    max_dd_comp_pct = (max_dd_comp_rs / np.max(peak_comp)) * 100.0

    final_cap_comp = compounded_equity[-1]
    net_profit_comp = final_cap_comp - initial_cap
    roi_comp = (net_profit_comp / initial_cap) * 100.0

    # Month-by-month breakdown for Nifty 100k
    df_trades = pd.DataFrame([{
        "exit_time": t.exit_time,
        "net_pnl": t.net_pnl,
        "points": t.points
    } for t in res.trades])
    df_trades["exit_time"] = pd.to_datetime(df_trades["exit_time"])
    df_trades["month"] = df_trades["exit_time"].dt.to_period("M").astype(str)
    monthly_nifty = df_trades.groupby("month").agg(
        trades=("points", "count"),
        points=("points", "sum"),
        net_pnl=("net_pnl", "sum")
    ).reset_index()

    return {
        "initial_cap": initial_cap,
        "total_trades": res.total_trades,
        "win_rate": res.win_rate,
        "profit_factor": res.profit_factor,
        "total_points": res.total_points,
        "fixed_1lot": {
            "final_cap": final_cap_fixed,
            "net_profit": net_profit_fixed,
            "roi_pct": roi_fixed,
            "max_dd_rs": max_dd_fixed_rs,
            "max_dd_pct": max_dd_fixed_pct
        },
        "compounded": {
            "final_cap": final_cap_comp,
            "net_profit": net_profit_comp,
            "roi_pct": roi_comp,
            "max_dd_rs": max_dd_comp_rs,
            "max_dd_pct": max_dd_comp_pct
        },
        "monthly": monthly_nifty
    }


def simulate_stock_universe_with_original_conditions():
    """
    Simulates Strategy 1 (Renko + Supertrend + 1:1 Break-Even Ratchet) across
    the top momentum universe / Nifty 500 stocks on 4H candles under the EXACT conditions:
    - Initial Capital: ₹100,000
    - Max Positions: 3 concurrent
    - Position Allocation: 30% per trade (~₹30,000)
    - Full Zerodha Delivery Friction (STT 0.1%, Txn 0.00345%, GST 18%, Stamp Duty)
    - Time Horizon: 2020-2026 (matching our original strategy benchmark)
    """
    print("\nLoading historical 4H candles for multi-stock universe from market_data.db...")
    
    # Top momentum leaders from original strategy
    test_symbols = [
        "TRENT.NS", "DIXON.NS", "HAL.NS", "BEL.NS", "ZOMATO.NS", "POLYCAB.NS",
        "BHEL.NS", "RVNL.NS", "IRFC.NS", "PFC.NS", "RECLTD.NS", "SUZLON.NS",
        "MCX.NS", "KALYANKJIL.NS", "PERSISTENT.NS", "COFORGE.NS", "CUMMINSIND.NS",
        "SHRIRAMFIN.NS", "CHOLAFIN.NS", "TVSMOTOR.NS", "BHARTIARTL.NS", "TITAN.NS",
        "TATAMOTORS.NS", "MAXHEALTH.NS", "AUROPHARMA.NS", "LUPIN.NS", "MOTHERSON.NS",
        "TORNTPOWER.NS", "SIEMENS.NS", "ABB.NS", "VEDL.NS", "HINDALCO.NS"
    ]

    stock_signals = {}
    all_timestamps = set()

    with sqlite3.connect("market_data.db") as conn:
        for sym in test_symbols:
            query = """
                SELECT timestamp, open, high, low, close, volume
                FROM market_data
                WHERE ticker = ? AND timeframe = '4H' AND timestamp >= '2020-01-01'
                ORDER BY timestamp ASC
            """
            df = pd.read_sql_query(query, conn, params=(sym,))
            if df.empty or len(df) < 100:
                continue

            df["timestamp"] = pd.to_datetime(df["timestamp"])

            # Build Adaptive Renko for each stock (Brick size = 1.5% of stock price)
            # In equities, % brick size creates stationary volatility-normalized bricks
            avg_price = df["close"].iloc[0]
            brick_size = max(1.0, round(avg_price * 0.015, 2))  # 1.5% brick size

            re = RenkoEngine(brick_size=brick_size)
            bricks = re.build_bricks(df)
            if len(bricks) < 30:
                continue

            st = calculate_supertrend(bricks, period=10, multiplier=2.0)

            # Map signals back to timestamp dict for portfolio execution
            # Brick close gives entry/exit
            st_dict = {}
            for _, r in st.iterrows():
                st_dict[r["timestamp"]] = {
                    "close": r["close"],
                    "high": r["high"],
                    "low": r["low"],
                    "signal": r["signal"],
                    "trend": r["trend"],
                    "brick_size": r["brick_size"]
                }
                all_timestamps.add(r["timestamp"])

            stock_signals[sym] = st_dict

    all_ts = sorted(list(all_timestamps))
    print(f"Loaded {len(stock_signals)} stocks across {len(all_ts)} timestamps.")

    # Execution Loop with EXACT Original Conditions
    initial_cap = 100000.0
    alloc_pct = 0.30
    max_positions = 3

    engine = EngineMechanics(initial_capital=initial_cap, max_positions=max_positions)
    available_cash = initial_cap
    open_positions = {}
    closed_trades = []
    equity_curve = []

    for ts in all_ts:
        # Calculate current equity
        current_equity = available_cash
        for s, pos in open_positions.items():
            if ts in stock_signals[s]:
                current_equity += pos["qty"] * stock_signals[s][ts]["close"]
            else:
                current_equity += pos["qty"] * pos["entry_price"]

        equity_curve.append({"timestamp": ts, "equity": current_equity})

        # 1. Process EXITS for open positions
        to_close = []
        for s, pos in open_positions.items():
            if ts not in stock_signals[s]:
                continue
            bar = stock_signals[s][ts]
            close = bar["close"]
            high = bar["high"]
            low = bar["low"]
            entry = pos["entry_price"]
            sl_dist = pos["sl_points"]

            exit_reason = None
            exit_price = None

            # Rule 4: 1:1 -> Move SL to Cost
            if not pos["sl_at_cost"]:
                if high >= entry + sl_dist:
                    pos["sl_at_cost"] = True
                    pos["current_sl"] = entry

            # Check SL Hit
            if low <= pos["current_sl"]:
                exit_price = pos["current_sl"]
                exit_reason = "Break_Even_SL" if pos["sl_at_cost"] else "Initial_SL"

            # Check Supertrend Bearish Reversal Flip
            elif bar["signal"] == -1:
                exit_price = close
                exit_reason = "Supertrend_Reversal"

            if exit_price is not None:
                gross_buy = pos["qty"] * entry
                gross_sell = pos["qty"] * exit_price
                friction = engine.calculate_friction(gross_buy, True) + engine.calculate_friction(gross_sell, False)
                net_pnl = (gross_sell - gross_buy) - friction
                pts_pct = ((exit_price - entry) / entry) * 100.0

                available_cash += (gross_sell - engine.calculate_friction(gross_sell, False))

                closed_trades.append({
                    "symbol": s,
                    "entry_time": pos["entry_time"],
                    "exit_time": ts,
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "pnl_pct": pts_pct,
                    "net_pnl": net_pnl,
                    "reason": exit_reason
                })
                to_close.append(s)

        for s in to_close:
            del open_positions[s]

        # 2. Process ENTRIES (Max 3 concurrent, 30% allocation)
        if len(open_positions) < max_positions and available_cash > 5000:
            for s, series in stock_signals.items():
                if len(open_positions) >= max_positions:
                    break
                if s in open_positions:
                    continue

                if ts in series:
                    bar = series[ts]
                    # Entry on Bullish Signal flip
                    if bar["signal"] == 1:
                        target_trade_val = min(current_equity * alloc_pct, available_cash)
                        if target_trade_val < 5000:
                            continue

                        entry_px = bar["close"] * 1.002  # 0.2% entry slippage
                        qty = int(target_trade_val // entry_px)
                        if qty < 1:
                            continue

                        actual_buy_val = qty * entry_px
                        buy_fric = engine.calculate_friction(actual_buy_val, True)

                        if available_cash >= (actual_buy_val + buy_fric):
                            available_cash -= (actual_buy_val + buy_fric)
                            b_size = bar["brick_size"]
                            open_positions[s] = {
                                "entry_time": ts,
                                "entry_price": entry_px,
                                "qty": qty,
                                "sl_points": b_size,
                                "current_sl": entry_px - b_size,
                                "sl_at_cost": False
                            }

    # Final summary calculations
    eq_series = [e["equity"] for e in equity_curve]
    peak_eq = np.maximum.accumulate(eq_series)
    drawdowns = peak_eq - eq_series
    max_dd_rs = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0
    max_dd_pct = (max_dd_rs / np.max(peak_eq)) * 100.0 if np.max(peak_eq) > 0 else 0.0

    final_equity = eq_series[-1] if eq_series else initial_cap
    total_net = final_equity - initial_cap
    roi_pct = (total_net / initial_cap) * 100.0

    wins = [t for t in closed_trades if t["net_pnl"] > 0]
    losses = [t for t in closed_trades if t["net_pnl"] <= 0]
    win_rate = (len(wins) / len(closed_trades) * 100.0) if closed_trades else 0.0

    gross_win = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else 999.0

    return {
        "initial_cap": initial_cap,
        "final_cap": final_equity,
        "total_net": total_net,
        "roi_pct": roi_pct,
        "max_dd_rs": max_dd_rs,
        "max_dd_pct": max_dd_pct,
        "total_trades": len(closed_trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "closed_trades": closed_trades
    }


if __name__ == "__main__":
    print("=" * 80)
    print("SIMULATING STRATEGY 1 UNDER EXACT ORIGINAL CONDITIONS (₹100,000 CAPITAL)")
    print("=" * 80)

    # 1. Nifty 50 with ₹100,000
    nifty_res = simulate_nifty_with_100k()
    if nifty_res:
        print("\n" + "-" * 80)
        print("PART A: STRATEGY 1 APPLIED TO NIFTY 50 (STARTING ₹100,000 CAPITAL)")
        print("-" * 80)
        print(f"  • Starting Capital:          ₹{nifty_res['initial_cap']:,.2f}")
        print(f"  • Total Trades:              {nifty_res['total_trades']}")
        print(f"  • Win Rate:                  {nifty_res['win_rate']:.1f}%")
        print(f"  • Profit Factor:             {nifty_res['profit_factor']:.2f}")
        print(f"  • Total Points:              +{nifty_res['total_points']:.1f} pts")
        print(f"  --- Mode 1: Conservative Fixed 1 Lot (25 shares) ---")
        f = nifty_res["fixed_1lot"]
        print(f"    - Final Capital:           ₹{f['final_cap']:,.2f}")
        print(f"    - Net Realized Profit:     ₹{f['net_profit']:,.2f} (+{f['roi_pct']:.1f}% ROI)")
        print(f"    - Max Portfolio Drawdown:  ₹{f['max_dd_rs']:,.2f} ({f['max_dd_pct']:.1f}%)")
        print(f"  --- Mode 2: Dynamic Compounding (1 Lot per ₹75k equity) ---")
        c = nifty_res["compounded"]
        print(f"    - Final Capital:           ₹{c['final_cap']:,.2f}")
        print(f"    - Net Realized Profit:     ₹{c['net_profit']:,.2f} (+{c['roi_pct']:.1f}% ROI)")
        print(f"    - Max Portfolio Drawdown:  ₹{c['max_dd_rs']:,.2f} ({c['max_dd_pct']:.1f}%)")

        print("\n  Monthly Return Sample (Nifty 1 Lot):")
        for _, row in nifty_res["monthly"].head(8).iterrows():
            print(f"    Month {row['month']}: {row['trades']:>2} trades | {row['points']:>+7.1f} pts | Net PnL: ₹{row['net_pnl']:>+10,.2f}")

    # 2. Multi-Stock Universe with ₹100,000 (3 Max Positions, 30% Alloc, Delivery Friction)
    stock_res = simulate_stock_universe_with_original_conditions()
    if stock_res:
        print("\n" + "-" * 80)
        print("PART B: STRATEGY 1 APPLIED TO MULTI-STOCK SWING PORTFOLIO (EXACT ORIGINAL SETUP)")
        print("-" * 80)
        print(f"  • Starting Capital:          ₹{stock_res['initial_cap']:,.2f}")
        print(f"  • Portfolio Structure:       Max 3 Positions, 30% Allocation per Trade (Cash Delivery)")
        print(f"  • Friction Model:            Zerodha Equity Delivery (STT, Stamp Duty, GST, Exchange)")
        print(f"  • Total Trades Executed:     {stock_res['total_trades']}")
        print(f"  • Win Rate:                  {stock_res['win_rate']:.1f}% ({stock_res['wins']} Wins / {stock_res['losses']} Losses)")
        print(f"  • Profit Factor:             {stock_res['profit_factor']:.2f}")
        print(f"  • Final Capital:             ₹{stock_res['final_cap']:,.2f}")
        print(f"  • Net Realized Profit:       ₹{stock_res['total_net']:,.2f} (+{stock_res['roi_pct']:.1f}% ROI)")
        print(f"  • Max Portfolio Drawdown:    ₹{stock_res['max_dd_rs']:,.2f} ({stock_res['max_dd_pct']:.1f}%)")

        # Exit reasons breakdown
        reasons = pd.Series([t["reason"] for t in stock_res["closed_trades"]]).value_counts()
        print("\n  Exit Reasons Breakdown:")
        for r, cnt in reasons.items():
            pct = (cnt / stock_res['total_trades']) * 100
            print(f"    • {r:<22}: {cnt:>4} trades ({pct:>5.1f}%)")

    print("\n" + "=" * 80)
    print("COMPARISON WITH ORIGINAL STRATEGY (GOLDEN CROSS 4H + CHANDELIER 2.3x)")
    print("=" * 80)
