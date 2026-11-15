"""
test_alert_channels.py
======================
Quick utility to test Telegram Bot and Gmail notifications.
Usage:
    python test_alert_channels.py
"""

import sys
import os
import notifications as notif
import portfolio_manager as pm

def main():
    print("=" * 65)
    print("🔔 AUTO SWING TRADER: ALERT CHANNEL TEST")
    print("=" * 65)

    config = notif.load_config()
    tg_token = config.get("telegram_token")
    tg_chat = config.get("telegram_chat_id")
    gmail_sender = config.get("gmail_sender")
    gmail_pwd = config.get("gmail_app_password")

    print(f"• Telegram Token Configured: {'✅ Yes' if tg_token else '❌ Missing'}")
    print(f"• Telegram Chat ID: {'✅ ' + str(tg_chat) if tg_chat else '❌ Missing'}")
    print(f"• Gmail Sender: {'✅ ' + str(gmail_sender) if gmail_sender else '❌ Missing'}")
    print(f"• Gmail App Password: {'✅ Yes' if gmail_pwd else '❌ Missing'}")
    print("-" * 65)

    if not tg_token and not gmail_sender:
        print("⚠️ No credentials configured yet!")
        print("To configure, you can either:")
        print("1. Create an `alert_config.json` file (copy from `alert_config.example.json`).")
        print("2. Set environment variables: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GMAIL_SENDER, GMAIL_APP_PASSWORD.")
        print("3. In Streamlit Cloud, add them to `st.secrets`.")
        return

    # 1. Test Telegram
    if tg_token and tg_chat:
        print("Testing Telegram dispatch...")
        tg_ok = notif.send_telegram_message("🔔 *Test Alert from Auto Swing Trader Bot*\nYour Telegram alerts are working properly! ✅")
        if tg_ok:
            print("✅ Telegram message delivered successfully!")
        else:
            print("❌ Telegram dispatch failed. Check token and chat_id.")

    # 2. Test Gmail
    if gmail_sender and gmail_pwd:
        print("Testing Gmail dispatch...")
        html = """
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #0f141c; color: #ffffff; padding: 20px;">
            <div style="max-width: 500px; margin: auto; background-color: #1a2232; border-radius: 8px; border-left: 6px solid #00ff88; padding: 20px;">
                <h3 style="color: #00ff88; margin-top: 0;">✅ Gmail Alerts Connected</h3>
                <p style="font-size: 14px; color: #cbd5e0;">Your Auto Swing Trader email dispatch is active and verified.</p>
            </div>
        </body>
        </html>
        """
        mail_ok = notif.send_gmail_alert("🔔 Test Alert: Auto Swing Trader Connected", html)
        if mail_ok:
            print("✅ Gmail email delivered successfully!")
        else:
            print("❌ Gmail dispatch failed. Check sender email and 16-character App Password.")

    print("=" * 65)

if __name__ == "__main__":
    main()

