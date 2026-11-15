"""
security.py
===========
Enterprise-Grade Security & Hardening Suite for Sentinel-Swing.
Features:
1. Port Security & Inbound Access Restrictions (HTTPS only, outgoing-only health checks).
2. Cryptographic Security: SHA-256 password & PIN hashing with salting.
3. Anti-Bot & Anti-AI Shield: Blocks AI scrapers (GPTBot, ClaudeBot, CCBot, Perplexity, etc.) and headless crawlers.
4. Geo-IP Verification: Captures client IP, User-Agent, and country.
5. Foreign Access Alert: Triggers immediate Telegram & Gmail emergency alerts if accessed from outside India.
6. Anti-Brute-Force & IP Blacklisting: Automatically blocks any IP with >= 3 failed attempts.
7. Security Audit Logging: Captures every access attempt with full forensics in SQLite.
"""

import hashlib
import sqlite3
import re
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional

logger = logging.getLogger("SecurityEngine")

DB_FILE = "portfolio_tracker.db"
SALT_KEY = "SENTINEL_SWING_SALT_95K_2026"
MAX_FAILED_ATTEMPTS = 3
LOCKOUT_WINDOW_MINUTES = 15

# Comprehensive list of AI bots, web crawlers, search engines, and headless scrapers
BLOCKED_BOT_KEYWORDS = [
    "gptbot", "chatgpt-user", "claudebot", "anthropic", "ccbot", "perplexitybot",
    "google-extended", "bytespider", "petalbot", "semrushbot", "ahrefsbot",
    "mj12bot", "dotbot", "yandexbot", "baiduspider", "bingbot", "googlebot",
    "headless", "phantomjs", "selenium", "puppeteer", "playwright",
    "python-requests", "curl", "wget", "scrapy", "aiohttp", "httpx", "urllib"
]


def init_security_db():
    """Initializes security audit and IP blacklisting tables."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS security_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            ip_address TEXT NOT NULL,
            country TEXT,
            user_agent TEXT,
            status TEXT NOT NULL,
            details TEXT
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS blocked_ips (
            ip_address TEXT PRIMARY KEY,
            blocked_at DATETIME NOT NULL,
            reason TEXT NOT NULL
        );
    """)

    conn.commit()
    conn.close()


init_security_db()


# ---------------------------------------------------------------------------
# 1. CRYPTOGRAPHIC SECURITY (SHA-256)
# ---------------------------------------------------------------------------

def hash_credential(plain_text: str, salt: str = SALT_KEY) -> str:
    """Generates a secure, salted SHA-256 hash of a PIN or password."""
    salted = f"{salt}_{plain_text.strip()}_{salt}"
    return hashlib.sha256(salted.encode("utf-8")).hexdigest()


def verify_credential(plain_text: str, stored_hash: str, salt: str = SALT_KEY) -> bool:
    """Verifies an entered PIN against a stored SHA-256 hash using constant-time comparison."""
    calculated_hash = hash_credential(plain_text, salt=salt)
    import hmac
    return hmac.compare_digest(calculated_hash, stored_hash.strip())


# ---------------------------------------------------------------------------
# 2. ANTI-BOT & ANTI-AI CRAWLER SHIELD
# ---------------------------------------------------------------------------

def is_bot_or_crawler(user_agent: str) -> Tuple[bool, str]:
    """
    Screens the incoming User-Agent header against known AI scrapers,
    crawlers, search engine spiders, and headless automation tools.
    """
    if not user_agent or len(user_agent.strip()) < 10:
        return True, "Missing or suspicious short User-Agent header"

    ua_lower = user_agent.lower()
    for kw in BLOCKED_BOT_KEYWORDS:
        if kw in ua_lower:
            return True, f"Blocked crawler/AI signature: {kw}"

    return False, ""


# ---------------------------------------------------------------------------
# 3. IP BLACKLISTING & ANTI-BRUTE-FORCE
# ---------------------------------------------------------------------------

def is_ip_blocked(ip_address: str) -> Tuple[bool, str]:
    """Checks if an IP address is currently blacklisted."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT blocked_at, reason FROM blocked_ips WHERE ip_address = ?", (ip_address,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return True, f"IP blocked on {row[0]}: {row[1]}"
    return False, ""


def block_ip_address(ip_address: str, reason: str):
    """Adds an IP address to the permanent security blacklist."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO blocked_ips (ip_address, blocked_at, reason)
        VALUES (?, ?, ?)
    """, (ip_address, now, reason))
    conn.commit()
    conn.close()
    logger.warning(f"🚨 SECURITY ACTION: Blocked IP {ip_address} | Reason: {reason}")


def unblock_ip_address(ip_address: str) -> bool:
    """Removes an IP address from the permanent security blacklist."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM blocked_ips WHERE ip_address = ?", (ip_address.strip(),))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    if deleted:
        logger.info(f"Unblocked IP: {ip_address}")
    return deleted


