"""
notifications.py
================
Unified multi-channel notification engine for Telegram Bot and Gmail.
- Telegram: Instant push alerts, clickable inline buttons, document attachments.
- Gmail: Formatted HTML emails with tables, alerts, and CSV attachments.
Zero heavy frameworks; uses standard Python stdlib (smtplib, urllib).
"""

import os
import json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from urllib import request, parse
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("Notifications")

# Credential keys (can be set via environment variables, config.json, or Streamlit secrets)
CONFIG_FILE = "alert_config.json"


def load_config() -> Dict:
    """Loads notification credentials from config.json, environment, or Streamlit secrets."""
    config = {
        "telegram_token": os.getenv("TELEGRAM_BOT_TOKEN", "") or os.getenv("TELEGRAM_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        "gmail_sender": os.getenv("GMAIL_SENDER", ""),
        "gmail_app_password": os.getenv("GMAIL_APP_PASSWORD", ""),
        "gmail_receiver": os.getenv("GMAIL_RECIPIENT", "") or os.getenv("GMAIL_RECEIVER", "")
    }

    # If Streamlit secrets available
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            # Check Telegram keys
            for k in ["TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN", "telegram_bot_token", "telegram_token"]:
                if k in st.secrets:
                    config["telegram_token"] = str(st.secrets[k]).strip()
                    break

            for k in ["TELEGRAM_CHAT_ID", "telegram_chat_id"]:
                if k in st.secrets:
                    config["telegram_chat_id"] = str(st.secrets[k]).strip()
                    break

            # Check Gmail keys
            for k in ["GMAIL_SENDER", "gmail_sender"]:
                if k in st.secrets:
                    config["gmail_sender"] = str(st.secrets[k]).strip()
                    break

            for k in ["GMAIL_APP_PASSWORD", "gmail_app_password"]:
                if k in st.secrets:
                    config["gmail_app_password"] = str(st.secrets[k]).strip()
                    break

            for k in ["GMAIL_RECIPIENT", "GMAIL_RECEIVER", "gmail_recipient", "gmail_receiver"]:
                if k in st.secrets:
                    config["gmail_receiver"] = str(st.secrets[k]).strip()
                    break
    except Exception:
        pass

    # Fallback to local config.json if exists
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                for k, v in saved.items():
                    if v and not config.get(k):
                        config[k] = v
        except Exception as e:
            logger.warning(f"Failed to read {CONFIG_FILE}: {e}")

    return config


# ---------------------------------------------------------------------------
# TELEGRAM BOT CLIENT
# ---------------------------------------------------------------------------

def send_telegram_message(text: str, reply_markup: Optional[Dict] = None) -> bool:
    """Sends a markdown-formatted message to the whitelisted Telegram Chat ID."""
    config = load_config()
    token = config.get("telegram_token")
    chat_id = config.get("telegram_chat_id")

    if not token or not chat_id:
        logger.warning("Telegram credentials not configured. Skipping Telegram dispatch.")
        return False

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
            return resp.status == 200
    except Exception as e:
        logger.error(f"Telegram dispatch failed: {e}")
        return False


def send_telegram_document(file_path: str, caption: str = "") -> bool:
    """Uploads and sends a file (e.g. CSV ledger) directly into Telegram chat."""
    config = load_config()
    token = config.get("telegram_token")
    chat_id = config.get("telegram_chat_id")

    if not token or not chat_id or not os.path.exists(file_path):
        return False

    try:
        import requests
        url = f"https://api.telegram.org/bot{token}/sendDocument"
        with open(file_path, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"},
                files={"document": f},
                timeout=25
            )
            return resp.status_code == 200
    except ImportError:
        # Fallback using urllib multipart if requests not installed
        logger.warning("`requests` not installed; skipping telegram document upload.")
        return False
    except Exception as e:
        logger.error(f"Telegram document dispatch failed: {e}")
        return False


# ---------------------------------------------------------------------------
# GMAIL SMTP CLIENT
# ---------------------------------------------------------------------------

def send_gmail_alert(subject: str, html_body: str, attachment_path: Optional[str] = None) -> bool:
    """Sends a stylized HTML email alert to your Gmail address."""
    config = load_config()
    sender = config.get("gmail_sender")
    pwd = config.get("gmail_app_password")
    receiver = config.get("gmail_receiver") or sender

    if not sender or not pwd:
        logger.warning("Gmail credentials not configured. Skipping Email dispatch.")
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = f"Auto Swing Trader <{sender}>"
    msg["To"] = receiver
    msg["Subject"] = subject

    # Attach HTML
    msg.attach(MIMEText(html_body, "html"))

    # Optional CSV attachment
    if attachment_path and os.path.exists(attachment_path):
        part = MIMEBase("application", "octet-stream")
        with open(attachment_path, "rb") as f:
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(attachment_path)}")
        msg.attach(part)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(sender, pwd)
            server.sendmail(sender, receiver, msg.as_string())
        return True
    except Exception as e:
        logger.error(f"Gmail dispatch failed: {e}")
        return False


