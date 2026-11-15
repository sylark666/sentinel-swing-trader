"""
portfolio_manager.py
====================
Manages the real-world trade tracking database, account ledger, fractional slots,
partial profit scale-outs, long-term share retention (carve-out), and undo support.
Uses SQLite for robust, zero-dependency persistence.
"""

import sqlite3
import json
import logging
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import pandas as pd

DB_FILE = "portfolio_tracker.db"
DEFAULT_ALLOC_PCT = 0.30  # 30% per full swing trade slot
MAX_SLOTS = 3.0           # Maximum 3 concurrent full slots

logger = logging.getLogger("PortfolioManager")


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_portfolio_db(initial_capital: float = 100000.0):
    """Initializes all necessary tables for live portfolio tracking."""
    with get_db() as conn:
        cursor = conn.cursor()

        # 1. Portfolio Account Ledger
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_ledger (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                total_capital REAL NOT NULL,
                cash_available REAL NOT NULL,
                invested_capital REAL NOT NULL,
                total_deposits REAL NOT NULL,
                total_withdrawals REAL NOT NULL,
                updated_at DATETIME NOT NULL
            );
        """)

        # 2. Capital Injections & Withdrawals Ledger
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS capital_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                type TEXT NOT NULL,
                amount REAL NOT NULL,
                note TEXT
            );
        """)

        # 3. Active Swing Trades
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_swing_trades (
                ticker TEXT PRIMARY KEY,
                entry_date DATETIME NOT NULL,
                entry_price REAL NOT NULL,
                qty INTEGER NOT NULL,
                initial_stop REAL NOT NULL,
                current_trailing_stop REAL NOT NULL,
                max_high REAL NOT NULL,
                chandelier_stage INTEGER NOT NULL DEFAULT 1,
                alpha_score REAL NOT NULL,
                effective_slot REAL NOT NULL DEFAULT 1.0,
                status TEXT NOT NULL DEFAULT 'ACTIVE'
            );
        """)

        # 4. Closed Trades Historical Ledger
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS closed_trades (
                trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                entry_date DATETIME NOT NULL,
                exit_date DATETIME NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                qty INTEGER NOT NULL,
                gross_buy REAL NOT NULL,
                gross_sell REAL NOT NULL,
                friction REAL NOT NULL,
                net_pnl REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                holding_days REAL NOT NULL,
                exit_reason TEXT NOT NULL
            );
        """)

        # 5. Long-Term Vault (Carved-out shares kept forever)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS long_term_vault (
                ticker TEXT PRIMARY KEY,
                transfer_date DATETIME NOT NULL,
                qty INTEGER NOT NULL,
                original_entry_price REAL NOT NULL,
                notes TEXT
            );
        """)

        # 6. Audit Log for Full Undo Support
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                timestamp DATETIME NOT NULL
            );
        """)

        # Initialize ledger if not already initialized
        cursor.execute("SELECT COUNT(*) FROM portfolio_ledger")
        if cursor.fetchone()[0] == 0:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("""
                INSERT INTO portfolio_ledger 
                (id, total_capital, cash_available, invested_capital, total_deposits, total_withdrawals, updated_at)
                VALUES (1, ?, ?, 0.0, ?, 0.0, ?)
            """, (initial_capital, initial_capital, initial_capital, now))
            
            cursor.execute("""
                INSERT INTO capital_transactions (timestamp, type, amount, note)
                VALUES (?, 'DEPOSIT', ?, 'Initial Account Opening Balance')
            """, (now, initial_capital))

        conn.commit()


def calculate_friction(gross_val: float, is_buy: bool) -> float:
    """Estimated Indian regulatory friction (STT, GST, SEBI, stamp duty, slippage)."""
    brokerage = min(20.0, gross_val * 0.0003)
    stt = 0.0 if is_buy else gross_val * 0.001
    turnover = gross_val * 0.0000345
    gst = (brokerage + turnover) * 0.18
    stamp = (gross_val * 0.00015) if is_buy else 0.0
    sebi = gross_val * 0.000001
    slip = gross_val * 0.002
    return round(brokerage + stt + turnover + gst + stamp + sebi + slip, 2)


