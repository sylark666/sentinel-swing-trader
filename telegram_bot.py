"""
telegram_bot.py
===============
Interactive Telegram Chat Wizard & Command Handler.
Features:
- Chat-ID Whitelisting (rejects unauthorized users).
- Interactive Chat Wizard for /bought and /sold (zero syntax memorization).
- Inline Keyboard Buttons & Confirmation Cards (Are-You-Sure gate).
- Price & Capital Sanity Guardrails (catches fat-finger typos).
- Real-time /status, /capital, /ledger (CSV download), /vault, and /undo.
"""

import os
import json
import logging
import time
from datetime import datetime
import pandas as pd
from urllib import request, parse
from typing import Dict, Optional

import portfolio_manager as pm
import notifications as notif

logger = logging.getLogger("TelegramBot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# In-memory wizard state: chat_id -> dict
WIZARD_STATES: Dict[int, Dict] = {}


def get_authorized_chat_id() -> str:
    cfg = notif.load_config()
    return str(cfg.get("telegram_chat_id", "")).strip()


def get_bot_token() -> str:
    cfg = notif.load_config()
    return str(cfg.get("telegram_token", "")).strip()


def send_reply(chat_id: int, text: str, reply_markup: Optional[Dict] = None):
    token = get_bot_token()
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)

    try:
        data = parse.urlencode(payload).encode("utf-8")
        req = request.Request(url, data=data, method="POST")
        with request.urlopen(req, timeout=10) as resp:
            pass
    except Exception as e:
        logger.error(f"Failed to send reply: {e}")


def answer_callback_query(callback_query_id: str, text: str = ""):
    token = get_bot_token()
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    try:
        payload = {"callback_query_id": callback_query_id, "text": text}
        data = parse.urlencode(payload).encode("utf-8")
        req = request.Request(url, data=data, method="POST")
        with request.urlopen(req, timeout=5) as resp:
            pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# COMMAND HANDLERS
# ---------------------------------------------------------------------------

def handle_status_command(chat_id: int):
    active = pm.get_active_trades()
    summary = pm.get_portfolio_summary()
    now = datetime.now()

    if not active:
        send_reply(chat_id, "ℹ️ *No active swing positions currently open.*\n100% Cash is available for high-alpha setups.")
        return

    # Fetch live quotes for active positions
    try:
        from live_market_service import fetch_live_quotes
        quotes = fetch_live_quotes([p['ticker'] for p in active])
    except Exception:
        quotes = {}

    lines = ["🔔 *ACTIVE SWING POSITIONS (Live Status)*", "━━━━━━━━━━━━━━━━━━━━"]
    for pos in active:
        ticker = pos['ticker']
        clean = ticker.replace(".NS", "")
        stage = f"Stage {pos.get('chandelier_stage', 1)}"
        entry_price = float(pos['entry_price'])
        qty = int(pos['qty'])
        quote = quotes.get(ticker, {})
        cmp = quote.get('cmp', entry_price)

        pnl_pct = ((cmp - entry_price) / entry_price) * 100.0
        gross_buy = qty * entry_price
        gross_sell = qty * cmp
        friction = pm.calculate_friction(gross_sell, is_buy=False) + pm.calculate_friction(gross_buy, is_buy=True)
        net_pnl_rs = (gross_sell - gross_buy) - friction

        try:
            entry_dt = pd.to_datetime(pos['entry_date'])
            holding_days = (now - entry_dt).total_seconds() / 86400.0
        except Exception:
            holding_days = 0.0

        pnl_color = "🟢" if net_pnl_rs >= 0 else "🔴"
        pnl_sign = "+" if net_pnl_rs >= 0 else ""

        lines.append(
            f"📈 *{clean}*\n"
            f"• Entry Price: ₹{entry_price:,.2f}\n"
            f"• Current Price: ₹{cmp:,.2f}\n"
            f"• Estimated Net PnL: {pnl_color} {pnl_sign}{pnl_pct:.2f}% ({pnl_sign}₹{net_pnl_rs:,.2f})\n"
            f"• Holding Duration: {holding_days:.1f} days\n"
            f"• Open Shares: {qty}\n"
            f"• Trailing Stop: ₹{pos['current_trailing_stop']:,.2f} ({stage})\n"
        )
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💼 *Cash Available:* ₹{summary['cash_available']:,.2f} ({summary['cash_pct']:.1f}%)")
    lines.append(f"🎯 *Slots Available:* {summary['effective_slots_available']:.1f} / 3.0 Open")

    send_reply(chat_id, "\n".join(lines))


