# Changelog

All notable changes to the **Sentinel-Swing Advisory Platform** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-11-16 (Production Baseline)

### 🚀 Core Platform & Cloud Infrastructure
- **Streamlit Community Cloud Deployment:** Web dashboard deployed with HTTPS encryption and mobile responsiveness.
- **Enterprise Security Gate:** Salted SHA-256 PIN gate (`80115727`), anti-bot scraper shield, and brute-force lockout.
- **Lightweight Seed DB Architecture:** `seed_market_data.db` (19.02 MB) pre-loaded with the last 250 4H bars for all 499 stocks up to Friday, Nov 13, 2026 close, enabling 0.3s cloud boot and tiny delta-only morning updates.

### 📱 Communication & Automation
- **24/7 Telegram Listener Daemon:** Non-blocking background thread in Streamlit Cloud responding to `/status`, `/capital`, `/bought`, `/sold`, and `/help` from phone.
- **Dual-Channel Outbound Dispatch:** Instant real-time alerts delivered to Telegram (`@AdvaitSentinel_bot`) and Gmail HTML inbox (`ketkaradvait@gmail.com`).
- **4-Checkpoint Market Routine:**
  - `09:45 AM IST`: Morning Volatility Bypass & Gap Verification.
  - `01:25 PM IST`: 4-Hour Bar #1 Completion & Trailing Stop Ratchet.
  - `03:20 PM IST`: Pre-Market Close Assessment (10-minute broker execution window).
  - `05:00 PM IST`: Post-Market Close Bhavcopy Audit, Watchlist, & Daily Summary dispatch.

### 💼 Portfolio Management & Risk Controls
- **Active Trade Tracking:** Initial portfolio initialized with ₹94,784.73 equity across 3 active positions (`IRB.NS`, `HAVELLS.NS`, `ICICIAMC.NS`).
- **Custom Partial Profit Scaling:** Multi-staged profit harvesting by custom percentage (10%, 20%, 25%, 33%, etc.) or exact share count with proportional slot de-allocation and instant cash credit.
- **Multi-Option Advisory Menu:** Dispatches all qualifying setups with Alpha ≥ 110 (up to top 5) for user choice while enforcing max 3 active trades.
- **Falling-Knife Protection:** Hard 200 EMA floor, Chandelier Supertrend Buy Zone alignment, institutional volume surge validation, and Nifty 50 macro regime filter.