def get_portfolio_summary() -> Dict:
    """Returns live cash, invested capital, total equity, and slot utilization."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM portfolio_ledger WHERE id = 1")
        ledger = dict(cursor.fetchone())

        cursor.execute("SELECT * FROM active_swing_trades WHERE status != 'CLOSED'")
        active_rows = [dict(r) for r in cursor.fetchall()]

        cursor.execute("SELECT COUNT(*), SUM(qty) FROM long_term_vault")
        vault_stats = cursor.fetchone()
        vault_unique = vault_stats[0] or 0
        vault_qty = vault_stats[1] or 0

        # Calculate current invested capital and effective slots
        total_invested = sum(r['qty'] * r['entry_price'] for r in active_rows)
        effective_slots_occupied = sum(r.get('effective_slot', 1.0) for r in active_rows)
        effective_slots_available = max(0.0, MAX_SLOTS - effective_slots_occupied)

        total_equity = ledger['cash_available'] + total_invested
        slot_target_allocation = total_equity * DEFAULT_ALLOC_PCT

        # Update ledger cache
        cursor.execute("""
            UPDATE portfolio_ledger 
            SET total_capital = ?, invested_capital = ?, updated_at = ?
            WHERE id = 1
        """, (total_equity, total_invested, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()

        return {
            "total_capital": round(total_equity, 2),
            "cash_available": round(ledger['cash_available'], 2),
            "invested_capital": round(total_invested, 2),
            "cash_pct": round((ledger['cash_available'] / total_equity) * 100, 1) if total_equity > 0 else 0,
            "slot_target_allocation": round(slot_target_allocation, 2),
            "effective_slots_occupied": round(effective_slots_occupied, 1),
            "effective_slots_available": round(effective_slots_available, 1),
            "active_positions_count": len(active_rows),
            "can_take_new_trade": (effective_slots_available >= 1.0) and (ledger['cash_available'] >= (slot_target_allocation * 0.5)),
            "vault_stocks_count": vault_unique,
            "vault_total_qty": vault_qty
        }


def get_active_trades() -> List[Dict]:
    """Retrieves all currently active swing trades."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM active_swing_trades ORDER BY entry_date DESC")
        return [dict(r) for r in cursor.fetchall()]


def log_buy_trade(ticker: str, qty: int, price: float, stop_loss: float, alpha_score: float = 100.0) -> Tuple[bool, str]:
    """
    Logs an executed buy trade.
    Checks cash balance, calculates initial safety stop, occupies 1 slot.
    """
    ticker = ticker.upper().strip()
    if not ticker.endswith(".NS") and ticker != "NIFTY":
        ticker = f"{ticker}.NS"

    gross_cost = qty * price
    friction = calculate_friction(gross_cost, is_buy=True)
    total_cost = gross_cost + friction

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT cash_available, total_capital FROM portfolio_ledger WHERE id = 1")
        ledger = cursor.fetchone()
        cash = ledger['cash_available']

        if total_cost > cash:
            return False, f"🛑 Insufficient Cash: Required ₹{total_cost:,.2f}, Available: ₹{cash:,.2f}"

        # Check if already open
        cursor.execute("SELECT qty, entry_price FROM active_swing_trades WHERE ticker = ?", (ticker,))
        existing = cursor.fetchone()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if existing:
            # Scale-in: Calculate Volume-Weighted Average Price (VWAP)
            old_qty = existing['qty']
            old_price = existing['entry_price']
            new_qty = old_qty + qty
            vwap = round(((old_qty * old_price) + (qty * price)) / new_qty, 2)

            cursor.execute("""
                UPDATE active_swing_trades 
                SET qty = ?, entry_price = ?, initial_stop = MIN(initial_stop, ?), current_trailing_stop = MIN(current_trailing_stop, ?), effective_slot = 1.0
                WHERE ticker = ?
            """, (new_qty, vwap, stop_loss, stop_loss, ticker))

            action_type = "SCALE_IN"
            action_payload = {"ticker": ticker, "added_qty": qty, "price": price, "total_cost": total_cost}
            msg = f"✅ Scaled into `{ticker}`: Added {qty} shares @ ₹{price:,.2f}. New Holding: {new_qty} shares @ VWAP ₹{vwap:,.2f}."
        else:
            cursor.execute("""
                INSERT INTO active_swing_trades 
                (ticker, entry_date, entry_price, qty, initial_stop, current_trailing_stop, max_high, chandelier_stage, alpha_score, effective_slot, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 1.0, 'ACTIVE')
            """, (ticker, now, price, qty, stop_loss, stop_loss, price, alpha_score))

            action_type = "BUY"
            action_payload = {"ticker": ticker, "qty": qty, "price": price, "total_cost": total_cost, "stop_loss": stop_loss}
            msg = f"✅ Logged `{ticker}`: Bought {qty} shares @ ₹{price:,.2f} (Total: ₹{total_cost:,.2f}). Stop Loss: ₹{stop_loss:,.2f}."

        # Deduct cash
        cursor.execute("UPDATE portfolio_ledger SET cash_available = cash_available - ? WHERE id = 1", (total_cost,))

        # Record in audit log for undo
        cursor.execute("INSERT INTO audit_log (action_type, payload_json, timestamp) VALUES (?, ?, ?)",
                       (action_type, json.dumps(action_payload), now))

        conn.commit()
        return True, msg