# ---------------------------------------------------------------------------
# DUAL-CHANNEL SIGNAL DISPATCHERS
# ---------------------------------------------------------------------------

def dispatch_buy_alert(
    ticker: str,
    signal_price: float,
    suggested_qty: int,
    stop_loss: float,
    alpha_score: float,
    reasons: str,
    available_cash: float,
    suggested_alloc: float
) -> Tuple[bool, bool]:
    """Fires a high-conviction buy signal to Telegram (with interactive buttons) and Gmail."""
    clean_ticker = ticker.replace(".NS", "")
    risk_pct = ((signal_price - stop_loss) / signal_price) * 100

    # 1. Telegram Message with Inline Buttons
    tg_text = (
        f"🟢 *NEW SWING BUY SIGNAL: {clean_ticker}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• *Signal Price:* ₹{signal_price:,.2f}\n"
        f"• *Suggested Sizing:* {suggested_qty} shares (₹{suggested_alloc:,.2f})\n"
        f"• *Initial Hard Stop:* ₹{stop_loss:,.2f} (-{risk_pct:.1f}% risk)\n"
        f"• *Alpha Score:* `{alpha_score:.0f} / 100`\n"
        f"• *Setup Confluence:* {reasons}\n"
        f"• *Cash Available:* ₹{available_cash:,.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 *Action:* Buy on broker, then tap below or text `/bought`:"
    )

    # Clickable inline buttons for Telegram
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": f"⚡ Confirm Buy ({suggested_qty} @ ₹{signal_price:,.1f})", "callback_data": f"BUY_DEFAULT_{clean_ticker}_{suggested_qty}_{signal_price:.2f}_{stop_loss:.2f}"}
            ],
            [
                {"text": "✏️ Custom Qty / Price", "callback_data": f"BUY_WIZARD_{clean_ticker}"},
                {"text": "⏭️ Skip Signal", "callback_data": f"SKIP_{clean_ticker}"}
            ]
        ]
    }

    tg_ok = send_telegram_message(tg_text, reply_markup=reply_markup)

    # 2. Gmail HTML Message
    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #0f141c; color: #ffffff; padding: 20px;">
        <div style="max-width: 600px; margin: auto; background-color: #1a2232; border-radius: 8px; border-left: 6px solid #00ff88; padding: 20px;">
            <h2 style="color: #00ff88; margin-top: 0;">🟢 NEW SWING BUY SIGNAL: {clean_ticker}</h2>
            <p style="font-size: 14px; color: #a0aec0;">A high-conviction 4H Swing Golden Cross triggered with institutional momentum.</p>
            <table style="width: 100%; border-collapse: collapse; margin: 20px 0; font-size: 14px;">
                <tr style="border-bottom: 1px solid #2d3748;"><td style="padding: 8px; color: #a0aec0;">Signal CMP</td><td style="padding: 8px; font-weight: bold; color: #ffffff;">₹{signal_price:,.2f}</td></tr>
                <tr style="border-bottom: 1px solid #2d3748;"><td style="padding: 8px; color: #a0aec0;">Suggested Position</td><td style="padding: 8px; font-weight: bold; color: #ffffff;">{suggested_qty} shares (₹{suggested_alloc:,.2f})</td></tr>
                <tr style="border-bottom: 1px solid #2d3748;"><td style="padding: 8px; color: #a0aec0;">Initial Stop Loss</td><td style="padding: 8px; font-weight: bold; color: #ff4d4d;">₹{stop_loss:,.2f} (-{risk_pct:.1f}%)</td></tr>
                <tr style="border-bottom: 1px solid #2d3748;"><td style="padding: 8px; color: #a0aec0;">Alpha Score</td><td style="padding: 8px; font-weight: bold; color: #00ff88;">{alpha_score:.0f} / 100</td></tr>
                <tr style="border-bottom: 1px solid #2d3748;"><td style="padding: 8px; color: #a0aec0;">Confluence</td><td style="padding: 8px; color: #ffffff;">{reasons}</td></tr>
            </table>
            <div style="background-color: #243048; padding: 12px; border-radius: 4px; font-size: 13px; color: #cbd5e0;">
                <strong>Next Step:</strong> Execute order on your broker app. Log your executed price in your Telegram bot or Streamlit Dashboard to begin automated trailing exit monitoring.
            </div>
        </div>
    </body>
    </html>
    """
    mail_ok = send_gmail_alert(f"🟢 Swing Buy Signal: {clean_ticker} (Alpha {alpha_score:.0f})", html)

    return tg_ok, mail_ok


def dispatch_exit_alert(
    ticker: str,
    exit_price: float,
    reason: str,
    pnl_pct: float,
    net_pnl_rs: float,
    holding_days: float,
    current_qty: int
) -> Tuple[bool, bool]:
    """Fires a trailing exit or stop loss alert to Telegram and Gmail."""
    clean_ticker = ticker.replace(".NS", "")
    pnl_color = "🟢" if pnl_pct >= 0 else "🔴"
    pnl_sign = "+" if pnl_pct >= 0 else ""

    tg_text = (
        f"🚨 *SWING EXIT ALERT: {clean_ticker}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• *Exit Reason:* {reason}\n"
        f"• *Current Price:* ₹{exit_price:,.2f}\n"
        f"• *Estimated Net PnL:* {pnl_color} *{pnl_sign}{pnl_pct:.2f}%* ({pnl_sign}₹{net_pnl_rs:,.2f})\n"
        f"• *Holding Duration:* {holding_days:.1f} days\n"
        f"• *Open Shares:* {current_qty}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 *Action:* Sell on your broker, then tap an option below:"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": f"✅ Full Exit ({current_qty} shares)", "callback_data": f"SELL_FULL_{clean_ticker}_{exit_price:.2f}"}
            ],
            [
                {"text": "💎 Keep Some in Vault Forever", "callback_data": f"SELL_VAULT_{clean_ticker}_{exit_price:.2f}"},
                {"text": "📊 Partial Sell (50%)", "callback_data": f"SELL_PARTIAL_{clean_ticker}_{exit_price:.2f}"}
            ]
        ]
    }

    tg_ok = send_telegram_message(tg_text, reply_markup=reply_markup)

    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #0f141c; color: #ffffff; padding: 20px;">
        <div style="max-width: 600px; margin: auto; background-color: #1a2232; border-radius: 8px; border-left: 6px solid #ff4d4d; padding: 20px;">
            <h2 style="color: #ff4d4d; margin-top: 0;">🚨 SWING EXIT ALERT: {clean_ticker}</h2>
            <p style="font-size: 14px; color: #a0aec0;">Exit triggered via <strong>{reason}</strong>.</p>
            <table style="width: 100%; border-collapse: collapse; margin: 20px 0; font-size: 14px;">
                <tr style="border-bottom: 1px solid #2d3748;"><td style="padding: 8px; color: #a0aec0;">Exit CMP</td><td style="padding: 8px; font-weight: bold; color: #ffffff;">₹{exit_price:,.2f}</td></tr>
                <tr style="border-bottom: 1px solid #2d3748;"><td style="padding: 8px; color: #a0aec0;">Realized PnL</td><td style="padding: 8px; font-weight: bold; color: {'#00ff88' if pnl_pct >= 0 else '#ff4d4d'};">{pnl_sign}{pnl_pct:.2f}% ({pnl_sign}₹{net_pnl_rs:,.2f})</td></tr>
                <tr style="border-bottom: 1px solid #2d3748;"><td style="padding: 8px; color: #a0aec0;">Holding Time</td><td style="padding: 8px; color: #ffffff;">{holding_days:.1f} days</td></tr>
                <tr style="border-bottom: 1px solid #2d3748;"><td style="padding: 8px; color: #a0aec0;">Open Shares</td><td style="padding: 8px; color: #ffffff;">{current_qty}</td></tr>
            </table>
            <div style="background-color: #243048; padding: 12px; border-radius: 4px; font-size: 13px; color: #cbd5e0;">
                <strong>Action Required:</strong> Execute the sell order on your broker app. Confirm execution on Telegram to unlock your swing slot.
            </div>
        </div>
    </body>
    </html>
    """
    mail_ok = send_gmail_alert(f"🚨 Swing Exit Alert: {clean_ticker} ({pnl_sign}{pnl_pct:.1f}%)", html)

    return tg_ok, mail_ok