def handle_capital_command(chat_id: int):
    s = pm.get_portfolio_summary()
    text = (
        f"💼 *PORTFOLIO CAPITAL & EXPOSURE*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• *Total Portfolio Equity:* ₹{s['total_capital']:,.2f}\n"
        f"• *Cash in Hand:* ₹{s['cash_available']:,.2f} ({s['cash_pct']:.1f}%)\n"
        f"• *Invested in Swings:* ₹{s['invested_capital']:,.2f}\n"
        f"• *Dynamic 30% Slot Target:* ₹{s['slot_target_allocation']:,.2f}\n"
        f"• *Effective Slots:* {s['effective_slots_occupied']:.1f} / 3.0 Active\n"
        f"• *Long-Term Vault:* {s['vault_stocks_count']} stocks ({s['vault_total_qty']} shares)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 *Commands:* `/deposit <amount>` | `/withdraw <amount>`"
    )
    send_reply(chat_id, text)


def handle_vault_command(chat_id: int):
    with pm.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM long_term_vault ORDER BY transfer_date DESC")
        rows = [dict(r) for r in cursor.fetchall()]

    if not rows:
        send_reply(chat_id, "🏦 *Long-Term Vault is empty.*\nWhen closing winning swings, use `--keep <qty>` to archive shares forever at zero cost basis.")
        return

    lines = ["🏦 *LONG-TERM VAULT (Retained Forever)*", "━━━━━━━━━━━━━━━━━━━━"]
    for r in rows:
        t = r['ticker'].replace(".NS", "")
        lines.append(f"• *{t}:* {r['qty']} shares (Carved out @ ₹{r['original_entry_price']:,.2f})")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("🛡️ *These shares are completely excluded from swing stop-loss alerts.*")

    send_reply(chat_id, "\n".join(lines))


def handle_ledger_command(chat_id: int):
    df = pm.get_closed_trades_df()
    if df.empty:
        send_reply(chat_id, "ℹ️ *No closed trades in ledger yet.*")
        return

    csv_path = "trades_ledger_export.csv"
    df.to_csv(csv_path, index=False)
    summary_text = f"📁 *Exported {len(df)} closed trades.* Attached below:"
    send_reply(chat_id, summary_text)
    notif.send_telegram_document(csv_path, caption="Closed Trades Audit Ledger (CSV)")


def handle_undo_command(chat_id: int):
    ok, msg = pm.undo_last_action()
    send_reply(chat_id, msg)


# ---------------------------------------------------------------------------
# INTERACTIVE CHAT WIZARD (STEP-BY-STEP PROMPTING)
# ---------------------------------------------------------------------------