def log_partial_sell(ticker: str, qty_sold: int, exit_price: float, custom_reason: Optional[str] = None) -> Tuple[bool, str]:
    """
    Logs a partial profit scale-out.
    Realizes profit on sold shares, returns cash, sets remaining shares to proportional effective slot (runner).
    Allows multiple sequential scale-outs (e.g. 10%, 20%, 25%, 33%, 50%).
    """
    ticker = ticker.upper().strip()
    if not ticker.endswith(".NS") and ticker != "NIFTY":
        ticker = f"{ticker}.NS"

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM active_swing_trades WHERE ticker = ?", (ticker,))
        pos = cursor.fetchone()

        if not pos:
            return False, f"🛑 No active position found for `{ticker}`."

        curr_qty = pos['qty']
        if qty_sold >= curr_qty:
            return False, f"🛑 Quantity to sell ({qty_sold}) must be less than current holding ({curr_qty}). For full exit, use full sell."

        remaining_qty = curr_qty - qty_sold
        entry_price = pos['entry_price']
        pct_of_pos_sold = round((qty_sold / curr_qty) * 100, 1)

        # Proportional slot scaling (capped at min 0.1, max 1.0)
        curr_slot = pos.get('effective_slot', 1.0)
        new_effective_slot = max(0.1, round(curr_slot * (remaining_qty / curr_qty), 2))

        gross_buy = qty_sold * entry_price
        gross_sell = qty_sold * exit_price
        friction = calculate_friction(gross_sell, is_buy=False) + calculate_friction(gross_buy, is_buy=True)
        net_pnl = (gross_sell - gross_buy) - friction
        pnl_pct = (net_pnl / gross_buy) * 100
        cash_credited = gross_sell - calculate_friction(gross_sell, is_buy=False)

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Record realized portion in closed trades
        entry_dt = pd.to_datetime(pos['entry_date'])
        holding_days = (pd.to_datetime(now) - entry_dt).total_seconds() / 86400.0

        exit_label = custom_reason or f"Partial Profit Scale-Out ({pct_of_pos_sold:.0f}%)"

        cursor.execute("""
            INSERT INTO closed_trades 
            (ticker, entry_date, exit_date, entry_price, exit_price, qty, gross_buy, gross_sell, friction, net_pnl, pnl_pct, holding_days, exit_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ticker, pos['entry_date'], now, entry_price, exit_price, qty_sold, gross_buy, gross_sell, friction, round(net_pnl, 2), round(pnl_pct, 2), round(holding_days, 1), exit_label))

        # Update active holding with remaining shares and mark proportional effective_slot
        cursor.execute("""
            UPDATE active_swing_trades 
            SET qty = ?, effective_slot = ?, status = 'PARTIAL_RUNNER'
            WHERE ticker = ?
        """, (remaining_qty, new_effective_slot, ticker))

        # Credit cash
        cursor.execute("UPDATE portfolio_ledger SET cash_available = cash_available + ? WHERE id = 1", (cash_credited,))

        # Audit log
        payload = {"ticker": ticker, "qty_sold": qty_sold, "exit_price": exit_price, "cash_credited": cash_credited, "remaining_qty": remaining_qty, "effective_slot": new_effective_slot}
        cursor.execute("INSERT INTO audit_log (action_type, payload_json, timestamp) VALUES ('PARTIAL_SELL', ?, ?)", (json.dumps(payload), now))

        conn.commit()

        return True, (
            f"✅ **Partial Profit Booked for `{ticker}`!**\n"
            f"• Sold: **{qty_sold} shares** @ ₹{exit_price:,.2f} ({pct_of_pos_sold:.0f}% of position)\n"
            f"• Net Profit Realized: **₹{net_pnl:,.2f} ({pnl_pct:+.2f}%)**\n"
            f"• Cash Returned to Pool: ₹{cash_credited:,.2f}\n"
            f"• Remaining: **{remaining_qty} shares** trailing as a **{new_effective_slot:.2f} Effective Slot Runner**."
        )


def log_full_sell(ticker: str, exit_price: float, reason: str = "Chandelier Trend Reversal", retain_qty: int = 0) -> Tuple[bool, str]:
    """
    Logs full swing exit.
    If retain_qty > 0, transfers retained shares to long_term_vault and frees swing slot completely.
    """
    ticker = ticker.upper().strip()
    if not ticker.endswith(".NS") and ticker != "NIFTY":
        ticker = f"{ticker}.NS"

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM active_swing_trades WHERE ticker = ?", (ticker,))
        pos = cursor.fetchone()

        if not pos:
            return False, f"🛑 No active position found for `{ticker}`."

        curr_qty = pos['qty']
        if retain_qty >= curr_qty:
            return False, f"🛑 Retained shares ({retain_qty}) cannot be greater than or equal to current holding ({curr_qty})."

        qty_to_sell = curr_qty - retain_qty
        entry_price = pos['entry_price']

        gross_buy = qty_to_sell * entry_price
        gross_sell = qty_to_sell * exit_price
        friction = calculate_friction(gross_sell, is_buy=False) + calculate_friction(gross_buy, is_buy=True)
        net_pnl = (gross_sell - gross_buy) - friction
        pnl_pct = (net_pnl / gross_buy) * 100
        cash_credited = gross_sell - calculate_friction(gross_sell, is_buy=False)

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry_dt = pd.to_datetime(pos['entry_date'])
        holding_days = (pd.to_datetime(now) - entry_dt).total_seconds() / 86400.0

        # 1. Record completed swing trade in closed_trades
        full_reason = f"{reason} (Kept {retain_qty} in Vault)" if retain_qty > 0 else reason
        cursor.execute("""
            INSERT INTO closed_trades 
            (ticker, entry_date, exit_date, entry_price, exit_price, qty, gross_buy, gross_sell, friction, net_pnl, pnl_pct, holding_days, exit_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ticker, pos['entry_date'], now, entry_price, exit_price, qty_to_sell, gross_buy, gross_sell, friction, round(net_pnl, 2), round(pnl_pct, 2), round(holding_days, 1), full_reason))

        # 2. If retaining shares forever, transfer to long_term_vault
        if retain_qty > 0:
            cursor.execute("""
                INSERT INTO long_term_vault (ticker, transfer_date, qty, original_entry_price, notes)
                VALUES (?, ?, ?, ?, 'Carved out from Swing Trade')
                ON CONFLICT(ticker) DO UPDATE SET qty = qty + excluded.qty
            """, (ticker, now, retain_qty, entry_price))

        # 3. Delete from active_swing_trades (Frees the swing slot 100%)
        cursor.execute("DELETE FROM active_swing_trades WHERE ticker = ?", (ticker,))

        # 4. Credit cash to ledger
        cursor.execute("UPDATE portfolio_ledger SET cash_available = cash_available + ? WHERE id = 1", (cash_credited,))

        # 5. Audit log
        payload = {"ticker": ticker, "sold_qty": qty_to_sell, "retain_qty": retain_qty, "exit_price": exit_price, "cash_credited": cash_credited, "net_pnl": net_pnl}
        cursor.execute("INSERT INTO audit_log (action_type, payload_json, timestamp) VALUES ('FULL_SELL', ?, ?)", (json.dumps(payload), now))

        conn.commit()

        vault_msg = f"\n• Transferred **{retain_qty} shares** to your **Long-Term Vault** (Zero cost basis, no further alerts)." if retain_qty > 0 else ""

        return True, (
            f"✅ **Swing Trade Closed for `{ticker}`!**\n"
            f"• Sold: {qty_to_sell} shares @ ₹{exit_price:,.2f}\n"
            f"• Realized Net PnL: **₹{net_pnl:,.2f} ({pnl_pct:+.2f}%)**\n"
            f"• Holding Duration: {holding_days:.1f} days\n"
            f"• Cash Returned: ₹{cash_credited:,.2f}{vault_msg}\n"
            f"• **Swing Slot 100% UNLOCKED** for next candidate."
        )


