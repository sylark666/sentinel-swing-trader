"""
strategy_renko/renko_dashboard_adapter.py
=========================================
Adapter module that interfaces the AI Renko Supertrend Champion engine
with the Streamlit Web Console (dashboard.py).
Provides:
1. Multi-stock Renko Supertrend portfolio simulation with Plotly curve & trade ledger.
2. Real-time Renko Supertrend opportunity screener.
"""

import os
import sys
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime
from typing import List, Dict, Optional, Callable

from strategy_renko.renko_engine import RenkoEngine
from strategy_renko.supertrend import calculate_supertrend
from database import load_candles_from_db, get_connection
import portfolio_manager as pm


def simulate_renko_supertrend_portfolio(
    symbols: List[str],
    timeframe: str = "1D",
    brick_pct: float = 0.025,
    st_period: int = 10,
    st_multiplier: float = 2.0,
    initial_cap: float = 100000.0,
    monthly_sip: float = 5000.0,
    max_positions: int = 8,
    use_be_ratchet: bool = True,
    progress_callback: Optional[Callable] = None
) -> Dict:
    """
    Executes a multi-stock Renko Supertrend compounding portfolio simulation.
    Strictly enforces:
    - 2-brick reversal logic
    - Supertrend direction alignment
    - 1:1 / +2 Bricks Break-Even ratchet
    - Indian regulatory friction (STT, GST, brokerage, stamp duty)
    - Monthly SIP injection
    """
    if not symbols:
        return {}

    stock_renko = {}
    all_timestamps = set()
    total_syms = len(symbols)

    for idx, sym in enumerate(symbols):
        if progress_callback:
            progress_callback((idx / total_syms) * 0.5, f"Building Renko Bricks: {sym} ({idx+1}/{total_syms})")

        clean_sym = sym.replace("^NSEI", "NIFTY")
        df = load_candles_from_db(clean_sym, timeframe)
        if df.empty or len(df) < 50:
            if timeframe != "4H":
                df = load_candles_from_db(clean_sym, "4H")
        if df.empty or len(df) < 50:
            continue

        df = df.sort_values(by="timestamp").reset_index(drop=True)
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"])

        avg_price = df["close"].iloc[0]
        b_size = max(0.5, round(avg_price * brick_pct, 2))

        try:
            re = RenkoEngine(brick_size=b_size)
            bricks = re.build_bricks(df)
            if len(bricks) < st_period + 5:
                continue

            st = calculate_supertrend(bricks, period=st_period, multiplier=st_multiplier)
            st_dict = {}
            for _, r in st.iterrows():
                ts = r["timestamp"]
                all_timestamps.add(ts)
                st_dict[ts] = {
                    "close": float(r["close"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "signal": int(r["signal"]),
                    "trend": int(r["trend"]),
                    "brick_size": b_size,
                    "supertrend": float(r.get("supertrend", 0.0))
                }
            stock_renko[sym] = st_dict
        except Exception:
            continue

    if not stock_renko:
        return {}

    all_ts = sorted(list(all_timestamps))
    alloc_pct = 1.0 / max_positions
    cash = float(initial_cap)
    total_invested = float(initial_cap)
    positions = {}
    trades = []
    equity_curve = []
    equity_dates = []
    last_month = None

    trade_id = 1
    total_ts = len(all_ts)

    for t_idx, ts in enumerate(all_ts):
        if progress_callback and t_idx % 20 == 0:
            progress_callback(0.5 + (t_idx / total_ts) * 0.48, f"Simulating Portfolio Execution: {ts.strftime('%Y-%m-%d')}")

        # Monthly SIP injection
        cur_m = (ts.year, ts.month)
        if last_month is not None and cur_m != last_month and monthly_sip > 0:
            cash += monthly_sip
            total_invested += monthly_sip
        last_month = cur_m

        # Compute current portfolio valuation
        curr_eq = cash + sum(
            p["qty"] * stock_renko[s][ts]["close"] if (s in stock_renko and ts in stock_renko[s]) else p["qty"] * p["entry_px"]
            for s, p in positions.items()
        )
        equity_curve.append(round(curr_eq, 2))
        equity_dates.append(ts)

        # 1. Update Exits
        to_close = []
        for s, p in positions.items():
            if s not in stock_renko or ts not in stock_renko[s]:
                continue
            b = stock_renko[s][ts]
            close = b["close"]
            high = b["high"]
            low = b["low"]
            entry = p["entry_px"]
            b_size = p["brick_size"]
            p["days_held"] = (ts - p["entry_date"]).total_seconds() / 86400.0

            # Break-even ratchet at +2 bricks
            if use_be_ratchet and not p["be_locked"] and high >= (entry + (2.0 * b_size)):
                p["be_locked"] = True
                p["sl"] = entry * 1.002  # Cover friction

            exit_px = None
            reason = None

            if low <= p["sl"]:
                exit_px = p["sl"]
                reason = "Break_Even_Ratchet_Hit" if p["be_locked"] else "Initial_Brick_SL"
            elif b["signal"] == -1:
                exit_px = close
                reason = "Supertrend_Reversal"

            if exit_px is not None:
                qty = p["qty"]
                gross_b = qty * entry
                gross_s = qty * exit_px
                friction = pm.calculate_friction(gross_b, is_buy=True) + pm.calculate_friction(gross_s, is_buy=False)
                net = (gross_s - gross_b) - friction
                cash += (gross_s - pm.calculate_friction(gross_s, is_buy=False))

                trades.append({
                    "Trade ID": trade_id,
                    "ticker": s,
                    "Alpha Score": "130",
                    "entry_date": p["entry_date"],
                    "entry_price": round(entry, 2),
                    "exit_date": ts,
                    "exit_price": round(exit_px, 2),
                    "gross_buy": round(gross_b, 2),
                    "gross_sell": round(gross_s, 2),
                    "net_pnl": round(net, 2),
                    "pnl_pct": round(((exit_px - entry) / entry) * 100.0, 2),
                    "max_runup_pct": round(((high - entry) / entry) * 100.0, 2),
                    "days_held": round(p["days_held"], 1),
                    "reason": reason
                })
                trade_id += 1
                to_close.append(s)

        for s in to_close:
            del positions[s]

        # 2. Check Entries
        can_open = len(positions) < max_positions and cash >= 5000.0
        if can_open:
            for s, series in stock_renko.items():
                if len(positions) >= max_positions:
                    break
                if s in positions or ts not in series:
                    continue
                b = series[ts]
                if b["signal"] == 1:
                    target_alloc = min(curr_eq * alloc_pct, cash)
                    if target_alloc < 5000.0:
                        continue
                    entry_px = b["close"] * 1.002  # slippage
                    qty = int(target_alloc // entry_px)
                    if qty <= 0:
                        continue
                    gross_b = qty * entry_px
                    friction = pm.calculate_friction(gross_b, is_buy=True)
                    if cash >= (gross_b + friction):
                        cash -= (gross_b + friction)
                        b_sz = b["brick_size"]
                        positions[s] = {
                            "entry_date": ts,
                            "entry_px": entry_px,
                            "qty": qty,
                            "brick_size": b_sz,
                            "sl": round(entry_px - b_sz, 2),
                            "be_locked": False,
                            "days_held": 0.0
                        }

    if progress_callback:
        progress_callback(1.0, "Simulation Finalized!")

    df_trades = pd.DataFrame(trades)
    final_eq = equity_curve[-1] if equity_curve else initial_cap
    total_net_pnl = final_eq - total_invested
    roi_pct = (total_net_pnl / total_invested) * 100.0 if total_invested > 0 else 0.0

    if not df_trades.empty:
        wins = df_trades[df_trades["net_pnl"] > 0]
        losses = df_trades[df_trades["net_pnl"] <= 0]
        win_rate = (len(wins) / len(df_trades)) * 100.0
        gross_w = wins["net_pnl"].sum()
        gross_l = abs(losses["net_pnl"].sum())
        pf = (gross_w / gross_l) if gross_l > 0 else 999.0
        avg_w = wins["pnl_pct"].mean() if not wins.empty else 0.0
        avg_l = losses["pnl_pct"].mean() if not losses.empty else 0.0
        max_w = df_trades["pnl_pct"].max()
        avg_hold = df_trades["days_held"].mean()
    else:
        win_rate = 0.0
        pf = 0.0
        avg_w = 0.0
        avg_l = 0.0
        max_w = 0.0
        avg_hold = 0.0

    # Max Drawdown
    if equity_curve:
        eq_s = pd.Series(equity_curve)
        peak = eq_s.cummax()
        dd = (eq_s - peak) / peak
        max_dd = abs(float(dd.min())) * 100.0
    else:
        max_dd = 0.0

    return {
        "total_trades": len(df_trades),
        "total_net_pnl": round(total_net_pnl, 2),
        "total_invested": round(total_invested, 2),
        "roi_pct": round(roi_pct, 2),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(pf, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "avg_win_pct": round(avg_w, 2),
        "avg_loss_pct": round(avg_l, 2),
        "max_single_win_pct": round(max_w, 2),
        "avg_hold_days": round(avg_hold, 1),
        "trades_df": df_trades,
        "equity_curve": equity_curve,
        "equity_dates": equity_dates
    }


def scan_renko_supertrend_opportunities(
    symbols: List[str],
    timeframe: str = "1D",
    brick_pct: float = 0.025,
    st_period: int = 10,
    st_multiplier: float = 2.0
) -> List[Dict]:
    """
    Scans the selected universe for live Renko Supertrend Buy Flips (signal == 1).
    """
    candidates = []
    for sym in symbols:
        clean = sym.replace("^NSEI", "NIFTY")
        df = load_candles_from_db(clean, timeframe)
        if df.empty or len(df) < 50:
            if timeframe != "4H":
                df = load_candles_from_db(clean, "4H")
        if df.empty or len(df) < 50:
            continue
        df = df.sort_values(by="timestamp").reset_index(drop=True)

        avg_price = df["close"].iloc[-1]
        b_size = max(0.5, round(avg_price * brick_pct, 2))

        try:
            re = RenkoEngine(brick_size=b_size)
            bricks = re.build_bricks(df)
            if len(bricks) < st_period + 5:
                continue
            st = calculate_supertrend(bricks, period=st_period, multiplier=st_multiplier)
            latest = st.iloc[-1].to_dict()

            # Buy flip or strong green trend
            if latest.get("trend") == 1:
                is_fresh_flip = (latest.get("signal") == 1)
                c = float(latest["close"])
                sl = round(c - b_size, 2)
                risk_pct = round(((c - sl) / c) * 100.0, 1)

                candidates.append({
                    "ticker": sym,
                    "price": c,
                    "brick_size": b_size,
                    "stop_loss": sl,
                    "risk_pct": risk_pct,
                    "supertrend": round(float(latest.get("supertrend", sl)), 2),
                    "is_fresh_flip": is_fresh_flip,
                    "alpha_score": 130.0 if is_fresh_flip else 115.0,
                    "reasons": "Renko Supertrend Bullish Flip (Fresh Signal)" if is_fresh_flip else "Renko Supertrend Green Trend Active"
                })
        except Exception:
            continue

    candidates.sort(key=lambda x: (x["is_fresh_flip"], x["alpha_score"]), reverse=True)
    return candidates