def start_buy_wizard(chat_id: int, prefill_ticker: str = ""):
    active = pm.get_active_trades()
    summary = pm.get_portfolio_summary()

    if not summary['can_take_new_trade'] and summary['effective_slots_occupied'] >= 3.0:
        send_reply(chat_id, "⚠️ *All 3 portfolio slots are currently occupied.*\nTo make room, close an active trade or book partial profit.")
        return

    WIZARD_STATES[chat_id] = {
        "action": "BUY",
        "step": "WAITING_QTY" if prefill_ticker else "WAITING_TICKER",
        "ticker": prefill_ticker,
        "qty": 0,
        "price": 0.0,
        "stop_loss": 0.0
    }

    if prefill_ticker:
        sug_qty = int(summary['slot_target_allocation'] // 1500) if summary['slot_target_allocation'] > 0 else 10
        send_reply(chat_id, f"📝 *Logging Buy for `{prefill_ticker}`:*\nHow many shares were executed? *(e.g. `{sug_qty}`)*")
    else:
        send_reply(chat_id, "📝 *Which stock did you buy?*\nType the symbol (e.g. `TECHM` or `HAL`):")


def start_sell_wizard(chat_id: int):
    active = pm.get_active_trades()
    if not active:
        send_reply(chat_id, "ℹ️ *You have no active open positions to sell.*")
        return

    buttons = []
    for pos in active:
        t = pos['ticker'].replace(".NS", "")
        buttons.append([{"text": f"{t} ({pos['qty']} shs @ ₹{pos['entry_price']:,.1f})", "callback_data": f"WIZ_SELL_SELECT_{t}"}])
    buttons.append([{"text": "❌ Cancel", "callback_data": "WIZ_CANCEL"}])

    reply_markup = {"inline_keyboard": buttons}
    send_reply(chat_id, "📝 *Which position are you exiting / scaling out?*", reply_markup=reply_markup)


def handle_wizard_text_input(chat_id: int, text: str):
    state = WIZARD_STATES.get(chat_id)
    if not state:
        return False

    clean_text = text.strip()

    # Cancel command anytime
    if clean_text.lower() in ["cancel", "/cancel", "stop"]:
        del WIZARD_STATES[chat_id]
        send_reply(chat_id, "❌ Wizard cancelled. Nothing saved.")
        return True

    # --- BUY WIZARD STEPS ---
    if state['action'] == "BUY":
        if state['step'] == "WAITING_TICKER":
            ticker = clean_text.upper().replace(".NS", "")
            state['ticker'] = ticker
            state['step'] = "WAITING_QTY"
            s = pm.get_portfolio_summary()
            send_reply(chat_id, f"Selected: `{ticker}`.\nHow many shares did you execute? *(Target 30% size: ~₹{s['slot_target_allocation']:,.0f})*")
            return True

        elif state['step'] == "WAITING_QTY":
            try:
                qty = int(clean_text)
                if qty <= 0:
                    send_reply(chat_id, "🛑 Quantity must be greater than 0. Try again:")
                    return True
                state['qty'] = qty
                state['step'] = "WAITING_PRICE"
                send_reply(chat_id, f"Logged {qty} shares.\nWhat was your executed buy price per share? *(e.g. `1650.50`)*")
                return True
            except ValueError:
                send_reply(chat_id, "🛑 Please enter a valid whole number for quantity:")
                return True

        elif state['step'] == "WAITING_PRICE":
            try:
                price = float(clean_text.replace("₹", "").replace(",", ""))
                if price <= 0:
                    send_reply(chat_id, "🛑 Price must be greater than 0. Try again:")
                    return True
                state['price'] = price
                state['step'] = "CONFIRMING"

                # Estimated stop loss (5% below entry as baseline default)
                stop_loss = round(price * 0.95, 2)
                state['stop_loss'] = stop_loss
                total_val = state['qty'] * price

                # Sanity guardrails
                s = pm.get_portfolio_summary()
                if total_val > s['cash_available']:
                    send_reply(chat_id, f"⚠️ *Warning:* Total value ₹{total_val:,.2f} exceeds cash (₹{s['cash_available']:,.2f}).")

                # Show Confirmation Card with Inline Buttons
                buttons = {
                    "inline_keyboard": [
                        [{"text": "✅ CONFIRM & SAVE", "callback_data": f"CONFIRM_BUY_{state['ticker']}"}],
                        [{"text": "❌ CANCEL / REDO", "callback_data": "WIZ_CANCEL"}]
                    ]
                }
                card = (
                    f"⚠️ *PLEASE CONFIRM TRADE ENTRY:*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"• *Stock:* `{state['ticker']}.NS`\n"
                    f"• *Quantity:* {state['qty']} shares\n"
                    f"• *Executed Price:* ₹{price:,.2f}\n"
                    f"• *Total Value:* ₹{total_val:,.2f}\n"
                    f"• *Initial Hard Stop:* ₹{stop_loss:,.2f} (-5.0%)\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"Tap below to save or cancel:"
                )
                send_reply(chat_id, card, reply_markup=buttons)
                return True
            except ValueError:
                send_reply(chat_id, "🛑 Please enter a valid number for price:")
                return True

    # --- SELL WIZARD STEPS ---
    elif state['action'] == "SELL":
        if state['step'] == "WAITING_SELL_PRICE":
            try:
                price = float(clean_text.replace("₹", "").replace(",", ""))
                state['price'] = price
                mode = state.get('sell_mode', 'FULL')
                ticker = state['ticker']

                if mode == "FULL":
                    retain = state.get('retain_qty', 0)
                    ok, msg = pm.log_full_sell(ticker, price, reason="Manual User Exit", retain_qty=retain)
                elif mode == "PARTIAL":
                    qty_to_sell = state.get('qty_to_sell', 0)
                    ok, msg = pm.log_partial_sell(ticker, qty_to_sell, price)
                else:
                    ok, msg = False, "Unknown sell mode."

                del WIZARD_STATES[chat_id]
                send_reply(chat_id, msg)
                return True
            except ValueError:
                send_reply(chat_id, "🛑 Please enter a valid price:")
                return True

    return False


# ---------------------------------------------------------------------------
# INLINE BUTTON CALLBACK QUERY DISPATCHER
# ---------------------------------------------------------------------------

def handle_callback_query(callback_data: str, chat_id: int, query_id: str):
    answer_callback_query(query_id, "Processing...")

    # 1. Skip signal
    if callback_data.startswith("SKIP_"):
        ticker = callback_data.replace("SKIP_", "")
        send_reply(chat_id, f"⏭️ Skipped signal for `{ticker}`. Slot remains open.")
        return

    # 2. Cancel wizard
    if callback_data == "WIZ_CANCEL":
        if chat_id in WIZARD_STATES:
            del WIZARD_STATES[chat_id]
        send_reply(chat_id, "❌ Action cancelled.")
        return

    # 3. Default Buy Confirmation
    if callback_data.startswith("BUY_DEFAULT_"):
        parts = callback_data.split("_")
        ticker = parts[2]
        qty = int(parts[3])
        price = float(parts[4])
        stop_loss = float(parts[5])

        ok, msg = pm.log_buy_trade(ticker, qty, price, stop_loss)
        send_reply(chat_id, msg)
        return

    # 4. Trigger Buy Wizard for custom price/qty
    if callback_data.startswith("BUY_WIZARD_"):
        ticker = callback_data.replace("BUY_WIZARD_", "")
        start_buy_wizard(chat_id, prefill_ticker=ticker)
        return

    # 5. Confirm pending buy
    if callback_data.startswith("CONFIRM_BUY_"):
        state = WIZARD_STATES.get(chat_id)
        if state and state['action'] == "BUY":
            ok, msg = pm.log_buy_trade(state['ticker'], state['qty'], state['price'], state['stop_loss'])
            del WIZARD_STATES[chat_id]
            send_reply(chat_id, msg)
        else:
            send_reply(chat_id, "🛑 Session expired. Please run `/bought` again.")
        return

    # 6. Sell wizard selection
    if callback_data.startswith("WIZ_SELL_SELECT_"):
        ticker = callback_data.replace("WIZ_SELL_SELECT_", "")
        active = {p['ticker'].replace(".NS", ""): p for p in pm.get_active_trades()}
        pos = active.get(ticker)
        if not pos:
            send_reply(chat_id, f"🛑 No active position found for `{ticker}`.")
            return

        WIZARD_STATES[chat_id] = {
            "action": "SELL",
            "ticker": ticker,
            "total_qty": pos['qty'],
            "entry_price": pos['entry_price']
        }

        buttons = {
            "inline_keyboard": [
                [{"text": f"✅ Full Exit ({pos['qty']} shares)", "callback_data": f"SELL_DO_FULL_{ticker}"}],
                [{"text": f"📊 Partial Profit 50% ({int(pos['qty']//2)} shs)", "callback_data": f"SELL_DO_HALF_{ticker}"}],
                [{"text": "💎 Keep Some in Vault Forever", "callback_data": f"SELL_DO_VAULT_{ticker}"}],
                [{"text": "❌ Cancel", "callback_data": "WIZ_CANCEL"}]
            ]
        }
        send_reply(chat_id, f"Position: `{ticker}` ({pos['qty']} shares @ Entry ₹{pos['entry_price']:,.2f})\nSelect exit type:", reply_markup=buttons)
        return

    # 7. Execute sell modes
    if callback_data.startswith("SELL_DO_FULL_"):
        ticker = callback_data.replace("SELL_DO_FULL_", "")
        state = WIZARD_STATES.get(chat_id, {})
        state['sell_mode'] = "FULL"
        state['step'] = "WAITING_SELL_PRICE"
        WIZARD_STATES[chat_id] = state
        send_reply(chat_id, f"Exiting `{ticker}` completely ({state.get('total_qty', 0)} shares).\nWhat was your executed sell price per share? *(e.g. `1825.0`)*")
        return

    if callback_data.startswith("SELL_DO_HALF_"):
        ticker = callback_data.replace("SELL_DO_HALF_", "")
        state = WIZARD_STATES.get(chat_id, {})
        total_qty = state.get('total_qty', 0)
        state['sell_mode'] = "PARTIAL"
        state['qty_to_sell'] = max(1, total_qty // 2)
        state['step'] = "WAITING_SELL_PRICE"
        WIZARD_STATES[chat_id] = state
        send_reply(chat_id, f"Selling 50% ({state['qty_to_sell']} shares) of `{ticker}`.\nWhat was your executed sell price? *(e.g. `1825.0`)*")
        return

    if callback_data.startswith("SELL_DO_VAULT_"):
        ticker = callback_data.replace("SELL_DO_VAULT_", "")
        state = WIZARD_STATES.get(chat_id, {})
        total_qty = state.get('total_qty', 0)
        retain = max(1, int(total_qty * 0.05)) # default ~5%
        state['sell_mode'] = "FULL"
        state['retain_qty'] = retain
        state['step'] = "WAITING_SELL_PRICE"
        WIZARD_STATES[chat_id] = state
        send_reply(chat_id, f"Selling {total_qty - retain} shares of `{ticker}` & keeping **{retain} shares in Vault**.\nWhat was your executed sell price? *(e.g. `1825.0`)*")
        return


# ---------------------------------------------------------------------------
# MESSAGE DISPATCHER / ROUTER
# ---------------------------------------------------------------------------

def process_telegram_update(update: Dict):
    """Processes an incoming Telegram update with strict Chat ID authorization."""
    auth_chat_id = get_authorized_chat_id()

    # Handle inline button click (callback query)
    if "callback_query" in update:
        cq = update["callback_query"]
        user_id = str(cq["from"]["id"])
        chat_id = cq["message"]["chat"]["id"]
        data = cq.get("data", "")
        cq_id = cq["id"]

        if auth_chat_id and user_id != auth_chat_id:
            answer_callback_query(cq_id, "⛔ Unauthorized user.")
            return

        handle_callback_query(data, chat_id, cq_id)
        return

    # Handle normal text message
    if "message" in update and "text" in update["message"]:
        msg = update["message"]
        user_id = str(msg["from"]["id"])
        chat_id = msg["chat"]["id"]
        text = msg["text"].strip()

        # Strict Security Gatekeeper: Check authorized chat ID
        if auth_chat_id and user_id != auth_chat_id:
            send_reply(chat_id, "⛔ *Unauthorized Access Denied.*\nThis bot is locked to its owner.")
            return

        # Check if user is currently inside the interactive wizard
        if handle_wizard_text_input(chat_id, text):
            return

        # Handle top-level slash commands
        cmd = text.split()[0].lower()

        if cmd in ["/start", "/help"]:
            help_text = (
                "🤖 *AUTO SWING TRADER BOT*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "• `/bought` : Interactive wizard to log a buy\n"
                "• `/sold`   : Interactive wizard to sell/scale-out/vault\n"
                "• `/status` : View live holdings & trailing stops\n"
                "• `/capital`: View cash, invested equity & slot size\n"
                "• `/ledger` : Download trade ledger (CSV)\n"
                "• `/vault`  : View shares kept in Long-Term Vault\n"
                "• `/deposit <amount>` : Add funds to pool\n"
                "• `/withdraw <amount>`: Withdraw funds\n"
                "• `/undo`   : Revert last action\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
            send_reply(chat_id, help_text)

        elif cmd == "/status":
            handle_status_command(chat_id)

        elif cmd == "/capital":
            handle_capital_command(chat_id)

        elif cmd == "/vault":
            handle_vault_command(chat_id)

        elif cmd in ["/ledger", "/export"]:
            handle_ledger_command(chat_id)

        elif cmd == "/undo":
            handle_undo_command(chat_id)

        elif cmd == "/bought":
            parts = text.split()
            if len(parts) >= 4:
                # Direct shortcut: /bought TICKER QTY PRICE
                ticker, qty, price = parts[1], int(parts[2]), float(parts[3])
                ok, res_msg = pm.log_buy_trade(ticker, qty, price, round(price * 0.95, 2))
                send_reply(chat_id, res_msg)
            else:
                # Launch interactive wizard
                start_buy_wizard(chat_id)

        elif cmd == "/sold":
            parts = text.split()
            if len(parts) >= 3:
                ticker, price = parts[1], float(parts[2])
                ok, res_msg = pm.log_full_sell(ticker, price, reason="Manual User Exit")
                send_reply(chat_id, res_msg)
            else:
                start_sell_wizard(chat_id)

        elif cmd == "/deposit":
            parts = text.split()
            if len(parts) >= 2:
                try:
                    amt = float(parts[1])
                    ok, res_msg = pm.deposit_capital(amt)
                    send_reply(chat_id, res_msg)
                except ValueError:
                    send_reply(chat_id, "🛑 Usage: `/deposit 50000`")
            else:
                send_reply(chat_id, "🛑 Usage: `/deposit 50000`")

        elif cmd == "/withdraw":
            parts = text.split()
            if len(parts) >= 2:
                try:
                    amt = float(parts[1])
                    ok, res_msg = pm.withdraw_capital(amt)
                    send_reply(chat_id, res_msg)
                except ValueError:
                    send_reply(chat_id, "🛑 Usage: `/withdraw 20000`")
            else:
                send_reply(chat_id, "🛑 Usage: `/withdraw 20000`")

        else:
            send_reply(chat_id, "❓ Unknown command. Type `/help` for available commands or `/bought` to log a trade.")


def poll_telegram_updates(interval_sec: float = 2.0):
    """
    Long-polling worker to listen for incoming Telegram messages and button clicks.
    Runs continuously or as a background thread.
    """
    # Wait until token is available (in case secrets are loading)
    token = get_bot_token()
    retries = 0
    while not token and retries < 15:
        time.sleep(2)
        token = get_bot_token()
        retries += 1

    if not token:
        logger.error("No Telegram token configured. Cannot start polling.")
        return

    logger.info("Starting Telegram Bot listener...")
    offset = 0

    while True:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=5"
            req = request.Request(url)
            with request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                try:
                    process_telegram_update(update)
                except Exception as ex:
                    logger.error(f"Error processing telegram update: {ex}")

        except Exception as e:
            logger.debug(f"Polling loop: {e}")
            time.sleep(interval_sec)

        time.sleep(interval_sec)


if __name__ == "__main__":
    print("Testing telegram_bot.py module compilation...")