def deposit_capital(amount: float, note: str = "Capital Deposit / Monthly SIP") -> Tuple[bool, str]:
    """Adds fresh capital to the cash pool and recalculates dynamic slot sizes."""
    if amount <= 0:
        return False, "🛑 Deposit amount must be positive."

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE portfolio_ledger 
            SET cash_available = cash_available + ?, total_deposits = total_deposits + ?, updated_at = ?
            WHERE id = 1
        """, (amount, amount, now))

        cursor.execute("INSERT INTO capital_transactions (timestamp, type, amount, note) VALUES (?, 'DEPOSIT', ?, ?)",
                       (now, amount, note))

        payload = {"amount": amount, "note": note}
        cursor.execute("INSERT INTO audit_log (action_type, payload_json, timestamp) VALUES ('DEPOSIT', ?, ?)", (json.dumps(payload), now))
        conn.commit()

    summary = get_portfolio_summary()
    return True, (
        f"✅ **Deposited ₹{amount:,.2f}!**\n"
        f"• New Available Cash: ₹{summary['cash_available']:,.2f}\n"
        f"• New Total Equity: ₹{summary['total_capital']:,.2f}\n"
        f"• New Dynamic 30% Position Size: **₹{summary['slot_target_allocation']:,.2f}**"
    )


def withdraw_capital(amount: float, note: str = "Capital Withdrawal") -> Tuple[bool, str]:
    """Withdraws cash from the uninvested pool."""
    if amount <= 0:
        return False, "🛑 Withdrawal amount must be positive."

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT cash_available FROM portfolio_ledger WHERE id = 1")
        cash = cursor.fetchone()['cash_available']

        if amount > cash:
            return False, f"🛑 Insufficient cash. Requested ₹{amount:,.2f}, Available: ₹{cash:,.2f}."

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            UPDATE portfolio_ledger 
            SET cash_available = cash_available - ?, total_withdrawals = total_withdrawals + ?, updated_at = ?
            WHERE id = 1
        """, (amount, amount, now))

        cursor.execute("INSERT INTO capital_transactions (timestamp, type, amount, note) VALUES (?, 'WITHDRAWAL', ?, ?)",
                       (now, amount, note))

        payload = {"amount": amount, "note": note}
        cursor.execute("INSERT INTO audit_log (action_type, payload_json, timestamp) VALUES ('WITHDRAWAL', ?, ?)", (json.dumps(payload), now))
        conn.commit()

    summary = get_portfolio_summary()
    return True, (
        f"✅ **Withdrawn ₹{amount:,.2f}.**\n"
        f"• Remaining Cash: ₹{summary['cash_available']:,.2f}\n"
        f"• Updated Dynamic 30% Position Size: **₹{summary['slot_target_allocation']:,.2f}**"
    )