def dispatch_daily_summary(summary: Dict, active_holdings: List[Dict], csv_attachment: Optional[str] = None) -> Tuple[bool, bool]:
    """Dispatches the market close (03:45 PM IST) portfolio health summary."""
    holdings_lines = []
    if active_holdings:
        for h in active_holdings:
            clean = h['ticker'].replace(".NS", "")
            stage_str = " (Ratchet 2.3x)" if h.get('chandelier_stage', 1) == 2 else " (Wide 3.0x)"
            holdings_lines.append(
                f"• *{clean}* ({h.get('effective_slot', 1.0):.1f} Slot): {h['qty']} shares @ Entry ₹{h['entry_price']:,.2f} | Trail Stop: ₹{h.get('current_trailing_stop', 0):,.2f}{stage_str}"
            )
        holdings_text = "\n".join(holdings_lines)
    else:
        holdings_text = "• *No active holdings (100% Cash / Waiting for high-alpha setups)*"

    tg_text = (
        f"📊 *DAILY PORTFOLIO SUMMARY (Market Close)*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• *Total Portfolio Equity:* ₹{summary['total_capital']:,.2f}\n"
        f"• *Available Cash:* ₹{summary['cash_available']:,.2f} ({summary['cash_pct']:.1f}%)\n"
        f"• *Effective Slots:* {summary['effective_slots_occupied']:.1f} / 3.0 Active\n"
        f"• *Long-Term Vault:* {summary['vault_stocks_count']} stocks ({summary['vault_total_qty']} shares)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"*Active Positions:*\n{holdings_text}\n"
    )

    tg_ok = send_telegram_message(tg_text)
    if csv_attachment and os.path.exists(csv_attachment):
        send_telegram_document(csv_attachment, caption="📁 Closed Trades Ledger (CSV)")

    # Email summary
    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #0f141c; color: #ffffff; padding: 20px;">
        <div style="max-width: 600px; margin: auto; background-color: #1a2232; border-radius: 8px; border-left: 6px solid #4da6ff; padding: 20px;">
            <h2 style="color: #4da6ff; margin-top: 0;">📊 Daily Portfolio Health Summary</h2>
            <p style="font-size: 14px; color: #a0aec0;">End-of-day checkpoint audit.</p>
            <table style="width: 100%; border-collapse: collapse; margin: 15px 0; font-size: 14px;">
                <tr style="border-bottom: 1px solid #2d3748;"><td style="padding: 8px; color: #a0aec0;">Total Equity</td><td style="padding: 8px; font-weight: bold;">₹{summary['total_capital']:,.2f}</td></tr>
                <tr style="border-bottom: 1px solid #2d3748;"><td style="padding: 8px; color: #a0aec0;">Available Cash</td><td style="padding: 8px; font-weight: bold; color: #00ff88;">₹{summary['cash_available']:,.2f} ({summary['cash_pct']:.1f}%)</td></tr>
                <tr style="border-bottom: 1px solid #2d3748;"><td style="padding: 8px; color: #a0aec0;">Slots Utilized</td><td style="padding: 8px;">{summary['effective_slots_occupied']:.1f} / 3.0 Slots</td></tr>
                <tr style="border-bottom: 1px solid #2d3748;"><td style="padding: 8px; color: #a0aec0;">Long-Term Vault</td><td style="padding: 8px;">{summary['vault_stocks_count']} stocks ({summary['vault_total_qty']} shares)</td></tr>
            </table>
        </div>
    </body>
    </html>
    """
    mail_ok = send_gmail_alert("📊 Auto Swing Trader: Daily Close Summary", html, attachment_path=csv_attachment)

    return tg_ok, mail_ok