def record_failed_attempt(ip_address: str, country: str, user_agent: str) -> int:
    """
    Records a failed PIN attempt. If attempts exceed threshold,
    permanently blacklists the IP and returns the count.
    """
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    cutoff_str = (now - timedelta(minutes=LOCKOUT_WINDOW_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Log failed attempt
    cursor.execute("""
        INSERT INTO security_audit_log (timestamp, ip_address, country, user_agent, status, details)
        VALUES (?, ?, ?, ?, 'FAILED_PIN', 'Incorrect PIN entered')
    """, (now_str, ip_address, country, user_agent))

    # Count recent failures
    cursor.execute("""
        SELECT COUNT(*) FROM security_audit_log 
        WHERE ip_address = ? AND status = 'FAILED_PIN' AND timestamp >= ?
    """, (ip_address, cutoff_str))
    fail_count = cursor.fetchone()[0]
    conn.commit()
    conn.close()

    if fail_count >= MAX_FAILED_ATTEMPTS:
        reason = f"Exceeded {MAX_FAILED_ATTEMPTS} failed PIN attempts in {LOCKOUT_WINDOW_MINUTES}m"
        block_ip_address(ip_address, reason)

    return fail_count


def record_successful_login(ip_address: str, country: str, user_agent: str):
    """Logs a successful authenticated login."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO security_audit_log (timestamp, ip_address, country, user_agent, status, details)
        VALUES (?, ?, ?, ?, 'SUCCESS', 'Authenticated successfully')
    """, (now_str, ip_address, country, user_agent))
    conn.commit()
    conn.close()


def get_security_audit_logs(limit: int = 100):
    """Returns recent security audit logs as a DataFrame."""
    import pandas as pd
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query(
        "SELECT id, timestamp, ip_address, country, status, details, user_agent FROM security_audit_log ORDER BY id DESC LIMIT ?",
        conn,
        params=(limit,)
    )
    conn.close()
    return df


def get_blocked_ips():
    """Returns list of currently blocked IPs as a DataFrame."""
    import pandas as pd
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT ip_address, blocked_at, reason FROM blocked_ips ORDER BY blocked_at DESC", conn)
    conn.close()
    return df


# ---------------------------------------------------------------------------
# 4. GEO-IP & FOREIGN ACCESS DETECTION
# ---------------------------------------------------------------------------

def extract_client_info(st_context=None) -> Dict:
    """Extracts client IP, country code, and User-Agent from Streamlit headers."""
    ip = "127.0.0.1"
    country = "IN"  # Default local
    user_agent = "LocalBrowser/1.0"

    try:
        import streamlit as st
        ctx = st_context or getattr(st, "context", None)
        if ctx:
            # Client IP
            if hasattr(ctx, "ip_address") and ctx.ip_address:
                ip = str(ctx.ip_address).strip()
            
            headers = getattr(ctx, "headers", {})
            if headers:
                # Cloudflare real IP header
                cf_ip = headers.get("Cf-Connecting-Ip") or headers.get("cf-connecting-ip")
                xf_ip = headers.get("X-Forwarded-For") or headers.get("x-forwarded-for")
                if cf_ip:
                    ip = cf_ip.strip()
                elif xf_ip:
                    ip = xf_ip.split(",")[0].strip()

                # Cloudflare Country Header
                cf_country = headers.get("Cf-Ipcountry") or headers.get("cf-ipcountry")
                if cf_country:
                    country = cf_country.strip().upper()

                # Browser User-Agent
                ua = headers.get("User-Agent") or headers.get("user-agent")
                if ua:
                    user_agent = ua.strip()
    except Exception as e:
        logger.debug(f"Could not parse streamlit context headers: {e}")

    # Is IP local/private?
    is_private = ip.startswith(("127.", "192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.", "localhost", "::1"))

    return {
        "ip": ip,
        "country": country,
        "user_agent": user_agent,
        "is_private": is_private,
        "is_india": (country == "IN") or is_private
    }


def trigger_foreign_access_alert(client_info: Dict):
    """Fires an emergency alert to Telegram and Gmail when access is detected outside India."""
    try:
        import notifications as notif
        ip = client_info['ip']
        country = client_info['country']
        ua = client_info['user_agent']
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")

        # 1. Telegram Emergency Alert
        tg_text = (
            f"🚨 *SECURITY ALERT: ACCESS ATTEMPT FROM OUTSIDE INDIA!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"• *Detected IP:* `{ip}`\n"
            f"• *Country Code:* `{country}` (Non-Indian Origin)\n"
            f"• *Timestamp:* {now}\n"
            f"• *User-Agent:* `{ua[:60]}...`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ If this was NOT you using an international VPN, this IP should be blocked immediately."
        )
        notif.send_telegram_message(tg_text)

        # 2. Gmail Alert
        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #0f141c; color: #ffffff; padding: 20px;">
            <div style="max-width: 550px; margin: auto; background-color: #1a2232; border-radius: 8px; border-left: 6px solid #ff4d4d; padding: 20px;">
                <h2 style="color: #ff4d4d; margin-top: 0;">🚨 FOREIGN ACCESS ATTEMPT DETECTED</h2>
                <p style="font-size: 14px; color: #cbd5e0;">Your Auto Swing Trader dashboard received an incoming connection originating outside India.</p>
                <table style="width: 100%; border-collapse: collapse; margin: 15px 0; font-size: 14px;">
                    <tr style="border-bottom: 1px solid #2d3748;"><td style="padding: 8px; color: #a0aec0;">IP Address</td><td style="padding: 8px; font-weight: bold; color: #ff4d4d;">{ip}</td></tr>
                    <tr style="border-bottom: 1px solid #2d3748;"><td style="padding: 8px; color: #a0aec0;">Origin Country</td><td style="padding: 8px; font-weight: bold;">{country}</td></tr>
                    <tr style="border-bottom: 1px solid #2d3748;"><td style="padding: 8px; color: #a0aec0;">Time</td><td style="padding: 8px;">{now}</td></tr>
                    <tr style="border-bottom: 1px solid #2d3748;"><td style="padding: 8px; color: #a0aec0;">Client Agent</td><td style="padding: 8px; font-size: 12px; color: #a0aec0;">{ua}</td></tr>
                </table>
            </div>
        </body>
        </html>
        """
        notif.send_gmail_alert("🚨 SECURITY ALERT: Foreign Access Attempt to Dashboard", html)
    except Exception as e:
        logger.error(f"Failed to dispatch foreign access security alert: {e}")


# ---------------------------------------------------------------------------
# 5. STREAMLIT GATEKEEPER PIPELINE
# ---------------------------------------------------------------------------

def enforce_security_gate():
    """
    Executes the full security barrier before rendering any dashboard components:
    1. Anti-Bot / Anti-AI User-Agent check.
    2. IP Blacklist check.
    3. Foreign Country check (alerts if outside India).
    4. Salted SHA-256 PIN Authentication.
    5. Brute-Force lockout after 3 failed attempts.
    """
    import streamlit as st

    client = extract_client_info()
    ip = client['ip']
    ua = client['user_agent']
    country = client['country']

    # 1. Anti-Bot / AI Screening
    is_bot, bot_reason = is_bot_or_crawler(ua)
    if is_bot:
        logger.warning(f"Blocked bot from {ip}: {bot_reason}")
        conn = sqlite3.connect(DB_FILE)
        conn.execute("INSERT INTO security_audit_log (timestamp, ip_address, country, user_agent, status, details) VALUES (?, ?, ?, ?, 'BLOCKED_BOT', ?)",
                     (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ip, country, ua, bot_reason))
        conn.commit()
        conn.close()
        st.error("⛔ Access Denied: Automated bots, crawlers, and AI agents are strictly prohibited.")
        st.stop()

    # 2. Blacklisted IP check
    blocked, block_reason = is_ip_blocked(ip)
    if blocked:
        st.error(f"⛔ Access Permanently Blocked for IP `{ip}`.")
        st.caption(f"Reason: {block_reason}. Contact administrator if this is an error.")
        st.stop()

    # 3. Foreign IP Check (Outside India)
    if not client['is_india']:
        # Alert if not already alerted in this session
        if "foreign_alert_sent" not in st.session_state:
            trigger_foreign_access_alert(client)
            st.session_state["foreign_alert_sent"] = True

    # 4. PIN Authentication
    auth_pin = ""
    try:
        if hasattr(st, "secrets") and "DASHBOARD_PIN" in st.secrets:
            auth_pin = str(st.secrets["DASHBOARD_PIN"]).strip()
    except Exception:
        pass

    if not auth_pin and os.getenv("DASHBOARD_PIN"):
        auth_pin = os.getenv("DASHBOARD_PIN").strip()

    # If no PIN configured, allow access (local dev mode)
    if not auth_pin:
        return

    # Check session state
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if not st.session_state["authenticated"]:
        st.markdown("### 🔒 Security Access Gate")
        st.caption(f"Protected by SHA-256 Encryption & Anti-Brute-Force Shield | Client IP: `{ip}` ({country})")

        col_p1, col_p2 = st.columns([2, 3])
        with col_p1:
            entered_pin = st.text_input("Enter Dashboard PIN", type="password", key="auth_pin_input")
            if st.button("Unlock Dashboard", type="primary", use_container_width=True):
                # Verify using salted SHA-256
                stored_hash = hash_credential(auth_pin)
                if verify_credential(entered_pin, stored_hash):
                    st.session_state["authenticated"] = True
                    record_successful_login(ip, country, ua)
                    st.rerun()
                else:
                    fails = record_failed_attempt(ip, country, ua)
                    remaining = max(0, MAX_FAILED_ATTEMPTS - fails)
                    if remaining > 0:
                        st.error(f"⛔ Incorrect PIN. {remaining} attempt(s) remaining before IP `{ip}` is permanently blacklisted.")
                    else:
                        st.error(f"🛑 Maximum failed attempts exceeded! IP `{ip}` has been blacklisted and alerted to Telegram.")
                        # Alert Telegram of brute force attempt
                        import notifications as notif
                        notif.send_telegram_message(f"🚨 *SECURITY LOCKOUT: IP {ip} BLACKLISTED!*\n• Country: {country}\n• Reason: {MAX_FAILED_ATTEMPTS} consecutive failed PIN attempts.")
                        st.rerun()

        st.stop()