def undo_last_action() -> Tuple[bool, str]:
    """Reverts the most recent user action in the audit log."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audit_log ORDER BY log_id DESC LIMIT 1")
        last_log = cursor.fetchone()

        if not last_log:
            return False, "🛑 No actions available in audit log to undo."

        action = last_log['action_type']
        payload = json.loads(last_log['payload_json'])
        log_id = last_log['log_id']

        if action == "BUY":
            # Delete active trade and refund cash
            ticker = payload['ticker']
            total_cost = payload['total_cost']
            cursor.execute("DELETE FROM active_swing_trades WHERE ticker = ?", (ticker,))
            cursor.execute("UPDATE portfolio_ledger SET cash_available = cash_available + ? WHERE id = 1", (total_cost,))
            msg = f"↩️ **Undone BUY**: Deleted `{ticker}` from active holdings and restored ₹{total_cost:,.2f} to cash."

        elif action == "DEPOSIT":
            amount = payload['amount']
            cursor.execute("UPDATE portfolio_ledger SET cash_available = cash_available - ?, total_deposits = total_deposits - ? WHERE id = 1", (amount, amount))
            msg = f"↩️ **Undone DEPOSIT**: Reversed ₹{amount:,.2f} deposit."

        elif action == "WITHDRAWAL":
            amount = payload['amount']
            cursor.execute("UPDATE portfolio_ledger SET cash_available = cash_available + ?, total_withdrawals = total_withdrawals - ? WHERE id = 1", (amount, amount))
            msg = f"↩️ **Undone WITHDRAWAL**: Restored ₹{amount:,.2f} to cash pool."

        else:
            return False, f"🛑 Automated undo for action `{action}` is restricted to preserve ledger integrity. Please use manual edit."

        cursor.execute("DELETE FROM audit_log WHERE log_id = ?", (log_id,))
        conn.commit()
        return True, msg


def get_closed_trades_df() -> pd.DataFrame:
    """Returns the closed trades ledger as a pandas DataFrame."""
    with get_db() as conn:
        df = pd.read_sql_query("SELECT * FROM closed_trades ORDER BY exit_date DESC", conn)
        return df


if __name__ == "__main__":
    init_portfolio_db(initial_capital=100000.0)
    print("Portfolio tracker initialized.")
    summary = get_portfolio_summary()
    print("Summary:", summary)

